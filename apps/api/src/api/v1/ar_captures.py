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
    "image/heic": "heic",
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
    # ---- 1. Validate image count ----------------------------------------
    imgs = images or []
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

    # ---- 3. Read recognition frame bytes --------------------------------
    rgb_bytes = await recognition_rgb.read()
    depth_bytes = await recognition_depth.read()
    await recognition_rgb.close()
    await recognition_depth.close()
    if not rgb_bytes:
        raise CaptureInvalid("recognition_rgb is empty")
    if not depth_bytes:
        raise CaptureInvalid("recognition_depth is empty")

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
    rgb_key = raw_object_key(str(capture_id), "recognition_rgb.png")
    storage.put_object(_settings_s3_bucket_raw(), rgb_key, rgb_bytes, content_type=recognition_rgb.content_type or "image/png")
    depth_key = raw_object_key(str(capture_id), "recognition_depth.png")
    storage.put_object(_settings_s3_bucket_raw(), depth_key, depth_bytes, content_type="image/png")
    keys: list[str] = []
    for idx, upload in enumerate(imgs):
        ext = ALLOWED_CONTENT_TYPES.get((upload.content_type or "").lower(), "bin")
        body = await upload.read()
        if not body:
            raise CaptureInvalid(f"image #{idx} is empty", details={"index": idx})
        key = raw_object_key(str(capture_id), f"{idx:03d}.{ext}")
        storage.put_object(_settings_s3_bucket_raw(), key, body, content_type=upload.content_type or "application/octet-stream")
        keys.append(key)
        await upload.close()

    # ---- 7. Persist the capture row -------------------------------------
    capture = Capture(
        id=capture_id,
        part_id=part_id,
        status="pending",
        image_count=len(keys),
        image_keys=keys,
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
        job_id = await commit_and_dispatch_reconstruct(
            session,
            capture_id=capture_id,
            job=job,
        )

    await session.commit()
    await session.refresh(capture)

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
