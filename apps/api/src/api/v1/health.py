"""``GET /api/v1/health`` — liveness + dependency probe (design.md §6.4)."""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Request
from sqlalchemy import text

from app.config import settings
from db.session import get_engine
from models.schemas import HealthRead
from storage.minio_client import StorageClient

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

VERSION = os.environ.get("APP_VERSION", "0.1.0")


@router.get("/health", response_model=HealthRead)
async def health(request: Request) -> HealthRead:
    """Probe Postgres, Redis, and MinIO; return a uniform status object."""
    db_status, db_err = await _check_db()
    redis_status, redis_err = await _check_redis()
    storage_status, storage_err = await _check_storage(request)

    details: dict[str, Any] = {}
    if db_err:
        details["db_error"] = db_err
    if redis_err:
        details["redis_error"] = redis_err
    if storage_err:
        details["storage_error"] = storage_err

    overall = "ok" if db_status == "ok" and storage_status == "ok" else "degraded"
    if db_status == "down" or storage_status == "down":
        overall = "down"

    return HealthRead(
        status=overall,
        version=VERSION,
        db=db_status,
        storage=storage_status,
        redis=redis_status,
        details=details or None,
    )


# ---- individual probes ----------------------------------------------------
async def _check_db() -> tuple[str, str | None]:
    engine = get_engine()
    t0 = time.perf_counter()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "ok", None
    except Exception as exc:
        logger.warning("health: db probe failed in %.0fms", (time.perf_counter() - t0) * 1000)
        return "down", str(exc)


async def _check_redis() -> tuple[str, str | None]:
    try:
        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url, decode_responses=True
        )
        try:
            await client.ping()
            return "ok", None
        finally:
            await client.aclose()
    except Exception as exc:
        return "down", str(exc)


async def _check_storage(request: Request) -> tuple[str, str | None]:
    client: StorageClient | None = getattr(request.app.state, "storage", None)
    if client is None:
        client = StorageClient()
    try:
        ok = client._client.bucket_exists(settings.s3_bucket_raw)
        return ("ok" if ok else "down"), None if ok else f"bucket {settings.s3_bucket_raw} missing"
    except Exception as exc:
        return "down", str(exc)
