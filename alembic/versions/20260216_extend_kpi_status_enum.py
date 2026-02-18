"""Extend perf.kpi_status enum with values used by application code.

Revision ID: 20260216_extend_kpi_status_enum
Revises: 20260215_add_org_metric_snapshot
Create Date: 2026-02-16
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260216_extend_kpi_status_enum"
down_revision = "20260215_add_org_metric_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep DB enum aligned with KPIStatus values used by model/service code.
    op.execute("ALTER TYPE perf.kpi_status ADD VALUE IF NOT EXISTS 'PENDING'")
    op.execute("ALTER TYPE perf.kpi_status ADD VALUE IF NOT EXISTS 'ON_TRACK'")
    op.execute("ALTER TYPE perf.kpi_status ADD VALUE IF NOT EXISTS 'AT_RISK'")
    op.execute("ALTER TYPE perf.kpi_status ADD VALUE IF NOT EXISTS 'COMPLETED'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values.
    pass
