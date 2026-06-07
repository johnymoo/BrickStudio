"""add captures ar_recognized columns (v0.5)

Revision ID: 0004_captures_ar_recognized
Revises: 0003_captures_parametric_block
Create Date: 2026-06-07 10:00:00

Adds the two JSONB columns the AR-capture recognition path needs. Both
are nullable; existing photo / parametric_block rows keep NULLs. The
``mode`` column (added in 0003) gains a new application-level value
``"ar_recognized"`` — no schema change needed for that (it's a String).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_captures_ar_recognized"
down_revision: str | Sequence[str] | None = "0003_captures_parametric_block"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "captures",
        sa.Column("ar_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "captures",
        sa.Column("recognition_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("captures", "recognition_result")
    op.drop_column("captures", "ar_metadata")
