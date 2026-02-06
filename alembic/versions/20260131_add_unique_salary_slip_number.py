"""Add unique constraint for salary slip number per org

Revision ID: 20260131_add_unique_salary_slip_number
Revises: add_payroll_expense_account
Create Date: 2026-01-31

"""

from alembic import op
import sqlalchemy as sa

revision = "20260131_add_unique_salary_slip_number"
down_revision = "add_payroll_expense_account"
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "uq_salary_slip_org_number"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("salary_slip", schema="payroll"):
        return

    existing = {
        uc["name"]
        for uc in inspector.get_unique_constraints("salary_slip", schema="payroll")
    }
    if CONSTRAINT_NAME not in existing:
        op.create_unique_constraint(
            CONSTRAINT_NAME,
            "salary_slip",
            ["organization_id", "slip_number"],
            schema="payroll",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("salary_slip", schema="payroll"):
        return

    existing = {
        uc["name"]
        for uc in inspector.get_unique_constraints("salary_slip", schema="payroll")
    }
    if CONSTRAINT_NAME in existing:
        op.drop_constraint(
            CONSTRAINT_NAME, "salary_slip", schema="payroll", type_="unique"
        )
