"""Merge scheduling and merge heads.

Revision ID: 20260128_merge_scheduling
Revises: 20260128_scheduling, 5709758f2538
Create Date: 2026-01-28

Merges the scheduling migration with the existing merge head.
"""

# revision identifiers, used by Alembic.
revision = "20260128_merge_scheduling"
down_revision = ("20260128_scheduling", "5709758f2538")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
