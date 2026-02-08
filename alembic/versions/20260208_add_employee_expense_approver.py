"""Add expense approver to employees.

Revision ID: 20260208_add_employee_expense_approver
Revises: e0696f5adbeb
Create Date: 2026-02-08
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260208_add_employee_expense_approver"
down_revision = "20260208_drop_expense_pg_sequences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("employee", schema="hr")}

    if "expense_approver_id" not in columns:
        op.add_column(
            "employee",
            sa.Column(
                "expense_approver_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("hr.employee.employee_id"),
                nullable=True,
            ),
            schema="hr",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("employee", schema="hr")}

    if "expense_approver_id" in columns:
        op.drop_column("employee", "expense_approver_id", schema="hr")
