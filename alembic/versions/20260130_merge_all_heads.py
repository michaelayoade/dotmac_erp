"""Merge all pending heads.

Revision ID: 20260130_merge_all
Revises: 20260130_fix_remaining, 20260130_backfill_asset_custodian_fk
Create Date: 2026-01-30
"""

# revision identifiers, used by Alembic.
revision = "20260130_merge_all"
down_revision = ("20260130_fix_remaining", "20260130_backfill_asset_custodian_fk")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
