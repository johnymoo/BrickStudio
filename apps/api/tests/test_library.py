"""Endpoint tests for /api/v1/library."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from db.models import Asset, Capture, Job, Part
from db.session import async_session_factory


async def _make_part(
    *,
    status: str = "pending",
    name: str = "p",
    source_mode: str = "parametric_block",
    part_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> uuid.UUID:
    capture_id = uuid.uuid4()
    factory = async_session_factory()
    async with factory() as session:
        session.add(
            Capture(
                id=capture_id,
                part_id=name,
                status="completed",
                image_count=0,
                image_keys=[],
                mode=source_mode,
            )
        )
        job = Job(capture_id=capture_id, kind="reconstruct", status="completed", progress=100)
        session.add(job)
        await session.flush()
        asset = Asset(
            job_id=job.id, kind="mesh_gltf", storage_key=f"recon/{job.id}/mesh.glb", size_bytes=9000
        )
        session.add(asset)
        await session.flush()
        part = Part(
            id=part_id,
            capture_id=capture_id,
            asset_id=asset.id,
            source_mode=source_mode,
            system="feile",
            kind="brick",
            units_x=2,
            units_y=2,
            name=name,
            status=status,
        )
        if created_at is not None:
            part.created_at = created_at
        session.add(part)
        await session.flush()
        part_id = part.id
        await session.commit()
    return part_id


async def test_list_library_returns_parts(app_client) -> None:
    await _make_part(name="a", status="pending")
    await _make_part(name="b", status="verified")
    resp = await app_client.get("/api/v1/library")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2
    assert {r["name"] for r in rows} == {"a", "b"}
    assert "part_id" in rows[0] and "asset_id" in rows[0] and "source_mode" in rows[0]


async def test_list_library_filters_by_status(app_client) -> None:
    await _make_part(name="a", status="pending")
    await _make_part(name="b", status="verified")
    resp = await app_client.get("/api/v1/library?status=verified")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "b"


async def test_list_library_limit_uses_uuid_desc_tiebreaker(app_client) -> None:
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    low_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    high_id = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    await _make_part(part_id=low_id, name="low", created_at=created_at)
    await _make_part(part_id=high_id, name="high", created_at=created_at)

    resp = await app_client.get("/api/v1/library?limit=1")

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["part_id"] == str(high_id)


async def test_get_library_part_detail(app_client) -> None:
    part_id = await _make_part(name="detail-me")
    resp = await app_client.get(f"/api/v1/library/{part_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["part_id"] == str(part_id)
    assert body["name"] == "detail-me"
    assert body["system"] == "feile"


async def test_get_library_part_404(app_client) -> None:
    resp = await app_client.get(f"/api/v1/library/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "PART_NOT_FOUND"


async def test_patch_library_part_updates_fields(app_client) -> None:
    part_id = await _make_part(name="before")
    resp = await app_client.patch(
        f"/api/v1/library/{part_id}",
        json={"name": "after", "notes": "great", "status": "verified"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "after"
    assert body["notes"] == "great"
    assert body["status"] == "verified"


async def test_patch_library_part_rejects_bad_status(app_client) -> None:
    part_id = await _make_part()
    resp = await app_client.patch(f"/api/v1/library/{part_id}", json={"status": "bogus"})
    assert resp.status_code == 422, resp.text


async def test_patch_library_part_rejects_null_name(app_client) -> None:
    part_id = await _make_part()
    resp = await app_client.patch(f"/api/v1/library/{part_id}", json={"name": None})
    assert resp.status_code == 422, resp.text


async def test_patch_library_part_rejects_empty_name(app_client) -> None:
    part_id = await _make_part()
    resp = await app_client.patch(f"/api/v1/library/{part_id}", json={"name": ""})
    assert resp.status_code == 422, resp.text


async def test_patch_library_part_rejects_name_over_db_limit(app_client) -> None:
    part_id = await _make_part()
    resp = await app_client.patch(f"/api/v1/library/{part_id}", json={"name": "x" * 129})
    assert resp.status_code == 422, resp.text


async def test_patch_library_part_rejects_null_status(app_client) -> None:
    part_id = await _make_part()
    resp = await app_client.patch(f"/api/v1/library/{part_id}", json={"status": None})
    assert resp.status_code == 422, resp.text


async def test_patch_library_part_404(app_client) -> None:
    resp = await app_client.patch(f"/api/v1/library/{uuid.uuid4()}", json={"name": "x"})
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "PART_NOT_FOUND"
