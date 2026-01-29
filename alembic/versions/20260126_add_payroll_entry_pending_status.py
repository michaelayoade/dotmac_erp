"""Add PENDING to payroll_entry_status enum.

Revision ID: 20260126_add_payroll_entry_pending_status
Revises: 20260126_add_payroll_entry_period_fields
Create Date: 2026-01-26
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260126_add_payroll_entry_pending_status"
down_revision = "20260126_add_payroll_entry_period_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE payroll_entry_status ADD VALUE IF NOT EXISTS 'PENDING'")


def downgrade() -> None:
    # Postgres enums cannot easily drop values; leave as-is.
    pass
