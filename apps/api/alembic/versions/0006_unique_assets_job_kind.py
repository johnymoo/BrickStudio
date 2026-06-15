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
    op.execute(
        """
        WITH ranked_assets AS (
            SELECT
                assets.id,
                first_value(assets.id) OVER (
                    PARTITION BY assets.job_id, assets.kind
                    ORDER BY
                        CASE WHEN referenced_assets.asset_id IS NOT NULL THEN 1 ELSE 0 END DESC,
                        assets.created_at DESC NULLS LAST,
                        assets.id DESC
                ) AS keeper_id,
                count(*) OVER (PARTITION BY assets.job_id, assets.kind) AS duplicate_count
            FROM assets
            LEFT JOIN (
                SELECT DISTINCT asset_id
                FROM parts
                WHERE asset_id IS NOT NULL
            ) AS referenced_assets ON referenced_assets.asset_id = assets.id
        ),
        duplicate_assets AS (
            SELECT id, keeper_id
            FROM ranked_assets
            WHERE duplicate_count > 1
              AND id <> keeper_id
        )
        UPDATE parts
        SET asset_id = duplicate_assets.keeper_id
        FROM duplicate_assets
        WHERE parts.asset_id = duplicate_assets.id
        """
    )
    op.execute(
        """
        WITH ranked_assets AS (
            SELECT
                assets.id,
                first_value(assets.id) OVER (
                    PARTITION BY assets.job_id, assets.kind
                    ORDER BY
                        CASE WHEN referenced_assets.asset_id IS NOT NULL THEN 1 ELSE 0 END DESC,
                        assets.created_at DESC NULLS LAST,
                        assets.id DESC
                ) AS keeper_id,
                count(*) OVER (PARTITION BY assets.job_id, assets.kind) AS duplicate_count
            FROM assets
            LEFT JOIN (
                SELECT DISTINCT asset_id
                FROM parts
                WHERE asset_id IS NOT NULL
            ) AS referenced_assets ON referenced_assets.asset_id = assets.id
        ),
        duplicate_assets AS (
            SELECT id
            FROM ranked_assets
            WHERE duplicate_count > 1
              AND id <> keeper_id
        )
        DELETE FROM assets
        USING duplicate_assets
        WHERE assets.id = duplicate_assets.id
        """
    )
    op.create_unique_constraint("uq_assets_job_id_kind", "assets", ["job_id", "kind"])


def downgrade() -> None:
    op.drop_constraint("uq_assets_job_id_kind", "assets", type_="unique")
