"""Add fixed asset serial uniqueness.

Revision ID: 20260520_fa_asset_serial_unique
Revises: 20260515_backfill_global_automatch
Create Date: 2026-05-20
"""

from __future__ import annotations

from alembic import op

revision = "20260520_fa_asset_serial_unique"
down_revision = "20260515_backfill_global_automatch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_org_serial_normalized
        ON fa.asset (organization_id, lower(btrim(serial_number)))
        WHERE nullif(btrim(serial_number), '') IS NOT NULL
          AND lower(btrim(serial_number)) NOT IN ('nil', 'n/a', 'na', 'none', 'null')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS fa.uq_asset_org_serial_normalized
        """
    )
