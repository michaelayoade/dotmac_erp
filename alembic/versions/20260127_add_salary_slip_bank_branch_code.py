"""Add bank_branch_code to payroll.salary_slip.

Revision ID: 20260127_add_salary_slip_bank_branch_code
Revises: 20260126_add_pm_comments
Create Date: 2026-01-27
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260127_add_salary_slip_bank_branch_code"
down_revision = "20260126_add_pm_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("salary_slip", schema="payroll"):
        return

    columns = {
        col["name"] for col in inspector.get_columns("salary_slip", schema="payroll")
    }

    if "bank_branch_code" not in columns:
        op.add_column(
            "salary_slip",
            sa.Column("bank_branch_code", sa.String(20), nullable=True),
            schema="payroll",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("salary_slip", schema="payroll"):
        return

    columns = {
        col["name"] for col in inspector.get_columns("salary_slip", schema="payroll")
    }

    if "bank_branch_code" in columns:
        op.drop_column("salary_slip", "bank_branch_code", schema="payroll")
