"""``GET /api/v1/jobs/{id}`` + ``GET /api/v1/jobs/{id}/stream`` (SSE)."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator


async def test_get_unknown_job_returns_404(app_client: AsyncIterator) -> None:
    resp = await app_client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] in {"JOB_NOT_FOUND", "NOT_FOUND"}


async def test_create_capture_then_get_job(app_client: AsyncIterator) -> None:
    """End-to-end: upload 4 images, then read the auto-created job."""
    import struct
    import zlib

    def png() -> bytes:
        def chunk(tag: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        sig = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)
        raw = b"\x00" + b"\xff\x80\x00" * 4
        idat = zlib.compress(raw * 4)
        return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")

    files = [("images", (f"img_{i}.png", png(), "image/png")) for i in range(4)]
    create = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-sse"},
        files=files,
    )
    assert create.status_code == 201, create.text
    job_id = create.json()["job_id"]

    resp = await app_client.get(f"/api/v1/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["status"] in {"pending", "running", "completed", "failed"}


async def test_sse_stream_emits_completed_for_terminal_job(app_client: AsyncIterator) -> None:
    """When the job is already in a terminal state, the stream should emit
    a single ``completed`` event and then close. This exercises the SSE
    wiring (Redis pubsub subscribe + SSE frame formatting + content-type)
    without requiring a running Celery worker.

    We force the job to "completed" via the ORM, then open the stream —
    the handler's terminal-state fast path yields one event and exits.
    """
    import struct
    import zlib
    from datetime import datetime, timezone

    from sqlalchemy import update

    from db.models import Job
    from db.session import async_session_factory

    def png() -> bytes:
        def chunk(tag: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        sig = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)
        raw = b"\x00" + b"\xff\x80\x00" * 4
        idat = zlib.compress(raw * 4)
        return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")

    files = [("images", (f"img_{i}.png", png(), "image/png")) for i in range(4)]
    create = await app_client.post(
        "/api/v1/captures",
        data={"part_id": "test-sse-completed"},
        files=files,
    )
    job_id = create.json()["job_id"]

    # Force the job into "completed" state in the DB so the SSE handler takes
    # the terminal-state fast path.
    factory = async_session_factory()
    async with factory() as session:
        await session.execute(
            update(Job)
            .where(Job.id == uuid.UUID(job_id))
            .values(
                status="completed",
                progress=100,
                stage="completed",
                finished_at=datetime.now(tz=timezone.utc),
            )
        )
        await session.commit()

    # Read the full response (the stream is short — one event and done).
    resp = await app_client.get(f"/api/v1/jobs/{job_id}/stream")
    assert resp.status_code == 200
    body = resp.text
    assert "event: completed" in body, body
    assert f'"job_id": "{job_id}"' in body, body
