"""``/api/v1/ar-captures`` — ARCore capture upload + synchronous recognition.

The ARCore client uploads a top-down stud frame (RGB + 16-bit depth +
camera intrinsics in ``ar_metadata``) plus a few angle photos. This
endpoint runs :func:`services.brick_recognizer.recognize_brick`
*synchronously* (fast: PIL decode + blob detection + pinhole math) and:

* **recognized** (system classified within tolerance & confidence): persists
  a ``Capture(mode="ar_recognized")`` with system/kind/units, dispatches the
  existing ``reconstruct`` task (which builds the canonical GLB via the
  ``ar_recognized`` worker branch), and returns ``status="recognized"``.
* **needs_measurement** (low confidence, or ``system_hint="unknown"``):
  persists the capture for audit, does NOT dispatch a job, and returns the
  5 caliper fields + guidance so the client falls back to
  ``POST /parametric-blocks``.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, UploadFile, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.deps import DBSessionDep
from api.v1.upload_limits import (
    assert_total_upload_bytes,
    cleanup_stored_uploads,
    extension_for_allowed_upload,
    read_upload_bytes,
    validate_depth_png_bytes,
    validate_image_bytes,
)
from core.errors import CaptureInvalid
from db.models import Capture, Job
from models.schemas import ArCaptureRead, NeedsMeasurement, RecognizedBlock
from services.brick_recognizer import recognize_brick
from services.reconstruct_dispatcher import commit_and_dispatch_reconstruct
from storage.minio_client import raw_object_key, storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ar-captures", tags=["ar-captures"])

MIN_IMAGES = 4
MAX_IMAGES = 20
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

Kind = Literal["brick", "plate", "tile", "slope"]
SystemHint = Literal["lego", "duplo", "feile", "generic", "unknown"]

#: The 5 caliper fields the fallback collects (identical to /parametric-blocks).
_MEASUREMENT_FIELDS = [
    "outer_pitch_mm",
    "inner_pitch_mm",
    "stud_diameter_mm",
    "brick_height_net_mm",
    "brick_height_total_mm",
]
_GUIDANCE = (
    "无法自动识别。请用游标卡尺测 5 个值并提交到 /api/v1/parametric-blocks："  # noqa: RUF001
    "1A=卡两端最外侧凸点外缘的总跨距 (outer_pitch_mm)；"  # noqa: RUF001
    "1B=相邻两凸点之间最窄缝隙 (inner_pitch_mm)；"  # noqa: RUF001
    "③=任一凸点外径 (stud_diameter_mm)；"  # noqa: RUF001
    "②=底面到砖体顶面(不含凸点)的净高 (brick_height_net_mm)；"  # noqa: RUF001
    "④=底面到凸点顶的总高 (brick_height_total_mm)。卡'可夹的实边'，不要找凸点中心。"  # noqa: RUF001
)


def _settings_s3_bucket_raw() -> str:
    """Local helper to keep ``storage.put_object`` lines tidy."""
    from app.config import settings as _s

    return _s.s3_bucket_raw


@router.post(
    "",
    response_model=ArCaptureRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an ARCore capture (top-down stud frame + depth + photos) and recognize it",
    responses={
        201: {"description": "Capture created; recognized → GLB job dispatched, else needs_measurement."},
        422: {"description": "Validation failed (bad image count, bad ar_metadata JSON, empty frame)."},
    },
)
async def create_ar_capture(
    session: DBSessionDep,
    kind: Annotated[Kind, Form()],
    recognition_rgb: Annotated[UploadFile, File(description="Top-down RGB of the stud face.")],
    recognition_depth: Annotated[UploadFile, File(description="DEPTH16 of the same frame as a 16-bit PNG.")],
    ar_metadata: Annotated[str, Form(description="JSON: intrinsics, depth dims, pose, distance, coarse hints.")],
    images: Annotated[
        list[UploadFile],
        File(description=f"{MIN_IMAGES}-{MAX_IMAGES} angle photos (compat with photo path / future)."),
    ] = [],  # noqa: B006 — FastAPI's Annotated/File idiom for a file list
    system_hint: Annotated[SystemHint | None, Form()] = None,
    part_id: Annotated[str | None, Form(max_length=64)] = None,
) -> JSONResponse:
    imgs = images or []
    angle_uploads: list[tuple[str, bytes, str]] = []
    try:
        # ---- 1. Validate image count ----------------------------------------
        if len(imgs) < MIN_IMAGES or len(imgs) > MAX_IMAGES:
            raise CaptureInvalid(
                f"need {MIN_IMAGES} <= image_count <= {MAX_IMAGES}, got {len(imgs)}",
                details={"image_count": len(imgs), "min": MIN_IMAGES, "max": MAX_IMAGES},
            )

        # ---- 2. Parse ar_metadata -------------------------------------------
        try:
            meta = json.loads(ar_metadata)
        except json.JSONDecodeError as exc:
            raise CaptureInvalid(
                f"ar_metadata is not valid JSON: {exc.msg} (line {exc.lineno})",
                details={"line": exc.lineno, "column": exc.colno},
            ) from exc
        if not isinstance(meta, dict):
            raise CaptureInvalid("ar_metadata must be a JSON object", details={"got_type": type(meta).__name__})

        # ---- 3. Read recognition frame + angle bytes ------------------------
        rgb_ext = extension_for_allowed_upload(
            recognition_rgb,
            label="recognition_rgb",
            allowed_content_types={"image/png": "png"},
        )
        rgb_bytes = await read_upload_bytes(recognition_rgb, label="recognition_rgb")
        validate_image_bytes(rgb_bytes, label="recognition_rgb", content_type=recognition_rgb.content_type)
        depth_bytes = await read_upload_bytes(recognition_depth, label="recognition_depth")
        validate_depth_png_bytes(depth_bytes, label="recognition_depth", content_type=recognition_depth.content_type)
        total_bytes = len(rgb_bytes) + len(depth_bytes)
        assert_total_upload_bytes(total_bytes)

        for idx, upload in enumerate(imgs):
            ext = extension_for_allowed_upload(
                upload,
                label=f"image #{idx}",
                allowed_content_types=ALLOWED_CONTENT_TYPES,
            )
            body = await read_upload_bytes(upload, label=f"image #{idx}")
            validate_image_bytes(body, label=f"image #{idx}", content_type=upload.content_type)
            total_bytes += len(body)
            assert_total_upload_bytes(total_bytes)
            key = f"{idx:03d}.{ext}"
            angle_uploads.append((key, body, upload.content_type or "application/octet-stream"))
    finally:
        await recognition_rgb.close()
        await recognition_depth.close()
        for upload in imgs:
            await upload.close()

    # ---- 4. Recognize (synchronous, fast) -------------------------------
    result = recognize_brick(
        rgb_bytes=rgb_bytes,
        depth_bytes=depth_bytes,
        ar_metadata=meta,
        kind=kind,
        system_hint=system_hint,
        pitch_tolerance_mm=settings.ar_pitch_tolerance_mm,
        min_confidence=settings.ar_min_confidence,
    )
    recognized = result.ok and system_hint != "unknown"

    # ---- 5. part_id default ---------------------------------------------
    capture_id = uuid.uuid4()
    if not part_id:
        part_id = (
            f"{result.system}-{kind}-{result.units_x}x{result.units_y}"
            if recognized
            else f"ar-{kind}-{str(capture_id)[:8]}"
        )

    # ---- 6. Upload recognition frame + angle photos to MinIO ------------
    keys: list[str] = []
    raw_bucket = _settings_s3_bucket_raw()
    durable_state_committed = False
    try:
        rgb_key = raw_object_key(str(capture_id), f"recognition_rgb.{rgb_ext}")
        storage.put_object(raw_bucket, rgb_key, rgb_bytes, content_type="image/png")
        keys.append(rgb_key)
        depth_key = raw_object_key(str(capture_id), "recognition_depth.png")
        storage.put_object(raw_bucket, depth_key, depth_bytes, content_type="image/png")
        keys.append(depth_key)
        angle_keys: list[str] = []
        for filename, body, content_type in angle_uploads:
            key = raw_object_key(str(capture_id), filename)
            storage.put_object(raw_bucket, key, body, content_type=content_type)
            keys.append(key)
            angle_keys.append(key)

        # ---- 7. Persist the capture row -------------------------------------
        capture = Capture(
            id=capture_id,
            part_id=part_id,
            status="pending",
            image_count=len(angle_keys),
            image_keys=angle_keys,
            capture_mode="phone_walkaround",
            mode="ar_recognized",
            system=result.system if recognized else None,
            kind=kind,
            units_x=result.units_x if recognized else None,
            units_y=result.units_y if recognized else None,
            ar_metadata=meta,
            recognition_result=result.to_dict(),
        )
        session.add(capture)

        # ---- 8. Dispatch a GLB job only when recognized ---------------------
        job_id: uuid.UUID | None = None
        if recognized:
            job = Job(capture_id=capture_id, kind="reconstruct", status="pending", progress=0, stage="queued")
            session.add(job)
            durable_state_committed = True
            job_id = await commit_and_dispatch_reconstruct(
                session,
                capture_id=capture_id,
                job=job,
            )

        await session.commit()
        durable_state_committed = True
        await session.refresh(capture)
    except Exception:
        if not durable_state_committed:
            cleanup_stored_uploads(raw_bucket, keys, storage.remove_object)
        raise

    logger.info(
        "ar_captures.create: capture_id=%s part_id=%s recognized=%s system=%s units=%sx%s "
        "pitch=%s conf=%s job_id=%s",
        capture_id, part_id, recognized, result.system, result.units_x, result.units_y,
        result.pitch_mm, result.confidence, job_id,
    )

    body = ArCaptureRead(
        capture_id=capture.id,
        part_id=capture.part_id,
        status="recognized" if recognized else "needs_measurement",
        recognized=RecognizedBlock(
            system=result.system,
            kind=kind,
            units_x=result.units_x,
            units_y=result.units_y,
            pitch_mm=result.pitch_mm,
            confidence=result.confidence,
        ),
        needs_measurement=None
        if recognized
        else NeedsMeasurement(fields=_MEASUREMENT_FIELDS, guidance=_GUIDANCE, reason=result.reason),
        job_id=job_id,
        warnings=result.warnings,
        created_at=capture.created_at,
    )
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=body.model_dump(mode="json"))


__all__ = ["router"]
