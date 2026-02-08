"""Add expense_account_id to payroll_entry

Revision ID: add_payroll_expense_account
Revises: 20260131_add_salary_slip_review_fields
Create Date: 2026-01-31

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "add_payroll_expense_account"
down_revision = "20260131_add_salary_slip_review_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("payroll_entry", schema="payroll"):
        return

    columns = {
        col["name"] for col in inspector.get_columns("payroll_entry", schema="payroll")
    }
    if "expense_account_id" not in columns:
        op.add_column(
            "payroll_entry",
            sa.Column(
                "expense_account_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("gl.account.account_id"),
                nullable=True,
                comment="GL expense account for payroll posting (overrides org default)",
            ),
            schema="payroll",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("payroll_entry", schema="payroll"):
        return

    columns = {
        col["name"] for col in inspector.get_columns("payroll_entry", schema="payroll")
    }
    if "expense_account_id" in columns:
        op.drop_column("payroll_entry", "expense_account_id", schema="payroll")
