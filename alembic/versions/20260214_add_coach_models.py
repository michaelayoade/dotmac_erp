"""Add coach insight/report models.

Revision ID: 20260214_add_coach_models
Revises: 20260214_add_splynx_numbers_to_ar
Create Date: 2026-02-14
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260214_add_coach_models"
down_revision = "20260214_add_splynx_numbers_to_ar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("coach_insight"):
        op.create_table(
            "coach_insight",
            sa.Column(
                "insight_id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("audience", sa.String(length=30), nullable=False),
            sa.Column(
                "target_employee_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            sa.Column("category", sa.String(length=30), nullable=False),
            sa.Column("severity", sa.String(length=20), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("coaching_action", sa.Text(), nullable=False),
            sa.Column(
                "confidence",
                sa.Float(),
                nullable=False,
                server_default=sa.text("0.5"),
            ),
            sa.Column(
                "data_sources",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "evidence",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default=sa.text("'GENERATED'"),
            ),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("feedback", sa.String(length=30), nullable=True),
            sa.Column("valid_until", sa.Date(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )

    if not insp.has_table("coach_report"):
        op.create_table(
            "coach_report",
            sa.Column(
                "report_id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("audience", sa.String(length=30), nullable=False),
            sa.Column(
                "target_employee_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            sa.Column("report_type", sa.String(length=50), nullable=False),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("period_end", sa.Date(), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("executive_summary", sa.Text(), nullable=False),
            sa.Column(
                "sections",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "key_metrics",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "recommendations",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "model_used",
                sa.String(length=120),
                nullable=False,
                server_default=sa.text("''"),
            ),
            sa.Column(
                "tokens_used",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "generation_time_ms",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Indexes (idempotent)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_coach_insight_org "
        "ON coach_insight (organization_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_coach_insight_org_status "
        "ON coach_insight (organization_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_coach_insight_org_target "
        "ON coach_insight (organization_id, target_employee_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_coach_insight_valid_until "
        "ON coach_insight (valid_until)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_coach_report_org "
        "ON coach_report (organization_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_coach_report_org_target "
        "ON coach_report (organization_id, target_employee_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_coach_report_period "
        "ON coach_report (organization_id, period_start, period_end)"
    )


def downgrade() -> None:
    # Drop tables first; indexes will be dropped automatically.
    op.execute("DROP TABLE IF EXISTS coach_report CASCADE")
    op.execute("DROP TABLE IF EXISTS coach_insight CASCADE")
