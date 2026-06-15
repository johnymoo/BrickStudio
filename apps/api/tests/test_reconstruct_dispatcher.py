"""Regression tests: routes must commit Capture/Job rows before dispatch."""
from __future__ import annotations

import json
import struct
import uuid
import zlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from fixtures.synth_studs import make_ar_bundle
from db.models import Capture, Job
from db.session import async_session_factory


@dataclass(frozen=True)
class _FakeAsyncResult:
    id: str


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


async def _assert_capture_and_job_visible(capture_id: str, job_id: str) -> None:
    factory = async_session_factory()
    async with factory() as session:
        capture = await session.get(Capture, uuid.UUID(capture_id))
        job = await session.get(Job, uuid.UUID(job_id))
        assert capture is not None, "capture must be committed before dispatch"
        assert job is not None, "job must be committed before dispatch"
        assert job.capture_id == capture.id


@pytest.fixture
def visible_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def _visible(capture_id: str, job_id: str) -> None:
        await _assert_capture_and_job_visible(capture_id, job_id)
        calls.append((capture_id, job_id))

    def fake_apply_async(*, args: list[str], task_id: str) -> _FakeAsyncResult:
        import asyncio
        import threading

        thread = threading.Thread(target=lambda: asyncio.run(_visible(args[0], task_id)))
        thread.start()
        thread.join()
        return _FakeAsyncResult(id=task_id)

    monkeypatch.setattr("services.reconstruct_dispatcher.reconstruct_task.apply_async", fake_apply_async)
    return calls


async def test_photo_capture_commits_before_dispatch(app_client: AsyncIterator, visible_dispatch: list[tuple[str, str]]) -> None:
    files = [("images", (f"{i}.png", _png(), "image/png")) for i in range(4)]

    resp = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "commit-photo", "capture_mode": "quick_snapshot"},
        files=files,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert visible_dispatch == [(body["capture_id"], body["job_id"])]


async def test_parametric_capture_commits_before_dispatch(app_client: AsyncIterator, visible_dispatch: list[tuple[str, str]]) -> None:
    resp = await app_client.post(
        "/api/v1/parametric-blocks",
        data={
            "part_id": "commit-parametric",
            "system": "feile",
            "kind": "brick",
            "units_x": "2",
            "units_y": "2",
            "raw_measurements_mm": json.dumps(
                {
                    "outer_pitch_mm": 33.4,
                    "inner_pitch_mm": 6.6,
                    "stud_diameter_mm": 9.4,
                    "brick_height_net_mm": 19.2,
                    "brick_height_total_mm": 24.6,
                }
            ),
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert visible_dispatch == [(body["capture_id"], body["job_id"])]


async def test_recognized_ar_capture_commits_before_dispatch(app_client: AsyncIterator, visible_dispatch: list[tuple[str, str]]) -> None:
    rgb, depth, meta = make_ar_bundle("feile", 2, 4)
    files = [
        ("recognition_rgb", ("rgb.png", rgb, "image/png")),
        ("recognition_depth", ("depth.png", depth, "image/png")),
        *[("images", (f"{i}.png", _png(), "image/png")) for i in range(4)],
    ]

    resp = await app_client.post(
        "/api/v1/ar-captures",
        data={"kind": "brick", "ar_metadata": json.dumps(meta)},
        files=files,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "recognized"
    assert visible_dispatch == [(body["capture_id"], body["job_id"])]
