"""add captures parametric-block columns (v0.3)

Revision ID: 0003_captures_parametric_block
Revises: 0002_capture_mode
Create Date: 2026-06-06 21:00:00

Adds the schema fields required by the parametric-block capture path
(see ``docs/ROADMAP.md`` §v0.3 and ``docs/capture-procedure.md``).
None of the new columns are required to backfill: every existing photo
capture gets ``mode='photo'`` (the default) and NULLs in the rest.

Columns added
-------------
* ``mode``  String(32) NOT NULL DEFAULT 'photo'  -- 'photo' | 'parametric_block'
* ``system``                  String(32) NULL
* ``kind``                    String(32) NULL
* ``units_x``                 Integer   NULL
* ``units_y``                 Integer   NULL
* ``raw_measurements_mm``     JSONB     NULL
* ``derived_spec_mm``         JSONB     NULL
* ``cross_check_warnings``    JSONB     NULL
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_captures_parametric_block"
down_revision: str | Sequence[str] | None = "0002_capture_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``mode`` is NOT NULL with a server-side default of ``"photo"`` so
    # the migration backfills every pre-existing row in a single ALTER
    # TABLE (Postgres applies server_default to existing rows on the
    # add of a NOT NULL column). New rows created by the application
    # also pick up the default unless they explicitly set ``mode``.
    op.add_column(
        "captures",
        sa.Column(
            "mode",
            sa.String(length=32),
            nullable=False,
            server_default="photo",
        ),
    )
    op.add_column(
        "captures",
        sa.Column("system", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "captures",
        sa.Column("kind", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "captures",
        sa.Column("units_x", sa.Integer(), nullable=True),
    )
    op.add_column(
        "captures",
        sa.Column("units_y", sa.Integer(), nullable=True),
    )
    op.add_column(
        "captures",
        sa.Column(
            "raw_measurements_mm",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "captures",
        sa.Column(
            "derived_spec_mm",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "captures",
        sa.Column(
            "cross_check_warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("captures", "cross_check_warnings")
    op.drop_column("captures", "derived_spec_mm")
    op.drop_column("captures", "raw_measurements_mm")
    op.drop_column("captures", "units_y")
    op.drop_column("captures", "units_x")
    op.drop_column("captures", "kind")
    op.drop_column("captures", "system")
    op.drop_column("captures", "mode")
