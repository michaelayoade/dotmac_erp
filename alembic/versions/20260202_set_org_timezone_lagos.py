"""set_org_timezone_lagos

Revision ID: 20260202_set_org_timezone_lagos
Revises: d72ea77c35b7
Create Date: 2026-02-02 08:08:00.000000

"""

from alembic import op


revision = "20260202_set_org_timezone_lagos"
down_revision = "d72ea77c35b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create tracking table for precise downgrade
    op.execute(
        "CREATE TABLE IF NOT EXISTS _migration_tz_affected ("
        "  organization_id UUID PRIMARY KEY"
        ")"
    )
    # Record which orgs we're about to change
    op.execute(
        "INSERT INTO _migration_tz_affected (organization_id) "
        "SELECT organization_id FROM core_org.organization "
        "WHERE timezone IS NULL OR timezone = ''"
    )
    op.execute(
        "UPDATE core_org.organization "
        "SET timezone = 'Africa/Lagos' "
        "WHERE timezone IS NULL OR timezone = ''"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE core_org.organization o "
        "SET timezone = NULL "
        "FROM _migration_tz_affected a "
        "WHERE o.organization_id = a.organization_id"
    )
    op.execute("DROP TABLE IF EXISTS _migration_tz_affected")
