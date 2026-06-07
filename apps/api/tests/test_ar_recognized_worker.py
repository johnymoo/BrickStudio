"""Worker-level tests for the ar_recognized reconstruction branch."""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def work_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BLOCKTOOL_RECON_ROOT", str(tmp_path))
    yield tmp_path


def test_run_ar_recognized_pipeline_builds_canonical_glb(work_in_tmp: Path) -> None:
    """Insert an ar_recognized capture+job directly, run the pipeline,
    assert a canonical FEILE 2x4 GLB asset with pipeline_used=ar_recognized."""
    from db.models import Asset, Capture, Job
    from db.session import async_session_factory
    from workers.tasks.reconstruct import _run_ar_recognized_pipeline

    capture_id = uuid.uuid4()
    job_id = uuid.uuid4()

    async def _seed() -> None:
        f = async_session_factory()
        async with f() as session:
            session.add(
                Capture(
                    id=capture_id, part_id="feile-brick-2x4", status="pending",
                    image_count=0, image_keys=[], capture_mode="phone_walkaround",
                    mode="ar_recognized", system="feile", kind="brick",
                    units_x=2, units_y=4,
                    recognition_result={"ok": True, "system": "feile", "pitch_mm": 16.1,
                                        "confidence": 0.96, "units_x": 2, "units_y": 4,
                                        "warnings": [], "reason": None},
                )
            )
            session.add(Job(id=job_id, capture_id=capture_id, kind="reconstruct",
                            status="pending", progress=0, stage="queued"))
            await session.commit()

    asyncio.run(_seed())

    class _FakeTask:
        def update_state(self, **_: object) -> None:
            pass

    result = _run_ar_recognized_pipeline(_FakeTask(), capture_id, job_id)
    assert result["status"] == "completed"

    async def _asset() -> Asset | None:
        f = async_session_factory()
        async with f() as session:
            from sqlalchemy import select
            return (await session.execute(select(Asset).where(Asset.job_id == job_id))).scalar_one_or_none()

    asset = asyncio.run(_asset())
    assert asset is not None
    assert asset.meta["pipeline_used"] == "ar_recognized"
    assert asset.meta["system"] == "feile"
    assert asset.meta["units_x"] == 2 and asset.meta["units_y"] == 4
    # FEILE 2x4 brick: width=2*16=32 -> bbox_max.x=16; depth=4*16=64 -> bbox_max.y=32.
    assert abs(asset.meta["bbox_max"][0] - 16.0) < 0.5, asset.meta["bbox_max"]
    assert abs(asset.meta["bbox_max"][1] - 32.0) < 0.5, asset.meta["bbox_max"]
    # recognition_result echoed into asset meta.
    assert asset.meta["recognition_result"]["system"] == "feile"
