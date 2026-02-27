"""make_employee_loan_loan_type_id_nullable

Revision ID: 78121b4eee25
Revises: 20260226_add_payroll_entry_employment_type_filter
Create Date: 2026-02-27 10:45:16.642527

"""

from sqlalchemy import inspect

from alembic import op

revision = "78121b4eee25"
down_revision = "20260226_add_payroll_entry_employment_type_filter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = {
        c["name"]: c for c in inspector.get_columns("employee_loan", schema="payroll")
    }
    col = columns.get("loan_type_id")
    if col and not col["nullable"]:
        op.alter_column(
            "employee_loan",
            "loan_type_id",
            nullable=True,
            schema="payroll",
        )


def downgrade() -> None:
    op.alter_column(
        "employee_loan",
        "loan_type_id",
        nullable=False,
        schema="payroll",
    )
