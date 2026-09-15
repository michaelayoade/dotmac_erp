"""Persist private KPI measurement direction without guessing legacy definitions.

Revision ID: 20260915_kpi_direction
Revises: 20260914_technician_role
"""

from alembic import op
import sqlalchemy as sa

revision = "20260915_kpi_direction"
down_revision = "20260914_technician_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kpi", sa.Column("lower_is_better", sa.Boolean(), nullable=True), schema="perf"
    )


def downgrade() -> None:
    op.drop_column("kpi", "lower_is_better", schema="perf")
