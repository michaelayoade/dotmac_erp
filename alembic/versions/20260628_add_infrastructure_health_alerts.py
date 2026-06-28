"""Add infrastructure health status and alerts.

Revision ID: 20260628_infra_health_alerts
Revises: 20260610_mri_serials, 20260614_tax_code_is_fixed_amount, 20260616_dotmac_sub_integration
Create Date: 2026-06-28
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260628_infra_health_alerts"
down_revision = (
    "20260610_mri_serials",
    "20260614_tax_code_is_fixed_amount",
    "20260616_dotmac_sub_integration",
)
branch_labels = None
depends_on = None


category_enum = postgresql.ENUM(
    "APPLICATION",
    "SERVER",
    "WORKERS",
    "SCHEDULED_JOBS",
    "QUEUES",
    "DATABASE",
    "REPLICATION",
    "CACHE",
    "EXTERNAL",
    name="infra_health_category",
    create_type=False,
)
health_status_enum = postgresql.ENUM(
    "HEALTHY",
    "DEGRADED",
    "UNHEALTHY",
    "UNKNOWN",
    name="infra_health_status",
    create_type=False,
)
alert_severity_enum = postgresql.ENUM(
    "INFO",
    "WARNING",
    "CRITICAL",
    name="infra_alert_severity",
    create_type=False,
)
alert_status_enum = postgresql.ENUM(
    "OPEN",
    "RESOLVED",
    name="infra_alert_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    category_enum.create(bind, checkfirst=True)
    health_status_enum.create(bind, checkfirst=True)
    alert_severity_enum.create(bind, checkfirst=True)
    alert_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "infrastructure_health_status",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("category", category_enum, nullable=False),
        sa.Column("check_key", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("status", health_status_enum, nullable=False),
        sa.Column("severity", alert_severity_enum, nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_healthy_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_unhealthy_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "category", "check_key", name="uq_infra_health_category_key"
        ),
        schema="public",
    )
    op.create_index(
        "ix_infra_health_category_status",
        "infrastructure_health_status",
        ["category", "status"],
        schema="public",
    )
    op.create_index(
        "ix_infra_health_checked_at",
        "infrastructure_health_status",
        ["last_checked_at"],
        schema="public",
    )

    op.create_table(
        "infrastructure_alert",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("fingerprint", sa.String(length=200), nullable=False),
        sa.Column("category", category_enum, nullable=False),
        sa.Column("check_key", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("severity", alert_severity_enum, nullable=False),
        sa.Column("status", alert_status_enum, nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("last_notification_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_infra_alert_fingerprint"),
        schema="public",
    )
    op.create_index(
        "ix_infra_alert_status_severity",
        "infrastructure_alert",
        ["status", "severity"],
        schema="public",
    )
    op.create_index(
        "ix_infra_alert_category_status",
        "infrastructure_alert",
        ["category", "status"],
        schema="public",
    )
    op.create_index(
        "ix_infra_alert_last_seen_at",
        "infrastructure_alert",
        ["last_seen_at"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_infra_alert_last_seen_at",
        table_name="infrastructure_alert",
        schema="public",
    )
    op.drop_index(
        "ix_infra_alert_category_status",
        table_name="infrastructure_alert",
        schema="public",
    )
    op.drop_index(
        "ix_infra_alert_status_severity",
        table_name="infrastructure_alert",
        schema="public",
    )
    op.drop_table("infrastructure_alert", schema="public")
    op.drop_index(
        "ix_infra_health_checked_at",
        table_name="infrastructure_health_status",
        schema="public",
    )
    op.drop_index(
        "ix_infra_health_category_status",
        table_name="infrastructure_health_status",
        schema="public",
    )
    op.drop_table("infrastructure_health_status", schema="public")

    bind = op.get_bind()
    alert_status_enum.drop(bind, checkfirst=True)
    alert_severity_enum.drop(bind, checkfirst=True)
    health_status_enum.drop(bind, checkfirst=True)
    category_enum.drop(bind, checkfirst=True)
