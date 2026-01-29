"""Merge HR handbook branch with main branch.

Revision ID: 20260128_merge_handbook
Revises: 20260128_hr_handbook, e0696f5adbeb
Create Date: 2026-01-28

Merges the HR handbook migration branch with the main development branch.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260128_merge_handbook"
down_revision = ("20260128_hr_handbook", "e0696f5adbeb")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
