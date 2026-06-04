"""ORM models for the captures / jobs / assets tables.

Schema follows `docs/design.md` §7 exactly:

* ``captures``:  uuid, part_id, status, image_count, image_keys (jsonb), ts
* ``jobs``:      uuid, capture_id (FK), kind, status, progress, stage, error, ts
* ``assets``:    uuid, job_id (FK), kind, storage_key, size_bytes, meta (jsonb), ts
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, make_created_at, make_updated_at, make_uuid_pk


# ---------------------------------------------------------------------------
# captures
# ---------------------------------------------------------------------------
class Capture(Base):
    """A user upload session: a batch of photos of one physical part."""

    __tablename__ = "captures"
    __table_args__ = (Index("ix_captures_part_id", "part_id"),)

    id: Mapped[UUID] = make_uuid_pk()
    part_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    image_count: Mapped[int] = mapped_column(Integer, nullable=False)
    image_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = make_created_at()
    updated_at: Mapped[datetime] = make_updated_at()

    jobs: Mapped[list[Job]] = relationship(back_populates="capture", lazy="selectin")


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------
class Job(Base):
    """An async unit of work (3D reconstruction, etc.) tied to a capture."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_capture_id", "capture_id"),)

    id: Mapped[UUID] = make_uuid_pk()
    capture_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("captures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="reconstruct")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = make_created_at()
    updated_at: Mapped[datetime] = make_updated_at()

    capture: Mapped[Capture] = relationship(back_populates="jobs", lazy="selectin")
    assets: Mapped[list[Asset]] = relationship(
        back_populates="job", lazy="selectin", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# assets
# ---------------------------------------------------------------------------
class Asset(Base):
    """A blob in S3 produced by a job (mesh, point cloud, texture, ...)."""

    __tablename__ = "assets"
    __table_args__ = (Index("ix_assets_job_id", "job_id"),)

    id: Mapped[UUID] = make_uuid_pk()
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = make_created_at()

    job: Mapped[Job] = relationship(back_populates="assets", lazy="selectin")


__all__ = ["Asset", "Capture", "Job"]
