"""Tests for bounded upload reads and image validation."""
from __future__ import annotations

import io
import struct
import zlib

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from core.errors import CaptureInvalid


def _png(width: int = 4, height: int = 4, color: bytes = b"\xff\x80\x00") -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + color * width for _ in range(height))
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _upload(name: str, body: bytes, content_type: str = "image/png") -> UploadFile:
    return UploadFile(
        filename=name,
        file=io.BytesIO(body),
        headers=Headers({"content-type": content_type}),
    )


async def test_read_upload_bytes_rejects_files_over_limit() -> None:
    from api.v1.upload_limits import read_upload_bytes

    upload = _upload("large.png", b"x" * 11)

    with pytest.raises(CaptureInvalid) as exc:
        await read_upload_bytes(upload, label="image #0", max_bytes=10)

    assert "too large" in str(exc.value)


async def test_read_upload_bytes_rejects_empty_files() -> None:
    from api.v1.upload_limits import read_upload_bytes

    upload = _upload("empty.png", b"")

    with pytest.raises(CaptureInvalid) as exc:
        await read_upload_bytes(upload, label="image #0", max_bytes=10)

    assert "empty" in str(exc.value)


async def test_validate_image_bytes_rejects_non_image() -> None:
    from api.v1.upload_limits import validate_image_bytes

    with pytest.raises(CaptureInvalid) as exc:
        validate_image_bytes(b"not an image", label="recognition_rgb", content_type="image/png")

    assert "invalid image" in str(exc.value)


async def test_validate_image_bytes_accepts_small_png() -> None:
    from api.v1.upload_limits import validate_image_bytes

    validate_image_bytes(_png(), label="recognition_rgb", content_type="image/png")


async def test_validate_image_bytes_rejects_over_pixel_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1 import upload_limits

    monkeypatch.setattr(
        upload_limits,
        "settings",
        upload_limits.settings.model_copy(update={"max_image_pixels": 15}),
    )

    with pytest.raises(CaptureInvalid) as exc:
        upload_limits.validate_image_bytes(_png(width=4, height=4), label="recognition_rgb", content_type="image/png")

    assert "too many pixels" in str(exc.value)


async def test_extension_for_allowed_upload_rejects_unsupported_content_type() -> None:
    from api.v1.upload_limits import extension_for_allowed_upload

    upload = _upload("vector.svg", _png(), content_type="image/svg+xml")

    with pytest.raises(CaptureInvalid) as exc:
        extension_for_allowed_upload(upload, label="image #0", allowed_content_types={"image/png": "png"})

    assert "unsupported content type" in str(exc.value)


async def test_extension_for_allowed_upload_rejects_heic_without_decoder_support() -> None:
    from api.v1.captures import ALLOWED_CONTENT_TYPES
    from api.v1.upload_limits import extension_for_allowed_upload

    upload = _upload("photo.heic", _png(), content_type="image/heic")

    with pytest.raises(CaptureInvalid):
        extension_for_allowed_upload(upload, label="image #0", allowed_content_types=ALLOWED_CONTENT_TYPES)


async def test_capture_route_closes_uploads_on_image_count_validation_error() -> None:
    from api.v1.captures import create_capture

    uploads = [_upload(f"{idx}.png", _png()) for idx in range(3)]

    with pytest.raises(CaptureInvalid):
        await create_capture(
            session=None,  # type: ignore[arg-type]
            part_id="too-few-images",
            images=uploads,
        )

    assert all(upload.file.closed for upload in uploads)


async def test_ar_capture_route_closes_uploads_on_image_count_validation_error() -> None:
    from api.v1.ar_captures import create_ar_capture

    recognition_rgb = _upload("rgb.png", _png())
    recognition_depth = _upload("depth.png", _png())
    uploads = [_upload(f"{idx}.png", _png()) for idx in range(3)]

    with pytest.raises(CaptureInvalid):
        await create_ar_capture(
            session=None,  # type: ignore[arg-type]
            kind="brick",
            recognition_rgb=recognition_rgb,
            recognition_depth=recognition_depth,
            ar_metadata="{}",
            images=uploads,
        )

    assert recognition_rgb.file.closed
    assert recognition_depth.file.closed
    assert all(upload.file.closed for upload in uploads)


async def test_ar_capture_route_closes_uploads_on_metadata_validation_error() -> None:
    from api.v1.ar_captures import create_ar_capture

    recognition_rgb = _upload("rgb.png", _png())
    recognition_depth = _upload("depth.png", _png())
    uploads = [_upload(f"{idx}.png", _png()) for idx in range(4)]

    with pytest.raises(CaptureInvalid):
        await create_ar_capture(
            session=None,  # type: ignore[arg-type]
            kind="brick",
            recognition_rgb=recognition_rgb,
            recognition_depth=recognition_depth,
            ar_metadata="not-json",
            images=uploads,
        )

    assert recognition_rgb.file.closed
    assert recognition_depth.file.closed
    assert all(upload.file.closed for upload in uploads)


async def test_parametric_route_closes_uploads_on_photo_count_validation_error() -> None:
    from api.v1.parametric_blocks import create_parametric_block

    uploads = [_upload(f"{idx}.png", _png()) for idx in range(21)]

    with pytest.raises(CaptureInvalid):
        await create_parametric_block(
            session=None,  # type: ignore[arg-type]
            system="duplo",
            kind="brick",
            units_x=2,
            units_y=2,
            raw_measurements_mm="{}",
            photos=uploads,
        )

    assert all(upload.file.closed for upload in uploads)


async def test_parametric_route_closes_uploads_on_measurement_validation_error() -> None:
    from api.v1.parametric_blocks import create_parametric_block

    uploads = [_upload(f"{idx}.png", _png()) for idx in range(2)]

    with pytest.raises(CaptureInvalid):
        await create_parametric_block(
            session=None,  # type: ignore[arg-type]
            system="duplo",
            kind="brick",
            units_x=2,
            units_y=2,
            raw_measurements_mm="not-json",
            photos=uploads,
        )

    assert all(upload.file.closed for upload in uploads)
