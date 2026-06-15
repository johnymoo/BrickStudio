"""Bounded upload helpers for capture routes."""
from __future__ import annotations

import io

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app.config import settings
from core.errors import CaptureInvalid


async def read_upload_bytes(
    upload: UploadFile,
    *,
    label: str,
    max_bytes: int | None = None,
) -> bytes:
    """Read an upload with a hard byte limit and reject empty files."""

    limit = max_bytes or settings.max_upload_file_bytes
    body = await upload.read(limit + 1)
    if len(body) > limit:
        raise CaptureInvalid(
            f"{label} is too large",
            details={"max_bytes": limit, "filename": upload.filename},
        )
    if not body:
        raise CaptureInvalid(
            f"{label} is empty",
            details={"filename": upload.filename},
        )
    return body


def extension_for_allowed_upload(
    upload: UploadFile,
    *,
    label: str,
    allowed_content_types: dict[str, str],
) -> str:
    content_type = (upload.content_type or "").lower()
    if content_type not in allowed_content_types:
        raise CaptureInvalid(
            f"{label} has unsupported content type",
            details={
                "content_type": upload.content_type,
                "filename": upload.filename,
                "allowed_content_types": sorted(allowed_content_types),
            },
        )
    return allowed_content_types[content_type]


def validate_image_bytes(
    body: bytes,
    *,
    label: str,
    content_type: str | None,
) -> None:
    """Validate that uploaded bytes are an image and not a pixel bomb."""

    try:
        with Image.open(io.BytesIO(body)) as image:
            width, height = image.size
            pixels = width * height
            if pixels > settings.max_image_pixels:
                raise CaptureInvalid(
                    f"{label} has too many pixels",
                    details={
                        "max_pixels": settings.max_image_pixels,
                        "actual_pixels": pixels,
                        "width": width,
                        "height": height,
                    },
                )
            image.verify()
    except CaptureInvalid:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise CaptureInvalid(
            f"{label} is an invalid image",
            details={"content_type": content_type},
        ) from exc


def assert_total_upload_bytes(total: int) -> None:
    if total > settings.max_upload_total_bytes:
        raise CaptureInvalid(
            "uploaded files exceed request size limit",
            details={"max_total_bytes": settings.max_upload_total_bytes, "actual_bytes": total},
        )


__all__ = [
    "assert_total_upload_bytes",
    "extension_for_allowed_upload",
    "read_upload_bytes",
    "validate_image_bytes",
]
