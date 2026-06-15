"""``/api/v1/library`` - list / read / update reusable library parts."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.auth import require_library_admin
from app.deps import DBSessionDep
from core.errors import PartNotFound
from db.models import Part
from models.schemas import LibraryPartRead, LibraryPartUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/library", tags=["library"])

PartStatus = Literal["pending", "verified", "rejected"]


@router.get("", response_model=list[LibraryPartRead], summary="List library parts")
async def list_parts(
    session: DBSessionDep,
    status_filter: Annotated[PartStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[LibraryPartRead]:
    stmt = select(Part).order_by(Part.created_at.desc(), Part.id.desc()).limit(limit)
    if status_filter is not None:
        stmt = stmt.where(Part.status == status_filter)
    parts = list((await session.scalars(stmt)).all())
    return [LibraryPartRead.model_validate(part) for part in parts]


@router.get("/{part_id}", response_model=LibraryPartRead, summary="Read a library part")
async def get_part(part_id: uuid.UUID, session: DBSessionDep) -> LibraryPartRead:
    part = await session.get(Part, part_id)
    if part is None:
        raise PartNotFound(f"part {part_id} not found")
    return LibraryPartRead.model_validate(part)


@router.patch("/{part_id}", response_model=LibraryPartRead, summary="Update a library part")
async def update_part(
    part_id: uuid.UUID,
    _: Annotated[None, Depends(require_library_admin)],
    payload: LibraryPartUpdate,
    session: DBSessionDep,
) -> LibraryPartRead:
    part = await session.get(Part, part_id)
    if part is None:
        raise PartNotFound(f"part {part_id} not found")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(part, field, value)
    await session.commit()
    await session.refresh(part)
    logger.info("library.update: part_id=%s fields=%s", part_id, list(data.keys()))
    return LibraryPartRead.model_validate(part)


__all__ = ["router"]
