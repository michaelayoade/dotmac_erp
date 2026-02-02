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
    op.execute(
        "UPDATE core_org.organization "
        "SET timezone = 'Africa/Lagos' "
        "WHERE timezone IS NULL OR timezone = ''"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE core_org.organization "
        "SET timezone = NULL "
        "WHERE timezone = 'Africa/Lagos'"
    )
