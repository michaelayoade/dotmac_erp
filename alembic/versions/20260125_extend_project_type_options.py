"""Extend project_type enum with installation/relocation options.

Revision ID: 20260125_extend_project_type
Revises: 20260125_project_templates
Create Date: 2026-01-25
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260125_extend_project_type"
down_revision: Union[str, Sequence[str], None] = "20260125_project_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE pm.project_type ADD VALUE IF NOT EXISTS 'FIBER_OPTICS_INSTALLATION'")
    op.execute("ALTER TYPE pm.project_type ADD VALUE IF NOT EXISTS 'AIR_FIBER_INSTALLATION'")
    op.execute("ALTER TYPE pm.project_type ADD VALUE IF NOT EXISTS 'CABLE_RERUN'")
    op.execute("ALTER TYPE pm.project_type ADD VALUE IF NOT EXISTS 'FIBER_OPTICS_RELOCATION'")
    op.execute("ALTER TYPE pm.project_type ADD VALUE IF NOT EXISTS 'AIR_FIBER_RELOCATION'")


def downgrade() -> None:
    # Enum values are not removed in downgrade to avoid data loss.
    pass
