"""``GET /api/v1/assets/{asset_id}`` — redirect to a presigned URL."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import insert

from db.models import Asset, Capture, Job
from db.session import async_session_factory


async def test_get_unknown_asset_returns_404(app_client: AsyncIterator) -> None:
    resp = await app_client.get(f"/api/v1/assets/{uuid.uuid4()}")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] in {"ASSET_NOT_FOUND", "NOT_FOUND"}


async def test_get_asset_returns_302_with_presigned_url(app_client: AsyncIterator) -> None:
    """Browser-style callers can still follow a 302 to the presigned URL."""
    asset_id, job_id = await _seed_asset("test-asset", "recon/test/mesh.glb")

    # Now hit the asset endpoint. We use follow_redirects=False so we can
    # inspect the 302 status + Location header.
    resp = await app_client.get(f"/api/v1/assets/{asset_id}", follow_redirects=False)
    assert resp.status_code == 302, resp.text
    assert "Location" in resp.headers
    location = resp.headers["Location"]
    assert "mesh.glb" in location or "X-Amz-Signature" in location or "blocktool-test-recon" in location, location

    # And the body (we keep it next to the 302) is a JSON envelope.
    body = resp.json()
    assert body["asset_id"] == str(asset_id)
    assert body["job_id"] == str(job_id)
    assert body["kind"] == "mesh_gltf"
    assert body["url"], body


async def test_get_asset_returns_json_when_client_accepts_json(app_client: AsyncIterator) -> None:
    """SPA fetch callers need the JSON envelope, not an auto-followed GLB."""
    asset_id, job_id = await _seed_asset("test-asset-json", "recon/test-json/mesh.glb")

    resp = await app_client.get(
        f"/api/v1/assets/{asset_id}",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert resp.status_code == 200, resp.text
    assert "Location" not in resp.headers
    body = resp.json()
    assert body["asset_id"] == str(asset_id)
    assert body["job_id"] == str(job_id)
    assert body["url"], body


async def _seed_asset(part_id: str, storage_key: str) -> tuple[uuid.UUID, uuid.UUID]:
    capture_id = uuid.uuid4()
    job_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    factory = async_session_factory()
    async with factory() as session:
        session.add(
            Capture(
                id=capture_id,
                part_id=part_id,
                status="completed",
                image_count=0,
                image_keys=[],
                capture_mode="parametric_block",
                mode="parametric_block",
            )
        )
        session.add(
            Job(
                id=job_id,
                capture_id=capture_id,
                kind="reconstruct",
                status="completed",
                progress=100,
                stage="completed",
            )
        )
        await session.execute(
            insert(Asset).values(
                id=asset_id,
                job_id=job_id,
                kind="mesh_gltf",
                storage_key=storage_key,
                size_bytes=1024,
                meta={"vertices": 100, "faces": 200},
                created_at=datetime.now(tz=UTC),
            )
        )
        await session.commit()
    return asset_id, job_id
