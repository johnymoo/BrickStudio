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
