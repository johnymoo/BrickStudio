"""``GET /api/v1/health`` — checks the three dependencies return the right shape."""
from __future__ import annotations

from collections.abc import AsyncIterator


async def test_health_returns_200_and_shape(app_client: AsyncIterator) -> None:
    resp = await app_client.get("/api/v1/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] in {"ok", "degraded", "down"}
    assert "version" in body
    assert body["db"] in {"ok", "down"}
    assert body["storage"] in {"ok", "down"}
    assert body["redis"] in {"ok", "down"}


async def test_health_db_is_ok_with_real_postgres(app_client: AsyncIterator) -> None:
    resp = await app_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    # We expect db to be ok because conftest stood up a real postgres.
    assert body["db"] == "ok", body
