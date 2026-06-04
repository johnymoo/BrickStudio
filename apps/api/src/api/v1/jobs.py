"""``/api/v1/jobs`` — job status + SSE stream (design.md §6.2)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.deps import DBSessionDep
from core.errors import JobNotFound
from db.models import Job
from models.schemas import JobRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobRead, summary="Read a job")
async def get_job(job_id: uuid.UUID, session: DBSessionDep) -> JobRead:
    job = await session.get(Job, job_id)
    if job is None:
        raise JobNotFound(f"job {job_id} not found")
    return JobRead.model_validate(job)


@router.get(
    "/{job_id}/stream",
    summary="SSE: stream job progress",
    response_class=StreamingResponse,
    response_model=None,
)
async def stream_job(
    job_id: uuid.UUID,
    request: Request,
    session: DBSessionDep,
) -> StreamingResponse:
    """Subscribe to ``job:{id}:events`` and push each message as an SSE frame.

    The stream auto-closes once the job reaches a terminal state
    (``completed`` / ``failed``) or the client disconnects.
    """
    job = await session.get(Job, job_id)
    if job is None:
        raise JobNotFound(f"job {job_id} not found")

    channel = f"job:{job_id}:events"

    async def event_source() -> AsyncIterator[bytes]:
        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url, decode_responses=True
        )
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
        logger.info("jobs.stream: subscribed %s", channel)
        try:
            # Send a 'connected' frame so clients know the stream is live.
            yield _sse_frame("connected", {"job_id": str(job_id), "channel": channel})

            # If the job is already terminal, just emit a final frame and stop.
            terminal = job.status in {"completed", "failed"}
            if terminal:
                payload: dict[str, Any] = {
                    "job_id": str(job_id),
                    "progress": job.progress,
                    "stage": job.stage,
                    "ts": _now_iso(),
                }
                # Include the result asset id when the job completed so
                # the front-end can render the 3D viewer without an
                # extra GET /jobs/{id} round-trip. Mirrors the worker's
                # ``completed`` event payload.
                if job.status == "completed" and job.assets:
                    payload["result_asset_id"] = str(job.assets[0].id)
                event_name = "completed" if job.status == "completed" else "failed"
                if job.error:
                    payload["error"] = job.error
                yield _sse_frame(event_name, payload)
                return

            while True:
                if await request.is_disconnected():
                    logger.info("jobs.stream: client disconnected job_id=%s", job_id)
                    break

                try:
                    msg = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                        timeout=2.0,
                    )
                except TimeoutError:
                    # heartbeat: keep proxies from closing the connection.
                    yield b": keep-alive\n\n"
                    continue

                if msg is None:
                    yield b": keep-alive\n\n"
                    continue

                data = msg.get("data")
                if not data:
                    continue
                try:
                    payload_dict: dict[str, Any] = (
                        json.loads(data) if isinstance(data, str) else json.loads(data.decode())
                    )
                except (TypeError, ValueError):
                    logger.warning("jobs.stream: non-JSON payload on %s: %r", channel, data)
                    continue

                event = str(payload_dict.get("event", "progress"))
                yield _sse_frame(event, payload_dict)

                if event in {"completed", "failed"}:
                    break
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(channel)
            with contextlib.suppress(Exception):
                await pubsub.aclose()
            with contextlib.suppress(Exception):
                await client.aclose()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---- helpers --------------------------------------------------------------
def _sse_frame(event: str, payload: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n".encode()


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).isoformat()
