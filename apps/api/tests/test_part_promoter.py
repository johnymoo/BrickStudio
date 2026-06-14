"""Tests for the parts library ORM model."""
from __future__ import annotations

import asyncio
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


async def _load_part(capture_id: uuid.UUID) -> Part | None:
    from sqlalchemy import select

    factory = async_session_factory()
    async with factory() as session:
        return (await session.scalars(select(Part).where(Part.capture_id == capture_id))).one_or_none()


async def test_promote_parametric_capture_snapshots_spec() -> None:
    from services.part_promoter import promote_capture_to_part

    capture_id, asset_id = await _seed_capture(
        mode="parametric_block",
        part_id="feile-brick-2x2",
        system="feile",
        kind="brick",
        units_x=2,
        units_y=2,
        derived_spec_mm={"unit_mm": 16.0, "height_mm": 19.2},
    )
    await promote_capture_to_part(capture_id, asset_id)

    part = await _load_part(capture_id)
    assert part is not None
    assert part.source_mode == "parametric_block"
    assert part.system == "feile"
    assert part.kind == "brick"
    assert part.units_x == 2 and part.units_y == 2
    assert part.derived_spec_mm == {"unit_mm": 16.0, "height_mm": 19.2}
    assert part.name == "feile-brick-2x2"
    assert part.status == "pending"
    assert part.asset_id == asset_id


async def test_promote_photo_capture_leaves_spec_null() -> None:
    from services.part_promoter import promote_capture_to_part

    capture_id, asset_id = await _seed_capture(mode="photo", part_id="photo-scan-1", image_count=8)
    await promote_capture_to_part(capture_id, asset_id)

    part = await _load_part(capture_id)
    assert part is not None
    assert part.source_mode == "photo"
    assert part.system is None and part.kind is None
    assert part.units_x is None and part.units_y is None
    assert part.derived_spec_mm is None
    assert part.name == "photo-scan-1"


async def test_promote_is_idempotent_and_preserves_user_edits() -> None:
    from sqlalchemy import select

    from services.part_promoter import promote_capture_to_part

    capture_id, asset_id = await _seed_capture(
        mode="ar_recognized",
        system="feile",
        kind="brick",
        units_x=2,
        units_y=4,
    )
    await promote_capture_to_part(capture_id, asset_id)

    # Simulate the user editing the part.
    factory = async_session_factory()
    async with factory() as session:
        part = (await session.scalars(select(Part).where(Part.capture_id == capture_id))).one()
        part.name = "renamed-by-user"
        part.notes = "looks good"
        part.status = "verified"
        await session.commit()

    # Re-promote with a changed capture spec and NEW asset id (worker re-run).
    new_asset_id = uuid.uuid4()
    async with factory() as session:
        from db.models import Asset, Job

        capture = await session.get(Capture, capture_id)
        assert capture is not None
        capture.kind = "plate"
        capture.units_x = 3
        capture.units_y = 6
        capture.derived_spec_mm = {"unit_mm": 16.0, "height_mm": 6.4}
        job = (await session.scalars(select(Job).where(Job.capture_id == capture_id))).first()
        session.add(Asset(id=new_asset_id, job_id=job.id, kind="mesh_gltf", storage_key="recon/x/mesh.glb"))
        await session.commit()
    await promote_capture_to_part(capture_id, new_asset_id)

    part = await _load_part(capture_id)
    assert part is not None
    assert part.asset_id == new_asset_id
    assert part.source_mode == "ar_recognized"
    assert part.system == "feile"
    assert part.kind == "plate"
    assert part.units_x == 3 and part.units_y == 6
    assert part.derived_spec_mm == {"unit_mm": 16.0, "height_mm": 6.4}
    assert part.name == "renamed-by-user"
    assert part.notes == "looks good"
    assert part.status == "verified"
    async with factory() as session:
        count = len((await session.scalars(select(Part).where(Part.capture_id == capture_id))).all())
    assert count == 1


async def test_promote_concurrent_initial_calls_create_one_part() -> None:
    from sqlalchemy import select

    from services.part_promoter import promote_capture_to_part

    capture_id, asset_id = await _seed_capture(
        mode="parametric_block",
        part_id="race-safe-2x2",
        system="feile",
        kind="brick",
        units_x=2,
        units_y=2,
        derived_spec_mm={"unit_mm": 16.0, "height_mm": 19.2},
    )

    await asyncio.gather(
        promote_capture_to_part(capture_id, asset_id),
        promote_capture_to_part(capture_id, asset_id),
    )

    factory = async_session_factory()
    async with factory() as session:
        parts = (await session.scalars(select(Part).where(Part.capture_id == capture_id))).all()

    assert len(parts) == 1
    assert parts[0].asset_id == asset_id
