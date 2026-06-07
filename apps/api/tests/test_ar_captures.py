"""Endpoint tests for POST /api/v1/ar-captures."""
from __future__ import annotations

import json
import struct
import zlib
from collections.abc import AsyncIterator

from fixtures.synth_studs import make_ar_bundle


def _png(width: int = 4, height: int = 4, color: bytes = b"\xff\x80\x00") -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + color * width for _ in range(height))
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _files(rgb: bytes, depth: bytes, n_images: int = 4):
    files = [
        ("recognition_rgb", ("rgb.png", rgb, "image/png")),
        ("recognition_depth", ("depth.png", depth, "image/png")),
    ]
    files += [("images", (f"{i}.png", _png(), "image/png")) for i in range(n_images)]
    return files


async def test_ar_capture_recognized_feile(app_client: AsyncIterator) -> None:
    rgb, depth, meta = make_ar_bundle("feile", 2, 4)
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta)},
        files=_files(rgb, depth),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "recognized"
    assert body["mode"] == "ar_recognized"
    assert body["recognized"]["system"] == "feile"
    assert body["recognized"]["units_x"] == 2
    assert body["recognized"]["units_y"] == 4
    assert body["job_id"] is not None
    assert body["needs_measurement"] is None


async def test_ar_capture_unknown_hint_needs_measurement(app_client: AsyncIterator) -> None:
    rgb, depth, meta = make_ar_bundle("feile", 2, 2)
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta), "system_hint": "unknown"},
        files=_files(rgb, depth),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "needs_measurement"
    assert body["job_id"] is None
    assert body["needs_measurement"]["endpoint"] == "/api/v1/parametric-blocks"
    assert "outer_pitch_mm" in body["needs_measurement"]["fields"]


async def test_ar_capture_rejects_missing_depth(app_client: AsyncIterator) -> None:
    rgb, _depth, meta = make_ar_bundle("feile")
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta)},
        files=[("recognition_rgb", ("rgb.png", rgb, "image/png"))]
        + [("images", (f"{i}.png", _png(), "image/png")) for i in range(4)],
    )
    assert resp.status_code == 422, resp.text


async def test_ar_capture_rejects_bad_metadata_json(app_client: AsyncIterator) -> None:
    rgb, depth, _meta = make_ar_bundle("feile")
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": "not-json"},
        files=_files(rgb, depth),
    )
    assert resp.status_code == 422, resp.text


async def test_ar_capture_rejects_too_few_images(app_client: AsyncIterator) -> None:
    rgb, depth, meta = make_ar_bundle("feile")
    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta)},
        files=_files(rgb, depth, n_images=2),
    )
    assert resp.status_code == 422, resp.text
