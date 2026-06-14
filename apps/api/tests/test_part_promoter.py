"""Tests for the parts library ORM model."""
from __future__ import annotations

import uuid

from db.models import Asset, Capture, Job, Part
from db.session import async_session_factory


async def _seed_capture(*, mode: str, **cols) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a capture + job + GLB asset; return (capture_id, asset_id)."""
    capture_id = uuid.uuid4()
    factory = async_session_factory()
    async with factory() as session:
        session.add(
            Capture(
                id=capture_id,
                part_id=cols.pop("part_id", f"seed-{str(capture_id)[:8]}"),
                status="completed",
                image_count=cols.pop("image_count", 0),
                image_keys=[],
                mode=mode,
                **cols,
            )
        )
        job = Job(capture_id=capture_id, kind="reconstruct", status="completed", progress=100)
        session.add(job)
        await session.flush()
        asset = Asset(job_id=job.id, kind="mesh_gltf", storage_key=f"recon/{job.id}/mesh.glb", size_bytes=9000)
        session.add(asset)
        await session.flush()
        asset_id = asset.id
        await session.commit()
    return capture_id, asset_id


async def test_part_model_round_trip() -> None:
    assert "ix_parts_asset_id" in {index.name for index in Part.__table__.indexes}

    capture_id, asset_id = await _seed_capture(mode="parametric_block", system="feile", kind="brick", units_x=2, units_y=2)
    factory = async_session_factory()
    async with factory() as session:
        session.add(
            Part(
                capture_id=capture_id,
                asset_id=asset_id,
                source_mode="parametric_block",
                system="feile",
                kind="brick",
                units_x=2,
                units_y=2,
                name="my-feile-2x2",
            )
        )
        await session.commit()
    async with factory() as session:
        from sqlalchemy import select

        part = (await session.scalars(select(Part).where(Part.capture_id == capture_id))).one()
        assert part.name == "my-feile-2x2"
        assert part.status == "pending"
        assert part.source_mode == "parametric_block"
        assert part.asset_id == asset_id
