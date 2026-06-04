"""End-to-end Celery task test.

Spawns a real Celery worker in-process (using celery's ``celery_worker`` test
fixture would be ideal but it requires live broker / backend). We use a
simpler approach: directly invoke the task function with the in-process
session, and verify that the job rows advance to ``completed`` and the
corresponding SSE events are published.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator

import redis.asyncio as aioredis


async def test_reconstruct_task_advances_job_to_completed(app_client: AsyncIterator) -> None:
    """Run the reconstruct task synchronously against the live DB + Redis,
    then assert the job moved pending → running → completed.
    """
    import struct
    import zlib

    from app.config import settings
    from db.models import Capture, Job
    from db.session import async_session_factory
    from workers.tasks.reconstruct import reconstruct

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

    # Pre-populate: a capture and a job (mirrors what the route does).
    capture_id = uuid.uuid4()
    job_id = uuid.uuid4()
    image_keys = [f"captures/{capture_id}/{i:03d}.png" for i in range(4)]
    # The new (real) reconstruct task actually downloads from MinIO; the
    # old placeholder didn't. We must PUT the PNGs in the bucket so the
    # download step doesn't bail with "missing in MinIO, skipping".
    from storage.minio_client import storage

    png_bytes = png()
    for k in image_keys:
        storage.put_object(
            settings.s3_bucket_raw,
            k,
            png_bytes,
            content_type="image/png",
        )

    factory = async_session_factory()
    async with factory() as session:
        cap = Capture(
            id=capture_id,
            part_id="test-celery",
            status="pending",
            image_count=4,
            image_keys=list(image_keys),
        )
        session.add(cap)
        session.add(
            Job(
                id=job_id,
                capture_id=capture_id,
                kind="reconstruct",
                status="pending",
                progress=0,
                stage="queued",
            )
        )
        await session.commit()

    # Subscribe to the job's pubsub channel before running the task.
    channel = f"job:{job_id}:events"
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel)

    # Run the task synchronously in a thread so the sync Celery code path is
    # exercised (the function uses `asyncio.run` internally so it can't be
    # awaited directly from inside the test's event loop).
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, reconstruct.apply, (str(capture_id),))

    # Drain published events (they're in the channel; non-blocking poll).
    # The real task can publish a flurry of progress events in <100ms
    # (the Open3D-only fallback is fast), so we read aggressively and
    # keep the loop going until either the ``completed`` event arrives
    # or the deadline (15s — generous to cover any CI hiccup) expires.
    events: list[dict] = []
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if msg is None:
            # If we've already seen ``completed`` we can stop early; otherwise
            # keep waiting for new events.
            if any(e.get("event") == "completed" for e in events):
                break
            continue
        try:
            events.append(json.loads(msg["data"]))
        except (TypeError, ValueError):
            continue

    # Verify state in the DB.
    async with factory() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.status == "completed", job.status
        assert job.progress == 100, job.progress
        assert job.stage == "completed", job.stage
        assert job.finished_at is not None

    # Verify events seen on the channel.
    kinds = {e.get("event") for e in events}
    assert "completed" in kinds, f"missing 'completed' event in {kinds}"
    # We should also have at least one progress event.
    assert any(e.get("event") == "progress" for e in events), events

    # Celery result is a dict describing the outcome.
    assert result.successful() is True or result.state == "SUCCESS", result.state

    # Clean up
    await pubsub.unsubscribe(channel)
    await pubsub.aclose()
    await redis_client.aclose()
