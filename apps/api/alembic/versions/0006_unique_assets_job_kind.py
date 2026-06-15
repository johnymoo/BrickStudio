"""make one asset row per job and asset kind

Revision ID: 0006_unique_assets_job_kind
Revises: 0005_create_parts
Create Date: 2026-06-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_unique_assets_job_kind"
down_revision: str | Sequence[str] | None = "0005_create_parts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_assets_job_id_kind", "assets", ["job_id", "kind"])


def downgrade() -> None:
    op.drop_constraint("uq_assets_job_id_kind", "assets", type_="unique")
