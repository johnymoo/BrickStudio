"""``POST /api/v1/captures`` and ``GET /api/v1/captures/{id}``."""
from __future__ import annotations

import io
import struct
import zlib
from collections.abc import AsyncIterator


def _png(width: int = 4, height: int = 4) -> bytes:
    """Build a minimal valid 4x4 PNG (3 channels) without external deps.

    Just enough to satisfy the multipart upload — we don't decode the bytes
    on the server, we only PUT them in MinIO.
    """
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = b""
    for _ in range(height):
        raw += b"\x00"  # filter: none
        raw += b"\xff\x80\x00" * width  # solid orange pixels
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _files(n: int) -> list[tuple[str, bytes, str]]:
    return [("images", (f"img_{i}.png", _png(), "image/png")) for i in range(n)]


async def test_create_capture_with_4_images_returns_201(app_client: AsyncIterator) -> None:
    resp = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-part-001"},
        files=_files(4),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["part_id"] == "test-part-001"
    assert body["image_count"] == 4
    assert body["status"] == "pending"
    assert "capture_id" in body
    assert "job_id" in body


async def test_create_capture_rejects_3_images_with_422(app_client: AsyncIterator) -> None:
    resp = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-part-002"},
        files=_files(3),
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    # Envelope is either FastAPI's default validation error or our custom
    # CAPTURE_INVALID (depending on where the check fires).
    if "error" in body:
        assert body["error"]["code"] in {"CAPTURE_INVALID", "VALIDATION_ERROR"}
    else:
        # FastAPI's default shape — should mention image count.
        assert any("image" in str(item).lower() for item in body.get("detail", []))


async def test_get_capture_returns_200(app_client: AsyncIterator) -> None:
    # Create first.
    create = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-part-003"},
        files=_files(4),
    )
    capture_id = create.json()["capture_id"]

    resp = await app_client.get(f"/api/v1/captures/{capture_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["capture_id"] == capture_id
    assert body["part_id"] == "test-part-003"
    assert body["image_count"] == 4
    assert body["status"] in {"pending", "running", "completed", "failed"}


async def test_get_unknown_capture_returns_404(app_client: AsyncIterator) -> None:
    import uuid

    missing = uuid.uuid4()
    resp = await app_client.get(f"/api/v1/captures/{missing}")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] in {"CAPTURE_NOT_FOUND", "NOT_FOUND"}
