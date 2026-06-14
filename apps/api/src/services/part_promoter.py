"""Promote a completed capture into a reusable library ``Part``.

Called best-effort from the Celery worker's shared finalize tail
(:func:`workers.tasks.reconstruct._finalize_mesh_pipeline`) right after the
GLB asset row is written. Idempotent on ``capture_id``: a worker re-run
updates the asset id + spec snapshot but never clobbers user-edited
``name`` / ``notes`` / ``status``.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from db.models import Capture, Part
from db.session import async_session_factory

logger = logging.getLogger(__name__)


async def promote_capture_to_part(capture_id: uuid.UUID, asset_id: uuid.UUID | None) -> None:
    """Upsert a ``Part`` for ``capture_id`` pointing at ``asset_id``.

    Snapshots ``source_mode`` / ``system`` / ``kind`` / ``units_x`` /
    ``units_y`` / ``derived_spec_mm`` from the capture. On INSERT, ``name``
    defaults to ``capture.part_id`` and ``status`` to ``"pending"``. On
    UPDATE (re-run), only the asset id + spec snapshot change.
    """
    factory = async_session_factory()
    async with factory() as session:
        capture = await session.get(Capture, capture_id)
        if capture is None:
            logger.warning("promote_capture_to_part: capture %s not found; skipping", capture_id)
            return

        stmt = insert(Part).values(
            capture_id=capture_id,
            asset_id=asset_id,
            source_mode=capture.mode,
            system=capture.system,
            kind=capture.kind,
            units_x=capture.units_x,
            units_y=capture.units_y,
            derived_spec_mm=capture.derived_spec_mm,
            name=capture.part_id,
            status="pending",
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[Part.capture_id],
                set_={
                    "asset_id": stmt.excluded.asset_id,
                    "source_mode": stmt.excluded.source_mode,
                    "system": stmt.excluded.system,
                    "kind": stmt.excluded.kind,
                    "units_x": stmt.excluded.units_x,
                    "units_y": stmt.excluded.units_y,
                    "derived_spec_mm": stmt.excluded.derived_spec_mm,
                    "updated_at": func.now(),
                },
            )
        )
        await session.commit()
