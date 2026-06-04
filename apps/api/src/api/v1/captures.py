"""``/api/v1/captures`` — multipart upload + read."""
from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status
from sqlalchemy import select

from app.deps import DBSessionDep
from core.errors import CaptureInvalid, CaptureNotFound
from db.models import Capture, Job
from models.schemas import CaptureRead
from storage.minio_client import raw_object_key, storage
from workers.tasks.reconstruct import reconstruct as reconstruct_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/captures", tags=["captures"])

MIN_IMAGES = 4
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
}


@router.post(
    "",
    response_model=CaptureRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload photos of a single physical part",
)
async def create_capture(
    session: DBSessionDep,
    part_id: Annotated[str, Form(min_length=1, max_length=64)],
    images: Annotated[list[UploadFile], File(description="At least 4 photos of the part.")],
) -> CaptureRead:
    if len(images) < MIN_IMAGES:
        raise CaptureInvalid(
            f"need at least {MIN_IMAGES} images, got {len(images)}",
            details={"image_count": len(images), "min": MIN_IMAGES},
        )

    capture_id = uuid.uuid4()
    keys: list[str] = []
    for idx, upload in enumerate(images):
        ext = ALLOWED_CONTENT_TYPES.get((upload.content_type or "").lower(), "bin")
        filename = f"{idx:03d}.{ext}"
        key = raw_object_key(str(capture_id), filename)
        body = await upload.read()
        if not body:
            raise CaptureInvalid(
                f"image #{idx} is empty", details={"index": idx, "filename": upload.filename}
            )
        storage.put_object(
            settings_s3_bucket_raw(),
            key,
            body,
            content_type=upload.content_type or "application/octet-stream",
        )
        keys.append(key)
        await upload.close()

    # Persist capture row.
    capture = Capture(
        id=capture_id,
        part_id=part_id,
        status="pending",
        image_count=len(keys),
        image_keys=keys,
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
    await session.flush()  # populate job.id

    # Dispatch — use the pre-created job's id as the task id hint.
    async_result = reconstruct_task.apply_async(
        args=[str(capture_id)],
        task_id=str(job.id),
    )
    job.celery_task_id = async_result.id

    await session.commit()
    await session.refresh(capture)

    logger.info(
        "captures.create: capture_id=%s part_id=%s images=%d job_id=%s",
        capture_id, part_id, len(keys), job.id,
    )

    return CaptureRead(
        capture_id=capture.id,
        part_id=capture.part_id,
        status=capture.status,
        image_count=capture.image_count,
        created_at=capture.created_at,
        job_id=job.id,
        image_keys=capture.image_keys,
    )


@router.get("/{capture_id}", response_model=CaptureRead, summary="Read a capture")
async def get_capture(capture_id: uuid.UUID, session: DBSessionDep) -> CaptureRead:
    capture = await session.get(Capture, capture_id)
    if capture is None:
        raise CaptureNotFound(f"capture {capture_id} not found")
    job_id = await session.scalar(select(Job.id).where(Job.capture_id == capture_id).order_by(Job.created_at.desc()))
    return CaptureRead(
        capture_id=capture.id,
        part_id=capture.part_id,
        status=capture.status,
        image_count=capture.image_count,
        created_at=capture.created_at,
        job_id=job_id,
        image_keys=capture.image_keys,
    )


def settings_s3_bucket_raw() -> str:  # tiny local helper to keep `storage.put_object` lines tidy
    from app.config import settings

    return settings.s3_bucket_raw
