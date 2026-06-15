"""``/api/v1/captures`` — multipart upload + read."""
from __future__ import annotations

import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from sqlalchemy import func, select

from app.config import settings
from app.deps import DBSessionDep
from api.v1.upload_limits import assert_total_upload_bytes, read_upload_bytes, validate_image_bytes
from core.errors import CaptureInvalid, CaptureNotFound
from db.models import Capture, Job
from models.schemas import CaptureImagesRead, CaptureImageRead, CaptureRead, NeedsMeasurement
from services.reconstruct_dispatcher import commit_and_dispatch_reconstruct
from storage.minio_client import raw_object_key, storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/captures", tags=["captures"])

# Phase-2 contract (design-phase2.md §3.1): images between 4 and 20.
MIN_IMAGES = 4
MAX_IMAGES = 20
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
}

#: Capture mode values. Stored verbatim in the capture row (phase 2 added
#: a ``capture_mode`` column; default = ``phone_walkaround`` per the
#: design-phase2.md §3.1 contract).
CaptureMode = Literal["phone_walkaround", "studio_turntable", "quick_snapshot"]
DEFAULT_CAPTURE_MODE: CaptureMode = "phone_walkaround"

_MEASUREMENT_FIELDS = [
    "outer_pitch_mm",
    "inner_pitch_mm",
    "stud_diameter_mm",
    "brick_height_net_mm",
    "brick_height_total_mm",
]
_GUIDANCE = (
    "无法自动识别。请用游标卡尺测 5 个值并提交到 /api/v1/parametric-blocks："
    "outer_pitch_mm、inner_pitch_mm、stud_diameter_mm、"
    "brick_height_net_mm、brick_height_total_mm。"
)


@router.post(
    "",
    response_model=CaptureRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload photos of a single physical part",
)
async def create_capture(
    session: DBSessionDep,
    part_id: Annotated[str, Form(min_length=1, max_length=64)],
    images: Annotated[list[UploadFile], File(
        description=(
            f"Between {MIN_IMAGES} and {MAX_IMAGES} photos of the part. "
            "8+ photos unlocks the COLMAP SfM path; 4-7 uses the "
            "Open3D icosahedron fallback."
        )
    )],
    capture_mode: Annotated[CaptureMode, Form()] = DEFAULT_CAPTURE_MODE,
) -> CaptureRead:
    n = len(images)
    if n < MIN_IMAGES or n > MAX_IMAGES:
        raise CaptureInvalid(
            f"need {MIN_IMAGES} <= image_count <= {MAX_IMAGES}, got {n}",
            details={
                "image_count": n,
                "min": MIN_IMAGES,
                "max": MAX_IMAGES,
            },
        )

    capture_id = uuid.uuid4()
    keys: list[str] = []
    staged_uploads: list[tuple[str, bytes, str]] = []
    try:
        total_bytes = 0
        for idx, upload in enumerate(images):
            ext = ALLOWED_CONTENT_TYPES.get((upload.content_type or "").lower(), "bin")
            filename = f"{idx:03d}.{ext}"
            key = raw_object_key(str(capture_id), filename)
            body = await read_upload_bytes(upload, label=f"image #{idx}")
            validate_image_bytes(body, label=f"image #{idx}", content_type=upload.content_type)
            total_bytes += len(body)
            assert_total_upload_bytes(total_bytes)
            staged_uploads.append((key, body, upload.content_type or "application/octet-stream"))
    finally:
        for upload in images:
            await upload.close()

    for key, body, content_type in staged_uploads:
        storage.put_object(
            settings_s3_bucket_raw(),
            key,
            body,
            content_type=content_type,
        )
        keys.append(key)

    # Persist capture row.
    capture = Capture(
        id=capture_id,
        part_id=part_id,
        status="pending",
        image_count=len(keys),
        image_keys=keys,
        capture_mode=capture_mode,
    )
    session.add(capture)

    # Pre-create the job row so GET /jobs/{id} works before the worker boots.
    job = Job(
        capture_id=capture_id,
        kind="reconstruct",
        status="pending",
        progress=0,
        stage="queued",
    )
    session.add(job)

    job_id = await commit_and_dispatch_reconstruct(
        session,
        capture_id=capture_id,
        job=job,
    )
    await session.refresh(capture)

    logger.info(
        "captures.create: capture_id=%s part_id=%s images=%d mode=%s job_id=%s",
        capture_id, part_id, len(keys), capture_mode, job_id,
    )

    return CaptureRead(
        capture_id=capture.id,
        part_id=capture.part_id,
        status=capture.status,
        image_count=capture.image_count,
        created_at=capture.created_at,
        updated_at=capture.updated_at,
        job_id=job_id,
        image_keys=capture.image_keys,
        capture_mode=capture_mode,
    )


@router.get("", response_model=list[CaptureRead], summary="List recent captures")
async def list_captures(
    session: DBSessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[CaptureRead]:
    stmt = select(Capture).order_by(Capture.created_at.desc()).limit(limit)
    captures = list((await session.scalars(stmt)).all())
    job_ids = await _latest_job_ids(session, [capture.id for capture in captures])
    return [_capture_read(capture, job_ids.get(capture.id)) for capture in captures]


@router.get("/{capture_id}", response_model=CaptureRead, summary="Read a capture")
async def get_capture(capture_id: uuid.UUID, session: DBSessionDep) -> CaptureRead:
    capture = await session.get(Capture, capture_id)
    if capture is None:
        raise CaptureNotFound(f"capture {capture_id} not found")
    job_id = await session.scalar(
        select(Job.id)
        .where(Job.capture_id == capture_id)
        .order_by(Job.created_at.desc(), Job.id.desc())
    )
    return _capture_read(capture, job_id)


@router.get("/{capture_id}/images", response_model=CaptureImagesRead, summary="Read raw capture image URLs")
async def get_capture_images(capture_id: uuid.UUID, session: DBSessionDep) -> CaptureImagesRead:
    capture = await session.get(Capture, capture_id)
    if capture is None:
        raise CaptureNotFound(f"capture {capture_id} not found")

    images: list[CaptureImageRead] = []
    for key in capture.image_keys or []:
        presigned = storage.presigned_get(settings.s3_bucket_raw, key)
        if isinstance(presigned, tuple):
            url, expires_at = presigned
        else:
            url, expires_at = presigned, None
        images.append(CaptureImageRead(key=key, url=url, expires_at=expires_at))
    return CaptureImagesRead(capture_id=capture.id, images=images)


async def _latest_job_ids(session: DBSessionDep, capture_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    if not capture_ids:
        return {}
    ranked_jobs = (
        select(
            Job.capture_id,
            Job.id,
            func.row_number()
            .over(
                partition_by=Job.capture_id,
                order_by=(Job.created_at.desc(), Job.id.desc()),
            )
            .label("job_rank"),
        )
        .where(Job.capture_id.in_(capture_ids))
        .subquery()
    )
    rows = (
        await session.execute(
            select(ranked_jobs.c.capture_id, ranked_jobs.c.id).where(ranked_jobs.c.job_rank == 1)
        )
    ).all()
    return {capture_id: job_id for capture_id, job_id in rows}


def _capture_read(capture: Capture, job_id: uuid.UUID | None) -> CaptureRead:
    return CaptureRead(
        capture_id=capture.id,
        part_id=capture.part_id,
        status=_capture_status(capture, job_id),
        image_count=capture.image_count,
        created_at=capture.created_at,
        updated_at=capture.updated_at,
        job_id=job_id,
        image_keys=capture.image_keys or [],
        capture_mode=getattr(capture, "capture_mode", DEFAULT_CAPTURE_MODE),
        mode=getattr(capture, "mode", "photo"),
        system=capture.system,
        kind=capture.kind,
        units_x=capture.units_x,
        units_y=capture.units_y,
        recognition_result=capture.recognition_result,
        needs_measurement=_needs_measurement(capture, job_id),
    )


def _capture_status(capture: Capture, job_id: uuid.UUID | None) -> str:
    if _needs_measurement(capture, job_id) is not None:
        return "needs_measurement"
    return capture.status


def _needs_measurement(capture: Capture, job_id: uuid.UUID | None) -> NeedsMeasurement | None:
    recognition = capture.recognition_result or {}
    if capture.mode != "ar_recognized" or job_id is not None:
        return None
    if recognition.get("ok") is True and not recognition.get("reason"):
        return None
    return NeedsMeasurement(
        fields=_MEASUREMENT_FIELDS,
        guidance=_GUIDANCE,
        reason=recognition.get("reason"),
    )


def settings_s3_bucket_raw() -> str:  # tiny local helper to keep `storage.put_object` lines tidy
    from app.config import settings

    return settings.s3_bucket_raw
