"""Add server health infrastructure category.

Revision ID: 20260628_server_health_category
Revises: 20260628_infra_health_alerts
Create Date: 2026-06-28
"""

from __future__ import annotations

from alembic import op

revision = "20260628_server_health_category"
down_revision = "20260628_infra_health_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE infra_health_category ADD VALUE IF NOT EXISTS 'SERVER'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely without recreating the type.
    pass
