"""add captures.capture_mode (phase 2)

Revision ID: 0002_capture_mode
Revises: 0001_init
Create Date: 2026-06-05 09:10:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_capture_mode"
down_revision: str | Sequence[str] | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add the new column. The design-phase2.md §3.1 default is
    # ``phone_walkaround``; Postgres applies it to all existing rows
    # via the ``server_default`` (works because the column is
    # NOT NULL with a server-side default).
    op.add_column(
        "captures",
        sa.Column(
            "capture_mode",
            sa.String(length=32),
            nullable=False,
            server_default="phone_walkaround",
        ),
    )


def downgrade() -> None:
    op.drop_column("captures", "capture_mode")
