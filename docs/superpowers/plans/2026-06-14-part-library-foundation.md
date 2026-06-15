# Part Library Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable, named, status-tracked `parts` library that auto-captures every completed reconstruction (photo / parametric / AR) as a reusable part, with list/detail/edit API + web UI, feeding a future brick planner.

**Architecture:** A new 4th table `parts` holds a 1:1 snapshot of each completed capture (FK → capture + GLB asset), carrying review `status` (`pending`/`verified`/`rejected`) and curation metadata (`name`/`notes`/`color`). A best-effort hook in the Celery worker's shared finalize tail auto-upserts the part on job completion. Three new endpoints under `/api/v1/library` (list / detail / PATCH) back two new React routes (`/library`, `/library/:id`). Reuses the existing `/assets/{id}` presign flow and the R3F `<Viewer>`.

**Tech Stack:** FastAPI + SQLAlchemy 2 async + Alembic + Pydantic v2 (backend); Celery (worker hook); React + TypeScript + Vite + Tailwind + react-three-fiber (web); pytest + vitest + Playwright (tests).

**Spec:** `docs/superpowers/specs/2026-06-14-part-library-foundation-design.md` (commit `890b8a4`).

**Conventions observed from the codebase:**
- Backend lives at `apps/api/src/` (src layout). Run tests: `cd apps/api && uv run --project . pytest <path> -v`.
- ORM models use `db.base` factory helpers (`make_uuid_pk`, `make_created_at`, `make_updated_at`).
- API errors subclass `core.errors.AppError` (auto-mapped to the `{"error": {...}}` envelope by `app/main.py`).
- Routers are registered in `apps/api/src/api/v1/__init__.py`.
- pytest fixtures: `app_client` (httpx AsyncClient), session-scoped Postgres/Redis/MinIO, function-scoped table truncation (`conftest.py`).
- Web api client uses the shared `request<T>()` helper in `apps/web/src/lib/api.ts`. Component tests use `vi.mock("@lib/api")`; `api.test.ts` uses `msw`.
- E2E specs live at repo-root `tests/e2e/*.spec.ts`, mock the API with `page.route()`, and run against `vite preview` on port 4173.

---

## File Structure

**Backend (create):**
- `apps/api/alembic/versions/0005_create_parts.py` — additive migration: create `parts` table.
- `apps/api/src/services/part_promoter.py` — `promote_capture_to_part()` upsert service.
- `apps/api/src/api/v1/library.py` — `/api/v1/library` router (GET list, GET detail, PATCH).
- `apps/api/tests/test_part_promoter.py` — promoter unit + idempotency tests.
- `apps/api/tests/test_library.py` — endpoint tests.

**Backend (modify):**
- `apps/api/src/db/models.py` — add `Part` ORM model + `Capture.part` relationship.
- `apps/api/src/models/schemas.py` — add `LibraryPartRead` + `LibraryPartUpdate`.
- `apps/api/src/core/errors.py` — add `PartNotFound`.
- `apps/api/src/api/v1/__init__.py` — register `library.router`.
- `apps/api/src/workers/tasks/reconstruct.py` — call promoter in `_finalize_mesh_pipeline`.
- `apps/api/tests/conftest.py` — add `parts` to the truncation statement.

**Web (create):**
- `apps/web/src/features/library/LibraryList.tsx`
- `apps/web/src/features/library/LibraryDetail.tsx`
- `apps/web/src/features/library/PartBadge.tsx`
- `apps/web/src/features/library/LibraryList.test.tsx`
- `apps/web/src/features/library/LibraryDetail.test.tsx`
- `tests/e2e/library.spec.ts`

**Web (modify):**
- `apps/web/src/lib/api.ts` — `LibraryPart` type + `listLibraryParts` / `getLibraryPart` / `updateLibraryPart`.
- `apps/web/src/lib/api.test.ts` — tests for the 3 new helpers.
- `apps/web/src/App.tsx` — routes `/library`, `/library/:id`.
- `apps/web/src/components/AppHeader.tsx` — nav entry.
- `apps/web/src/index.css` — `.badge-verified`, `.badge-rejected` classes.

---

## Task 1: `parts` table — ORM model + migration

**Files:**
- Modify: `apps/api/src/db/models.py`
- Create: `apps/api/alembic/versions/0005_create_parts.py`
- Modify: `apps/api/tests/conftest.py:332-334` (truncation statement)
- Test: `apps/api/tests/test_part_promoter.py` (new file, model round-trip test only in this task)

- [ ] **Step 1: Add the `Part` ORM model + `Capture.part` relationship**

In `apps/api/src/db/models.py`, add the `Part` class after the `Asset` class (before `__all__`):

```python
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
```

Add the back-reference inside the `Capture` class, right after the existing `jobs` relationship (line ~97):

```python
    part: Mapped[Part | None] = relationship(
        back_populates="capture", uselist=False, lazy="selectin"
    )
```

Update the `__all__` at the bottom of the file:

```python
__all__ = ["Asset", "Capture", "Job", "Part"]
```

(All imports needed — `ForeignKey`, `Index`, `Integer`, `String`, `Text`, `JSONB`, `PG_UUID`, `Any`, `datetime`, `UUID`, `relationship`, `mapped_column`, `Mapped` — are already imported at the top of `models.py`.)

- [ ] **Step 2: Write the Alembic migration**

Create `apps/api/alembic/versions/0005_create_parts.py`:

```python
"""create parts table (issue #5 — reusable library part)

Revision ID: 0005_create_parts
Revises: 0004_captures_ar_recognized
Create Date: (filled in by alembic at generation time)

Additive only: creates the ``parts`` table (1:1 with a completed capture).
No existing table is touched, so all prior rows are unaffected.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_create_parts"
down_revision: str | Sequence[str] | None = "0004_captures_ar_recognized"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "parts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capture_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_mode", sa.String(length=32), nullable=False),
        sa.Column("system", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=True),
        sa.Column("units_x", sa.Integer(), nullable=True),
        sa.Column("units_y", sa.Integer(), nullable=True),
        sa.Column("derived_spec_mm", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("color", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["capture_id"], ["captures.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_parts_capture_id", "parts", ["capture_id"], unique=True)
    op.create_index("ix_parts_status", "parts", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_parts_status", table_name="parts")
    op.drop_index("ix_parts_capture_id", table_name="parts")
    op.drop_table("parts")
```

- [ ] **Step 3: Add `parts` to the conftest truncation statement**

In `apps/api/tests/conftest.py`, change the TRUNCATE line (currently `TRUNCATE TABLE assets, jobs, captures RESTART IDENTITY CASCADE`) to include `parts` first:

```python
            await session.execute(
                text("TRUNCATE TABLE parts, assets, jobs, captures RESTART IDENTITY CASCADE")
            )
```

- [ ] **Step 4: Write the failing model round-trip test**

Create `apps/api/tests/test_part_promoter.py` with this first test:

```python
"""Tests for the parts library: ORM model round-trip + promoter service."""
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
        assert part.status == "pending"  # server default
        assert part.source_mode == "parametric_block"
        assert part.asset_id == asset_id
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `cd apps/api && uv run --project . pytest tests/test_part_promoter.py::test_part_model_round_trip -v`
Expected: FAIL — `ImportError: cannot import name 'Part'` OR `relation "parts" does not exist` until the migration is applied. (The session-scoped `_apply_migrations` fixture runs `alembic upgrade head`, so once Steps 1–2 are in place and the model imports, the table is created.)

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd apps/api && uv run --project . pytest tests/test_part_promoter.py::test_part_model_round_trip -v`
Expected: PASS. If it fails with "relation parts does not exist", confirm the migration filename + `down_revision` chain and that `_apply_migrations` ran `upgrade head`.

- [ ] **Step 7: Verify the migration is reversible**

Run: `cd apps/api && uv run --project . alembic downgrade -1 && uv run --project . alembic upgrade head`
Expected: both commands exit 0 (table dropped then recreated).

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/db/models.py apps/api/alembic/versions/0005_create_parts.py apps/api/tests/conftest.py apps/api/tests/test_part_promoter.py
git commit -m "feat(api): add parts table for the reusable library (issue #5)"
```

---

## Task 2: Pydantic schemas + `PartNotFound` error

**Files:**
- Modify: `apps/api/src/core/errors.py`
- Modify: `apps/api/src/models/schemas.py`
- Test: covered by Task 5's endpoint tests (no standalone test — these are passive data classes; validating them in isolation duplicates the endpoint tests).

- [ ] **Step 1: Add the `PartNotFound` error**

In `apps/api/src/core/errors.py`, add after `CaptureNotFound`:

```python
class PartNotFound(AppError):
    code = "PART_NOT_FOUND"
    http_status = 404
```

And add `"PartNotFound"` to `__all__` (keep it alphabetical):

```python
__all__ = [
    "AppError",
    "AssetNotFound",
    "CaptureInvalid",
    "CaptureNotFound",
    "JobNotFound",
    "PartNotFound",
    "ReconstructFailed",
]
```

- [ ] **Step 2: Add the schemas**

In `apps/api/src/models/schemas.py`, add before the `# Health` section:

```python
# ---------------------------------------------------------------------------
# Library part (issue #5)
# ---------------------------------------------------------------------------
class LibraryPartRead(BaseModel):
    """Response shape for ``/library`` list + detail."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    part_id: UUID = Field(validation_alias="id")
    capture_id: UUID
    #: GLB asset id — the web fetches the presigned URL via GET /assets/{id}.
    asset_id: UUID | None = None
    #: photo / parametric_block / ar_recognized (snapshot of capture.mode).
    source_mode: str
    system: str | None = None
    kind: str | None = None
    units_x: int | None = None
    units_y: int | None = None
    derived_spec_mm: dict[str, Any] | None = None
    color: str | None = None
    name: str
    notes: str | None = None
    #: pending / verified / rejected.
    status: str
    created_at: datetime
    updated_at: datetime


class LibraryPartUpdate(BaseModel):
    """Request body for ``PATCH /library/{id}`` — partial update."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    notes: str | None = None
    status: Literal["pending", "verified", "rejected"] | None = None
```

Add both names to the `__all__` list at the bottom of `schemas.py` (keep alphabetical ordering near the existing entries):

```python
    "JobRead",
    "LibraryPartRead",
    "LibraryPartUpdate",
    "NeedsMeasurement",
```

(`Any`, `Literal`, `UUID`, `datetime`, `BaseModel`, `ConfigDict`, `Field` are already imported at the top of `schemas.py`.)

- [ ] **Step 3: Verify it imports cleanly**

Run: `cd apps/api && uv run --project . python -c "from models.schemas import LibraryPartRead, LibraryPartUpdate; from core.errors import PartNotFound; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add apps/api/src/core/errors.py apps/api/src/models/schemas.py
git commit -m "feat(api): add library part schemas + PartNotFound error"
```

---

## Task 3: `part_promoter` service

**Files:**
- Create: `apps/api/src/services/part_promoter.py`
- Test: `apps/api/tests/test_part_promoter.py` (append)

- [ ] **Step 1: Write the failing promoter tests**

Append to `apps/api/tests/test_part_promoter.py`:

```python
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
    assert part.name == "feile-brick-2x2"  # defaults to capture.part_id
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
    assert part.name == "photo-scan-1"


async def test_promote_is_idempotent_and_preserves_user_edits() -> None:
    from sqlalchemy import select

    from services.part_promoter import promote_capture_to_part

    capture_id, asset_id = await _seed_capture(mode="ar_recognized", system="feile", kind="brick", units_x=2, units_y=4)
    await promote_capture_to_part(capture_id, asset_id)

    # Simulate the user editing the part.
    factory = async_session_factory()
    async with factory() as session:
        part = (await session.scalars(select(Part).where(Part.capture_id == capture_id))).one()
        part.name = "renamed-by-user"
        part.notes = "looks good"
        part.status = "verified"
        await session.commit()

    # Re-promote with a NEW asset id (worker re-run).
    new_asset_id = uuid.uuid4()
    async with factory() as session:
        from db.models import Asset, Job

        job = (await session.scalars(select(Job).where(Job.capture_id == capture_id))).first()
        session.add(Asset(id=new_asset_id, job_id=job.id, kind="mesh_gltf", storage_key="recon/x/mesh.glb"))
        await session.commit()
    await promote_capture_to_part(capture_id, new_asset_id)

    part = await _load_part(capture_id)
    assert part is not None
    # asset_id updated...
    assert part.asset_id == new_asset_id
    # ...but user edits preserved.
    assert part.name == "renamed-by-user"
    assert part.notes == "looks good"
    assert part.status == "verified"
    # Exactly one part row (no duplicate from the second promote).
    async with factory() as session:
        count = len((await session.scalars(select(Part).where(Part.capture_id == capture_id))).all())
    assert count == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/api && uv run --project . pytest tests/test_part_promoter.py -v -k "promote"`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.part_promoter'`.

- [ ] **Step 3: Implement the promoter service**

Create `apps/api/src/services/part_promoter.py`:

```python
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

from sqlalchemy import select

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

        existing = (
            await session.scalars(select(Part).where(Part.capture_id == capture_id))
        ).one_or_none()

        if existing is None:
            session.add(
                Part(
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
            )
        else:
            # Re-run: refresh the asset + spec snapshot, preserve curation.
            existing.asset_id = asset_id
            existing.source_mode = capture.mode
            existing.system = capture.system
            existing.kind = capture.kind
            existing.units_x = capture.units_x
            existing.units_y = capture.units_y
            existing.derived_spec_mm = capture.derived_spec_mm

        await session.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/api && uv run --project . pytest tests/test_part_promoter.py -v`
Expected: PASS (all 4 tests: model round-trip + 3 promoter tests).

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/services/part_promoter.py apps/api/tests/test_part_promoter.py
git commit -m "feat(api): add part_promoter service (idempotent capture->part upsert)"
```

---

## Task 4: Wire the promoter into the worker (auto-promote on completion)

**Files:**
- Modify: `apps/api/src/workers/tasks/reconstruct.py` (inside `_finalize_mesh_pipeline`, ~line 649)
- Test: `apps/api/tests/test_reconstruction_parametric.py` (append an integration assertion) — see Step 1.

- [ ] **Step 1: Write the failing integration test**

Append to `apps/api/tests/test_reconstruction_parametric.py` (this file already exercises the parametric worker path end-to-end; we add a test asserting a `Part` row appears after the worker finalizes). First, open the file and note the existing helper that POSTs a parametric block + runs the worker; mirror its setup. Add:

```python
def test_parametric_capture_auto_promotes_to_part(work_in_tmp, app_client) -> None:
    """After the parametric worker finalizes, a Part row exists linked to the capture + GLB asset."""
    import asyncio
    import uuid as _uuid

    from db.models import Asset, Job, Part
    from db.session import async_session_factory
    from workers.tasks.reconstruct import reconstruct

    # POST a parametric block (multipart form, no photos).
    resp = asyncio.get_event_loop().run_until_complete(
        app_client.post(
            "/api/v1/parametric-blocks",
            data={
                "part_id": "promote-feile-2x2",
                "system": "feile",
                "kind": "brick",
                "units_x": "2",
                "units_y": "2",
                "raw_measurements_mm": '{"outer_pitch_mm":33.4,"inner_pitch_mm":6.6,"stud_diameter_mm":9.4,"brick_height_net_mm":19.2,"brick_height_total_mm":24.6}',
            },
        )
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    capture_id = body["capture_id"]

    result = reconstruct.apply(args=[capture_id])
    assert result.successful() or result.state == "SUCCESS", result.state

    async def _load() -> tuple[Part | None, list[Asset]]:
        from sqlalchemy import select

        f = async_session_factory()
        async with f() as session:
            part = (await session.scalars(select(Part).where(Part.capture_id == _uuid.UUID(capture_id)))).one_or_none()
            job = (await session.scalars(select(Job).where(Job.capture_id == _uuid.UUID(capture_id)))).first()
            assets = list((await session.scalars(select(Asset).where(Asset.job_id == job.id))).all()) if job else []
            return part, assets

    part, assets = asyncio.run(_load())
    assert part is not None, "worker should have auto-promoted the capture to a Part"
    assert part.source_mode == "parametric_block"
    assert part.system == "feile" and part.kind == "brick"
    assert part.units_x == 2 and part.units_y == 2
    assert part.status == "pending"
    assert part.name == "promote-feile-2x2"
    assert len(assets) == 1
    assert part.asset_id == assets[0].id
```

> Note: if `test_reconstruction_parametric.py` does not already define a `work_in_tmp` fixture, copy the 3-line `work_in_tmp` fixture from `apps/api/tests/test_ar_captures.py:157-160` into this test file. If the parametric POST field names differ from the above, match the existing passing tests in this file (they are the source of truth for the multipart contract).

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && uv run --project . pytest tests/test_reconstruction_parametric.py::test_parametric_capture_auto_promotes_to_part -v`
Expected: FAIL — `assert part is not None` (the worker doesn't promote yet).

- [ ] **Step 3: Call the promoter from `_finalize_mesh_pipeline`**

In `apps/api/src/workers/tasks/reconstruct.py`, locate the end of `_finalize_mesh_pipeline` — right after `asyncio.run(_write_asset())` (~line 649) and before the "Stage 6: final progress + completed event" block. Insert:

```python
    # ---- Auto-promote into the reusable library (issue #5, best-effort) --
    # The GLB is already persisted as an asset; promotion is an additive
    # post-completion action. If it fails we log and continue — we must NOT
    # turn a successful reconstruction into a failed job. A worker re-run
    # upserts idempotently on capture_id.
    try:
        from services.part_promoter import promote_capture_to_part

        asyncio.run(promote_capture_to_part(capture_uuid, asset_id))
    except Exception as exc:  # noqa: BLE001 — best-effort by design
        logger.warning("reconstruct: auto-promote to part failed for capture %s: %s", capture_uuid, exc)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run --project . pytest tests/test_reconstruction_parametric.py::test_parametric_capture_auto_promotes_to_part -v`
Expected: PASS.

- [ ] **Step 5: Run the worker test suite to confirm no regressions**

Run: `cd apps/api && uv run --project . pytest tests/test_reconstruction_parametric.py tests/test_ar_recognized_worker.py -v`
Expected: all PASS (existing worker tests unaffected; the promote step is best-effort and additive).

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/workers/tasks/reconstruct.py apps/api/tests/test_reconstruction_parametric.py
git commit -m "feat(api): auto-promote completed captures into the part library"
```

---

## Task 5: `/api/v1/library` router (list / detail / PATCH)

**Files:**
- Create: `apps/api/src/api/v1/library.py`
- Modify: `apps/api/src/api/v1/__init__.py`
- Test: `apps/api/tests/test_library.py`

- [ ] **Step 1: Write the failing endpoint tests**

Create `apps/api/tests/test_library.py`:

```python
"""Endpoint tests for /api/v1/library."""
from __future__ import annotations

import uuid

from db.models import Asset, Capture, Job, Part
from db.session import async_session_factory


async def _make_part(*, status: str = "pending", name: str = "p", source_mode: str = "parametric_block") -> uuid.UUID:
    capture_id = uuid.uuid4()
    factory = async_session_factory()
    async with factory() as session:
        session.add(
            Capture(id=capture_id, part_id=name, status="completed", image_count=0, image_keys=[], mode=source_mode)
        )
        job = Job(capture_id=capture_id, kind="reconstruct", status="completed", progress=100)
        session.add(job)
        await session.flush()
        asset = Asset(job_id=job.id, kind="mesh_gltf", storage_key=f"recon/{job.id}/mesh.glb", size_bytes=9000)
        session.add(asset)
        await session.flush()
        part = Part(
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


async def test_patch_library_part_404(app_client) -> None:
    resp = await app_client.patch(f"/api/v1/library/{uuid.uuid4()}", json={"name": "x"})
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "PART_NOT_FOUND"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/api && uv run --project . pytest tests/test_library.py -v`
Expected: FAIL — 404s for every route (the router isn't registered yet), so the status-code asserts fail.

- [ ] **Step 3: Implement the router**

Create `apps/api/src/api/v1/library.py`:

```python
"""``/api/v1/library`` — list / read / update reusable library parts (issue #5)."""
from __future__ import annotations

import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status
from sqlalchemy import select

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
    stmt = select(Part).order_by(Part.created_at.desc()).limit(limit)
    if status_filter is not None:
        stmt = stmt.where(Part.status == status_filter)
    parts = list((await session.scalars(stmt)).all())
    return [LibraryPartRead.model_validate(p) for p in parts]


@router.get("/{part_id}", response_model=LibraryPartRead, summary="Read a library part")
async def get_part(part_id: uuid.UUID, session: DBSessionDep) -> LibraryPartRead:
    part = await session.get(Part, part_id)
    if part is None:
        raise PartNotFound(f"part {part_id} not found")
    return LibraryPartRead.model_validate(part)


@router.patch("/{part_id}", response_model=LibraryPartRead, summary="Update a library part")
async def update_part(
    part_id: uuid.UUID,
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
```

- [ ] **Step 4: Register the router**

In `apps/api/src/api/v1/__init__.py`, add `library` to the import and include it:

```python
from api.v1 import ar_captures, assets, captures, health, jobs, library, parametric_blocks
```

and after the `ar_captures` include:

```python
# issue #5 library: durable reusable parts promoted from completed captures.
api_v1_router.include_router(library.router)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd apps/api && uv run --project . pytest tests/test_library.py -v`
Expected: all 7 PASS.

- [ ] **Step 6: Run the full backend suite for regressions**

Run: `cd apps/api && uv run --project . pytest tests/test_captures.py tests/test_assets.py tests/test_library.py tests/test_part_promoter.py -v`
Expected: all PASS (existing capture/asset flows unaffected).

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/api/v1/library.py apps/api/src/api/v1/__init__.py apps/api/tests/test_library.py
git commit -m "feat(api): add /api/v1/library list/detail/patch endpoints"
```

---

## Task 6: Web API client — types + helpers

**Files:**
- Modify: `apps/web/src/lib/api.ts`
- Test: `apps/web/src/lib/api.test.ts`

- [ ] **Step 1: Write the failing helper tests**

In `apps/web/src/lib/api.test.ts`, add `listLibraryParts`, `getLibraryPart`, `updateLibraryPart` to the imports from `@lib/api`, then add these msw handlers inside the existing `setupServer(...)` call (add to the handler list) and a new `describe`:

```ts
// --- library handlers (add inside setupServer(...)) ---
http.get("*/api/v1/library", ({ request }) => {
  const url = new URL(request.url);
  const status = url.searchParams.get("status");
  const all = [
    { part_id: "p1", capture_id: "c1", asset_id: "a1", source_mode: "parametric_block",
      system: "feile", kind: "brick", units_x: 2, units_y: 2, derived_spec_mm: null,
      color: null, name: "alpha", notes: null, status: "pending",
      created_at: "<PRIVATE_DATE>", updated_at: "<PRIVATE_DATE>" },
    { part_id: "p2", capture_id: "c2", asset_id: "a2", source_mode: "ar_recognized",
      system: "feile", kind: "plate", units_x: 1, units_y: 4, derived_spec_mm: null,
      color: null, name: "beta", notes: null, status: "verified",
      created_at: "<PRIVATE_DATE>", updated_at: "<PRIVATE_DATE>" },
  ];
  return HttpResponse.json(status ? all.filter((p) => p.status === status) : all);
}),
http.get("*/api/v1/library/p1", () =>
  HttpResponse.json({
    part_id: "p1", capture_id: "c1", asset_id: "a1", source_mode: "parametric_block",
    system: "feile", kind: "brick", units_x: 2, units_y: 2, derived_spec_mm: null,
    color: null, name: "alpha", notes: null, status: "pending",
    created_at: "<PRIVATE_DATE>", updated_at: "<PRIVATE_DATE>",
  }),
),
http.patch("*/api/v1/library/p1", async ({ request }) => {
  const body = (await request.json()) as Record<string, unknown>;
  return HttpResponse.json({
    part_id: "p1", capture_id: "c1", asset_id: "a1", source_mode: "parametric_block",
    system: "feile", kind: "brick", units_x: 2, units_y: 2, derived_spec_mm: null,
    color: null, name: (body.name as string) ?? "alpha", notes: (body.notes as string) ?? null,
    status: (body.status as string) ?? "pending",
    created_at: "<PRIVATE_DATE>", updated_at: "<PRIVATE_DATE>",
  });
}),
```

```ts
describe("library api", () => {
  it("lists parts", async () => {
    const parts = await listLibraryParts();
    expect(parts).toHaveLength(2);
    expect(parts[0].name).toBe("alpha");
  });

  it("filters parts by status", async () => {
    const parts = await listLibraryParts("verified");
    expect(parts).toHaveLength(1);
    expect(parts[0].name).toBe("beta");
  });

  it("gets a part by id", async () => {
    const part = await getLibraryPart("p1");
    expect(part.system).toBe("feile");
  });

  it("updates a part", async () => {
    const part = await updateLibraryPart("p1", { name: "renamed", status: "verified" });
    expect(part.name).toBe("renamed");
    expect(part.status).toBe("verified");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/web && pnpm exec vitest run src/lib/api.test.ts`
Expected: FAIL — `listLibraryParts is not exported` (import error).

- [ ] **Step 3: Add the types + helpers**

In `apps/web/src/lib/api.ts`, add the type after the `AssetInfo` interface:

```ts
export type PartStatus = "pending" | "verified" | "rejected";

export interface LibraryPart {
  part_id: string;
  capture_id: string;
  asset_id: string | null;
  source_mode: string;
  system: string | null;
  kind: string | null;
  units_x: number | null;
  units_y: number | null;
  derived_spec_mm: Record<string, number> | null;
  color: string | null;
  name: string;
  notes: string | null;
  status: PartStatus;
  created_at: string;
  updated_at: string;
}
```

and add the helpers near `getAssetUrl` (before the `JobStage` type):

```ts
export async function listLibraryParts(
  status?: PartStatus,
  limit = 20,
  signal?: AbortSignal,
): Promise<LibraryPart[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (status) params.set("status", status);
  return request<LibraryPart[]>(`/library?${params.toString()}`, { method: "GET" }, signal);
}

export async function getLibraryPart(id: string, signal?: AbortSignal): Promise<LibraryPart> {
  return request<LibraryPart>(`/library/${encodeURIComponent(id)}`, { method: "GET" }, signal);
}

export async function updateLibraryPart(
  id: string,
  patch: { name?: string; notes?: string; status?: PartStatus },
  signal?: AbortSignal,
): Promise<LibraryPart> {
  return request<LibraryPart>(
    `/library/${encodeURIComponent(id)}`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) },
    signal,
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/web && pnpm exec vitest run src/lib/api.test.ts`
Expected: all PASS (existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/api.ts apps/web/src/lib/api.test.ts
git commit -m "feat(web): add library part api client (list/get/update)"
```

---

## Task 7: `PartBadge` component + badge CSS

**Files:**
- Create: `apps/web/src/features/library/PartBadge.tsx`
- Modify: `apps/web/src/index.css` (add `.badge-verified`, `.badge-rejected`)
- Test: none standalone (covered by LibraryList/LibraryDetail tests in Tasks 8–9).

- [ ] **Step 1: Add the two badge classes**

In `apps/web/src/index.css`, after the existing `.badge-failed` rule (~line 113), add:

```css
  .badge-verified {
    @apply bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300;
  }
  .badge-rejected {
    @apply bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300;
  }
```

> If `.badge-completed` / `.badge-failed` use project theme tokens (e.g. `text-ok` / `text-err`) rather than raw Tailwind palette colors, match that style instead — open `index.css` lines 101–115 and copy the pattern (`.badge` base + a status modifier). The goal is a small colored pill consistent with the existing job badges.

- [ ] **Step 2: Create the `PartBadge` component**

Create `apps/web/src/features/library/PartBadge.tsx`:

```tsx
import type { PartStatus } from "@lib/api";

const STATUS_LABEL: Record<PartStatus, string> = {
  pending: "待核验",
  verified: "已核验",
  rejected: "已拒绝",
};

const SOURCE_LABEL: Record<string, string> = {
  photo: "照片扫描",
  parametric_block: "卡尺测量",
  ar_recognized: "AR 识别",
};

export function StatusBadge({ status }: { status: PartStatus }) {
  return (
    <span className={`badge badge-${status}`} data-testid="part-status-badge" data-status={status}>
      {STATUS_LABEL[status]}
    </span>
  );
}

export function SourceBadge({ source }: { source: string }) {
  return (
    <span className="badge badge-pending" data-testid="part-source-badge" data-source={source}>
      {SOURCE_LABEL[source] ?? source}
    </span>
  );
}
```

> Note: this `StatusBadge` is library-specific and lives in `features/library/`; it does NOT replace `components/StatusBadge.tsx` (which is keyed to job statuses). Import it as `import { StatusBadge as PartStatusBadge, SourceBadge } from "./PartBadge"` where needed to avoid name collisions.

- [ ] **Step 3: Verify the web build still type-checks**

Run: `cd apps/web && pnpm exec tsc --noEmit`
Expected: no new type errors.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/features/library/PartBadge.tsx apps/web/src/index.css
git commit -m "feat(web): add PartBadge (status + source) for the library"
```

---

## Task 8: `LibraryList` page + route + header nav

**Files:**
- Create: `apps/web/src/features/library/LibraryList.tsx`
- Create: `apps/web/src/features/library/LibraryList.test.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/components/AppHeader.tsx`

- [ ] **Step 1: Write the failing component test**

Create `apps/web/src/features/library/LibraryList.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { LibraryList } from "@features/library/LibraryList";

vi.mock("@lib/api", async () => {
  const actual = await vi.importActual("@lib/api");
  return { ...actual, listLibraryParts: vi.fn() };
});

import { listLibraryParts, type LibraryPart } from "@lib/api";
const listMock = vi.mocked(listLibraryParts);

function part(over: Partial<LibraryPart>): LibraryPart {
  return {
    part_id: "p", capture_id: "c", asset_id: "a", source_mode: "parametric_block",
    system: "feile", kind: "brick", units_x: 2, units_y: 2, derived_spec_mm: null,
    color: null, name: "name", notes: null, status: "pending",
    created_at: "<PRIVATE_DATE>", updated_at: "<PRIVATE_DATE>", ...over,
  };
}

describe("LibraryList", () => {
  beforeEach(() => listMock.mockReset());
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("renders parts returned by the API", async () => {
    listMock.mockResolvedValue([part({ part_id: "p1", name: "alpha" }), part({ part_id: "p2", name: "beta", status: "verified" })]);
    render(<MemoryRouter><LibraryList /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("alpha")).toBeInTheDocument());
    expect(screen.getByText("beta")).toBeInTheDocument();
  });

  it("defaults to the pending tab and loads rejected only on its own tab", async () => {
    // Each tab filters by exactly one status (server-side). Default = pending.
    listMock.mockImplementation(async (status?: string) => {
      if (status === "rejected") return [part({ part_id: "pr", name: "trashed", status: "rejected" })];
      if (status === "verified") return [part({ part_id: "pv", name: "blessed", status: "verified" })];
      return [part({ part_id: "p1", name: "alpha", status: "pending" })]; // status === "pending"
    });
    render(<MemoryRouter><LibraryList /></MemoryRouter>);
    // Default tab loads pending.
    await waitFor(() => expect(screen.getByText("alpha")).toBeInTheDocument());
    expect(listMock).toHaveBeenCalledWith("pending", expect.any(Number), expect.anything());
    expect(screen.queryByText("trashed")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("library-tab-rejected"));
    await waitFor(() => expect(screen.getByText("trashed")).toBeInTheDocument());
    expect(screen.queryByText("alpha")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/web && pnpm exec vitest run src/features/library/LibraryList.test.tsx`
Expected: FAIL — cannot resolve `@features/library/LibraryList`.

- [ ] **Step 3: Implement `LibraryList`**

Create `apps/web/src/features/library/LibraryList.tsx`:

```tsx
import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { listLibraryParts, type LibraryPart, type PartStatus } from "@lib/api";
import { formatTimeAgo } from "@lib/format";
import { StatusBadge as PartStatusBadge, SourceBadge } from "./PartBadge";

// Each tab filters by exactly one status (server-side). Default = pending, so
// rejected parts are hidden until the user opens the 已拒绝 tab.
const TABS: ReadonlyArray<{ id: PartStatus; label: string }> = [
  { id: "pending", label: "待核验" },
  { id: "verified", label: "已核验" },
  { id: "rejected", label: "已拒绝" },
];

export function LibraryList() {
  const [tab, setTab] = useState<PartStatus>("pending");
  const [parts, setParts] = useState<LibraryPart[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    listLibraryParts(tab, 50, controller.signal)
      .then((rows) => setParts(rows))
      .catch(() => setParts([]))
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [tab]);

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-txt-primary">零件库</h1>
        <span className="text-xs text-txt-secondary">{parts.length} 个零件</span>
      </div>

      <div className="mb-4 flex gap-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            data-testid={`library-tab-${t.id}`}
            aria-pressed={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-full px-3 py-1 text-xs ${
              tab === t.id ? "bg-accent text-white dark:text-page" : "bg-card text-txt-secondary hover:text-txt-primary"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="card text-sm text-txt-secondary">加载中...</div>
      ) : parts.length === 0 ? (
        <div className="card flex flex-col items-center gap-2 py-16 text-center">
          <h2 className="text-base font-medium text-txt-primary">这里还没有零件</h2>
          <p className="text-sm text-txt-secondary">完成一次采集或建模后，零件会自动出现在这里</p>
        </div>
      ) : (
        <ul className="space-y-2.5">
          {parts.map((p) => (
            <li key={p.part_id}>
              <Link to={`/library/${p.part_id}`} className="card group block no-underline">
                <div className="flex items-start justify-between">
                  <div className="text-sm font-medium text-txt-primary">{p.name}</div>
                  <PartStatusBadge status={p.status} />
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-txt-secondary">
                  <SourceBadge source={p.source_mode} />
                  {p.system ? <span>{p.system}</span> : null}
                  {p.kind ? <span>· {p.kind}</span> : null}
                  {p.units_x && p.units_y ? <span>· {p.units_x}×{p.units_y}</span> : null}
                  <span>· {formatTimeAgo(p.created_at)}</span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Add the route + header nav**

In `apps/web/src/App.tsx`, add the import and routes:

```tsx
import { LibraryList } from "@features/library/LibraryList";
import { LibraryDetail } from "@features/library/LibraryDetail";
```

```tsx
          <Route path="/library" element={<LibraryList />} />
          <Route path="/library/:id" element={<LibraryDetail />} />
```

(Add these inside `<Routes>` before the `<Route path="*" ... />` catch-all. `LibraryDetail` is created in Task 9 — if you run the dev server between tasks, temporarily comment its route + import, or do Task 9 before starting the app.)

In `apps/web/src/components/AppHeader.tsx`, add to `NAV_ITEMS`:

```tsx
  { path: "/library", label: "零件库", testId: "header-library-link" },
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd apps/web && pnpm exec vitest run src/features/library/LibraryList.test.tsx`
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/library/LibraryList.tsx apps/web/src/features/library/LibraryList.test.tsx apps/web/src/App.tsx apps/web/src/components/AppHeader.tsx
git commit -m "feat(web): add /library list page + header nav"
```

---

## Task 9: `LibraryDetail` page

**Files:**
- Create: `apps/web/src/features/library/LibraryDetail.tsx`
- Create: `apps/web/src/features/library/LibraryDetail.test.tsx`

- [ ] **Step 1: Write the failing component test**

Create `apps/web/src/features/library/LibraryDetail.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { LibraryDetail } from "@features/library/LibraryDetail";

vi.mock("@lib/api", async () => {
  const actual = await vi.importActual("@lib/api");
  return { ...actual, getLibraryPart: vi.fn(), updateLibraryPart: vi.fn() };
});

// The Viewer pulls in react-three-fiber/WebGL; stub it for jsdom.
vi.mock("@features/viewer/Viewer", () => ({ Viewer: () => <div data-testid="viewer-stub" /> }));

import { getLibraryPart, updateLibraryPart, type LibraryPart } from "@lib/api";
const getMock = vi.mocked(getLibraryPart);
const updateMock = vi.mocked(updateLibraryPart);

const base: LibraryPart = {
  part_id: "p1", capture_id: "c1", asset_id: "a1", source_mode: "parametric_block",
  system: "feile", kind: "brick", units_x: 2, units_y: 2, derived_spec_mm: { unit_mm: 16 },
  color: null, name: "alpha", notes: null, status: "pending",
  created_at: "<PRIVATE_DATE>", updated_at: "<PRIVATE_DATE>",
};

function renderAt(path = "/library/p1") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/library/:id" element={<LibraryDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("LibraryDetail", () => {
  beforeEach(() => { getMock.mockReset(); updateMock.mockReset(); });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("renders the part + GLB viewer", async () => {
    getMock.mockResolvedValue(base);
    renderAt();
    await waitFor(() => expect(screen.getByDisplayValue("alpha")).toBeInTheDocument());
    expect(screen.getByTestId("viewer-stub")).toBeInTheDocument();
    expect(screen.getByText(/feile/)).toBeInTheDocument();
  });

  it("marks the part verified", async () => {
    getMock.mockResolvedValue(base);
    updateMock.mockResolvedValue({ ...base, status: "verified" });
    renderAt();
    await waitFor(() => expect(screen.getByDisplayValue("alpha")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("part-verify-btn"));
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith("p1", { status: "verified" }, expect.anything()));
  });

  it("saves an edited name", async () => {
    getMock.mockResolvedValue(base);
    updateMock.mockResolvedValue({ ...base, name: "renamed" });
    renderAt();
    await waitFor(() => expect(screen.getByDisplayValue("alpha")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("part-name-input"), { target: { value: "renamed" } });
    fireEvent.click(screen.getByTestId("part-save-btn"));
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith("p1", expect.objectContaining({ name: "renamed" }), expect.anything()));
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/web && pnpm exec vitest run src/features/library/LibraryDetail.test.tsx`
Expected: FAIL — cannot resolve `@features/library/LibraryDetail`.

- [ ] **Step 3: Implement `LibraryDetail`**

Create `apps/web/src/features/library/LibraryDetail.tsx`:

```tsx
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getLibraryPart, updateLibraryPart, type LibraryPart, type PartStatus } from "@lib/api";
import { Viewer } from "@features/viewer/Viewer";
import { StatusBadge as PartStatusBadge, SourceBadge } from "./PartBadge";

export function LibraryDetail() {
  const { id } = useParams();
  const [part, setPart] = useState<LibraryPart | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    setError(null);
    getLibraryPart(id, controller.signal)
      .then((p) => {
        setPart(p);
        setName(p.name);
        setNotes(p.notes ?? "");
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "加载零件失败");
      });
    return () => controller.abort();
  }, [id]);

  async function patch(patchBody: { name?: string; notes?: string; status?: PartStatus }) {
    if (!id) return;
    setSaving(true);
    try {
      const updated = await updateLibraryPart(id, patchBody, undefined);
      setPart(updated);
      setName(updated.name);
      setNotes(updated.notes ?? "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  if (!id) return null;
  if (error) return <div className="mx-auto max-w-5xl px-4 py-6"><div className="card text-sm text-err">{error}</div></div>;
  if (!part) return <div className="mx-auto max-w-5xl px-4 py-6"><div className="card text-sm text-txt-secondary">加载零件中...</div></div>;

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold text-txt-primary">{part.name}</h1>
          <PartStatusBadge status={part.status} />
        </div>
        <Link to={`/captures/${part.capture_id}`} className="btn-outline text-sm">查看来源采集</Link>
      </div>

      {part.asset_id ? (
        <div className="mb-4 h-80 overflow-hidden rounded-lg border border-line">
          <Viewer assetId={part.asset_id} />
        </div>
      ) : (
        <div className="card mb-4 text-sm text-txt-secondary">没有可显示的 3D 模型</div>
      )}

      <section className="card mb-4 space-y-3">
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-txt-secondary">
          <SourceBadge source={part.source_mode} />
          {part.system ? <span>{part.system}</span> : null}
          {part.kind ? <span>· {part.kind}</span> : null}
          {part.units_x && part.units_y ? <span>· {part.units_x}×{part.units_y}</span> : null}
        </div>
        {part.derived_spec_mm ? (
          <pre className="overflow-x-auto rounded bg-surface p-2 text-[11px] text-txt-secondary">
            {JSON.stringify(part.derived_spec_mm, null, 2)}
          </pre>
        ) : null}

        <label className="block text-xs text-txt-secondary">名称
          <input
            data-testid="part-name-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full rounded border border-line bg-card px-2 py-1 text-sm text-txt-primary"
          />
        </label>
        <label className="block text-xs text-txt-secondary">备注
          <textarea
            data-testid="part-notes-input"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            className="mt-1 w-full rounded border border-line bg-card px-2 py-1 text-sm text-txt-primary"
          />
        </label>

        <div className="flex flex-wrap gap-2">
          <button type="button" data-testid="part-save-btn" disabled={saving}
            onClick={() => patch({ name, notes })} className="btn-primary text-sm">保存</button>
          <button type="button" data-testid="part-verify-btn" disabled={saving}
            onClick={() => patch({ status: "verified" })} className="btn-outline text-sm">标记已核验</button>
          <button type="button" data-testid="part-reject-btn" disabled={saving}
            onClick={() => patch({ status: "rejected" })} className="btn-outline text-sm">标记拒绝</button>
          <button type="button" data-testid="part-reset-btn" disabled={saving}
            onClick={() => patch({ status: "pending" })} className="btn-ghost text-sm">重置为待核验</button>
        </div>
      </section>
    </div>
  );
}
```

> The `patch()` helper passes `undefined` as the 3rd `signal` arg to `updateLibraryPart`; the test asserts with `expect.anything()`. The Tailwind utility classes (`btn-primary`, `btn-outline`, `btn-ghost`, `card`, `text-err`, `border-line`, `bg-surface`, `text-txt-*`) all exist — confirm against `apps/web/src/index.css` and adjust if a token name differs.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/web && pnpm exec vitest run src/features/library/LibraryDetail.test.tsx`
Expected: all 3 tests PASS.

- [ ] **Step 5: Run the full web unit suite + typecheck**

Run: `cd apps/web && pnpm exec tsc --noEmit && pnpm test`
Expected: all PASS, no type errors.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/library/LibraryDetail.tsx apps/web/src/features/library/LibraryDetail.test.tsx
git commit -m "feat(web): add /library/:id detail page with viewer + status actions"
```

---

## Task 10: Playwright E2E (mandatory)

**Files:**
- Create: `tests/e2e/library.spec.ts`

This mirrors the established repo pattern (`tests/e2e/parametric.spec.ts`): mock the API with `page.route()` and run against a `vite preview` server on port 4173. No docker stack required.

- [ ] **Step 1: Build the web app (preview serves the static build)**

Run: `cd apps/web && pnpm build`
Expected: `apps/web/dist` is produced (the preview server serves it).

- [ ] **Step 2: Write the E2E spec**

Create `tests/e2e/library.spec.ts`:

```ts
// =============================================================================
// tests/e2e/library.spec.ts — part library (issue #5) end-to-end.
// Mirrors parametric.spec.ts: mock /api/v1/library + /assets with page.route(),
// run against `vite preview` on 4173. No docker compose needed.
// Flow: open /library -> see a part -> open detail -> 标记已核验 -> badge flips.
// =============================================================================
import { test, expect, type Page } from "@playwright/test";
import path from "node:path";
import { execSync } from "node:child_process";

const SCREENSHOTS_DIR = path.join(__dirname, "screenshots");
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const PART_ID = "lib-e2e-001";

const PART = {
  part_id: PART_ID,
  capture_id: "cap-e2e-001",
  asset_id: "asset-e2e-001",
  source_mode: "parametric_block",
  system: "feile",
  kind: "brick",
  units_x: 2,
  units_y: 2,
  derived_spec_mm: { unit_mm: 16.0 },
  color: null,
  name: "E2E FEILE 2x2",
  notes: null,
  status: "pending" as "pending" | "verified" | "rejected",
  created_at: "<PRIVATE_DATE>",
  updated_at: "<PRIVATE_DATE>",
};

async function mockLibraryApi(page: Page) {
  let current = { ...PART };

  await page.route("**/api/v1/library**", async (route, request) => {
    // PATCH /api/v1/library/{id}
    if (request.method() === "PATCH") {
      const body = request.postDataJSON() as Record<string, unknown>;
      current = { ...current, ...body };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(current) });
      return;
    }
    const url = new URL(request.url());
    // GET /api/v1/library/{id} (detail)
    if (url.pathname.endsWith(`/library/${PART_ID}`)) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(current) });
      return;
    }
    // GET /api/v1/library (list)
    if (url.pathname.endsWith("/library")) {
      const status = url.searchParams.get("status");
      const rows = status && status !== current.status ? [] : [current];
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(rows) });
      return;
    }
    await route.continue();
  });

  // GET /api/v1/assets/{id} → JSON pointing at a tiny inline GLB.
  await page.route(`**/api/v1/assets/${PART.asset_id}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        asset_id: PART.asset_id,
        kind: "mesh_gltf",
        url: "data:application/octet-stream;base64,Z2xURg==",
        expires_at: "<PRIVATE_DATE>",
      }),
    });
  });
}

test.describe("part library", () => {
  test.use({ baseURL: "http://localhost:4173", viewport: { width: 1280, height: 900 } });

  let startedPreview = false;
  let previewPid: number | null = null;

  test.beforeAll(async () => {
    let alreadyUp = false;
    try {
      alreadyUp = execSync("lsof -nP -iTCP:4173 -sTCP:LISTEN", { encoding: "utf8" }).includes("LISTEN");
    } catch {
      alreadyUp = false;
    }
    if (!alreadyUp) {
      const { spawn } = await import("node:child_process");
      const proc = spawn(
        "pnpm",
        ["--filter", "@blocktool/web", "exec", "vite", "preview", "--port", "4173", "--strictPort"],
        { cwd: REPO_ROOT, stdio: "ignore", detached: true },
      );
      previewPid = proc.pid ?? null;
      startedPreview = true;
      const start = Date.now();
      while (Date.now() - start < 15_000) {
        try {
          if (execSync("lsof -nP -iTCP:4173 -sTCP:LISTEN", { encoding: "utf8" }).includes("LISTEN")) break;
        } catch { /* not up yet */ }
        await new Promise((r) => setTimeout(r, 250));
      }
    }
  });

  test.afterAll(async () => {
    if (startedPreview && previewPid) {
      try { process.kill(-previewPid, "SIGTERM"); } catch { /* gone */ }
    }
  });

  test("header link navigates to /library and lists a part", async ({ page }) => {
    await mockLibraryApi(page);
    await page.goto("/");
    await expect(page.getByTestId("header-library-link")).toBeVisible();
    await page.getByTestId("header-library-link").click();
    await expect(page).toHaveURL(/\/library$/);
    await expect(page.getByText("E2E FEILE 2x2")).toBeVisible();
  });

  test("open detail and mark verified flips the status badge", async ({ page }) => {
    await mockLibraryApi(page);
    await page.goto("/library");
    await page.getByText("E2E FEILE 2x2").click();
    await expect(page).toHaveURL(/\/library\/lib-e2e-001$/);

    const badge = page.getByTestId("part-status-badge");
    await expect(badge).toHaveAttribute("data-status", "pending");

    await page.getByTestId("part-verify-btn").click();
    await expect(badge).toHaveAttribute("data-status", "verified", { timeout: 10_000 });

    const out = path.join(SCREENSHOTS_DIR, "library-verified.png");
    await page.screenshot({ path: out, fullPage: true });
    test.info().annotations.push({ type: "screenshot", description: out });
  });
});
```

- [ ] **Step 3: Run the E2E spec**

Run: `pnpm exec playwright test tests/e2e/library.spec.ts`
Expected: both tests PASS; `tests/e2e/screenshots/library-verified.png` is written.

> If the preview server fails to start (port conflict / sandbox without `lsof`), run `cd apps/web && pnpm exec vite preview --port 4173 --strictPort` in a separate terminal first, then re-run the spec — `beforeAll` detects the already-running server and skips spawning.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/library.spec.ts
git commit -m "test(e2e): add part library list -> detail -> verify flow"
```

---

## Final Verification

- [ ] **Step 1: Full backend suite**

Run: `cd apps/api && uv run --project . pytest -v`
Expected: all tests pass (pre-existing GLB-size skips/xfails noted in README are unrelated).

- [ ] **Step 2: Full web unit suite + typecheck**

Run: `cd apps/web && pnpm exec tsc --noEmit && pnpm test`
Expected: all pass, no type errors.

- [ ] **Step 3: E2E**

Run: `pnpm exec playwright test tests/e2e/library.spec.ts`
Expected: pass + screenshot produced.

- [ ] **Step 4: Manual smoke (optional, if stack is up via `bash scripts/up.sh`)**

Submit a parametric block via `/parametric`, wait for completion, open `/library`, confirm the new part appears as `待核验`, open it, mark `已核验`, reload — status persists.

---

## Spec Coverage Check

| Spec section | Task(s) |
|---|---|
| §3 `parts` table + snapshot + migration | Task 1 |
| §3 status model (`pending`/`verified`/`rejected`) + `source_mode` | Tasks 1, 2, 7 |
| §4 auto-promote hook in `_finalize_mesh_pipeline` + best-effort + idempotent | Tasks 3, 4 |
| §4 `needs_measurement` captures don't promote (no job → never finalizes) | Implicit (hook only runs on completed jobs); no code needed |
| §5.1 `GET /library` + status filter | Task 5 |
| §5.2 `GET /library/{id}` + `PartNotFound` | Tasks 2, 5 |
| §5.3 `PATCH /library/{id}` + status validation | Task 5 |
| §5.4 `LibraryPartRead` / `LibraryPartUpdate` | Task 2 |
| §6 `/library` list + `/library/:id` detail + header entry | Tasks 7, 8, 9 |
| §6 reuse `<Viewer>` + `/assets/{id}` presign | Task 9 |
| §6 `PartBadge` (status + source), no change to job `StatusBadge` | Task 7 |
| §7.1 promoter unit tests | Task 3 |
| §7.2 API endpoint tests | Task 5 |
| §7.3 promote-from-capture integration | Task 4 |
| §7.4 regression of capture/asset flows | Tasks 1 (conftest), 5 (Step 6) |
| §7.5–7.7 web vitest (list/detail/api) | Tasks 6, 8, 9 |
| §7.8 Playwright E2E (mandatory) | Task 10 |
| §7 migration applies via conftest | Task 1 |
