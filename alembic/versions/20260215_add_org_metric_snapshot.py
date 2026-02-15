"""Add org_metric_snapshot table for pre-computed analytics metrics.

Revision ID: 20260215_add_org_metric_snapshot
Revises: 20260214_add_coach_models
Create Date: 2026-02-15
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260215_add_org_metric_snapshot"
down_revision = "20260214_add_coach_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)

    if not insp.has_table("org_metric_snapshot"):
        op.create_table(
            "org_metric_snapshot",
            sa.Column(
                "snapshot_id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "organization_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column("metric_type", sa.String(80), nullable=False),
            sa.Column("snapshot_date", sa.Date, nullable=False),
            sa.Column(
                "granularity",
                sa.String(10),
                nullable=False,
                server_default="DAILY",
            ),
            sa.Column(
                "dimension_type",
                sa.String(30),
                nullable=False,
                server_default="ORG",
            ),
            sa.Column(
                "dimension_id",
                sa.String(40),
                nullable=False,
                server_default="ALL",
            ),
            sa.Column("value_numeric", sa.Numeric(20, 6), nullable=True),
            sa.Column("value_json", postgresql.JSONB, nullable=True),
            sa.Column("currency_code", sa.String(3), nullable=True),
            sa.Column(
                "computed_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("source_label", sa.String(80), nullable=True),
            sa.UniqueConstraint(
                "organization_id",
                "metric_type",
                "snapshot_date",
                "granularity",
                "dimension_type",
                "dimension_id",
                name="uq_org_metric_snapshot_key",
            ),
        )

    # Indexes (idempotent via IF NOT EXISTS)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_oms_org_type_date "
        "ON org_metric_snapshot (organization_id, metric_type, snapshot_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_oms_org_type_gran_date "
        "ON org_metric_snapshot (organization_id, metric_type, granularity, snapshot_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_oms_org_dim_type "
        "ON org_metric_snapshot (organization_id, dimension_type, dimension_id, metric_type)"
    )


def downgrade() -> None:
    op.drop_table("org_metric_snapshot")
