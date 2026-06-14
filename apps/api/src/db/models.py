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
    # Phase 2 (design-phase2.md §3.1): enum in
    # {phone_walkaround, studio_turntable, quick_snapshot}. The default
    # is ``phone_walkaround`` (the design contract). For the very rare
    # existing v0.1 rows that predate this column, the migration
    # 0002 backfills them with ``phone_walkaround`` via the column
    # ``server_default`` (Postgres applies the default on insert when
    # the application does not specify a value).
    capture_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="phone_walkaround",
        server_default="phone_walkaround",
    )
    # ---- Parametric-block (v0.3+, capture-procedure.md / ROADMAP.md §v0.3) ----
    # These columns are populated ONLY for captures whose ``mode`` is
    # ``"parametric_block"`` (i.e. no SfM/photo path — the user measured
    # the part with a caliper and ``tools/measure_block.py`` derived the
    # GLB from public spec instead). For photo captures they stay NULL.
    # ``mode`` is NOT NULL with a server default of ``"photo"`` so the
    # 0003 migration backfills all existing rows safely.
    mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="photo",
        server_default="photo",
    )
    #: Brick system: one of ``"duplo"``, ``"lego"``, ``"feile"``, ``"generic"``.
    system: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Brick kind: one of ``"brick"``, ``"plate"``, ``"tile"``, ``"slope"``.
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Stud-grid dimensions in units (1 unit = 8mm LEGO / 20mm DUPLO / ...).
    units_x: Mapped[int | None] = mapped_column(Integer, nullable=True)
    units_y: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 5 raw caliper measurements in millimetres, captured verbatim from
    #: the user. Keys: ``outer_pitch``, ``inner_pitch``, ``stud_diameter``,
    #: ``brick_height_net``, ``brick_height_total``. Populated by
    #: ``tools/measure_block.py`` from the form input.
    raw_measurements_mm: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: 4 spec values derived from the raw measurements (the canonical
    #: ``BlockSpec`` fields: ``unit_mm``, ``plate_height_mm``,
    #: ``stud_diameter_mm``, ``clearance_mm``). Populated by
    #: ``tools/measure_block.py`` after the algebraic derivation step.
    derived_spec_mm: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: Cross-check warnings raised by ``tools/measure_block.py`` when the
    #: 5 caliper numbers disagree beyond tolerance (e.g. unit_mm drift,
    #: inconsistent inner/outer pitch). Empty list = clean.
    cross_check_warnings: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    #: AR capture (v0.5, ar_recognized mode): the raw ``ar_metadata``
    #: JSON the phone uploaded (camera intrinsics, depth dims, pose,
    #: distance, coarse hints). Stored verbatim for audit / re-run.
    ar_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: AR recognition output: ``units_x`` / ``units_y`` / ``pitch_mm`` /
    #: ``system`` / ``confidence`` / ``warnings`` / ``ok`` / ``reason``.
    #: Populated by ``services.brick_recognizer.recognize_brick`` in the
    #: route layer. NULL for non-AR captures.
    recognition_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = make_created_at()
    updated_at: Mapped[datetime] = make_updated_at()

    jobs: Mapped[list[Job]] = relationship(back_populates="capture", lazy="selectin")
    part: Mapped[Part | None] = relationship(
        back_populates="capture", uselist=False, lazy="selectin"
    )


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
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


# ---------------------------------------------------------------------------
# parts (issue #5 — reusable library part, 1:1 with a completed capture)
# ---------------------------------------------------------------------------
class Part(Base):
    """A durable, curated library entry promoted from a completed capture.

    1:1 with its source capture (``capture_id`` unique). Holds a *snapshot*
    of the capture's spec at promotion time plus curation metadata
    (``name`` / ``notes`` / ``color``) and a review ``status``. The linked
    GLB lives on ``asset_id``; provenance is the ``capture`` relationship.
    """

    __tablename__ = "parts"
    __table_args__ = (
        Index("ix_parts_capture_id", "capture_id", unique=True),
        Index("ix_parts_asset_id", "asset_id"),
        Index("ix_parts_status", "status"),
    )

    id: Mapped[UUID] = make_uuid_pk()
    capture_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("captures.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The GLB asset this part renders. Nullable so the part survives if the
    #: asset is regenerated; SET NULL on asset delete.
    asset_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Snapshot of ``capture.mode`` at promotion: photo / parametric_block / ar_recognized.
    source_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    system: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    units_x: Mapped[int | None] = mapped_column(Integer, nullable=True)
    units_y: Mapped[int | None] = mapped_column(Integer, nullable=True)
    derived_spec_mm: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    color: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Review state: pending / verified / rejected.
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    created_at: Mapped[datetime] = make_created_at()
    updated_at: Mapped[datetime] = make_updated_at()

    capture: Mapped[Capture] = relationship(back_populates="part", lazy="selectin")


__all__ = ["Asset", "Capture", "Job", "Part"]
