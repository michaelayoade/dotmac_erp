"""Add pattern_lines to shift_pattern for explicit rotating week/day mapping.

Revision ID: 20260227_add_shift_pattern_lines
Revises: 78121b4eee25
Create Date: 2026-02-27
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260227_add_shift_pattern_lines"
down_revision = "78121b4eee25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shift_pattern",
        sa.Column(
            "pattern_lines",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema="scheduling",
    )


def downgrade() -> None:
    op.drop_column("shift_pattern", "pattern_lines", schema="scheduling")
