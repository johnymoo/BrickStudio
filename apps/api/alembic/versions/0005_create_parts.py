"""create parts table (issue #5 — reusable library part)

Revision ID: 0005_create_parts
Revises: 0004_captures_ar_recognized
Create Date: 2026-06-14

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
    op.create_index("ix_parts_asset_id", "parts", ["asset_id"], unique=False)
    op.create_index("ix_parts_status", "parts", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_parts_status", table_name="parts")
    op.drop_index("ix_parts_asset_id", table_name="parts", if_exists=True)
    op.drop_index("ix_parts_capture_id", table_name="parts")
    op.drop_table("parts")
