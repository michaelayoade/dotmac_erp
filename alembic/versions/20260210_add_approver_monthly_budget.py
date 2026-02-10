"""Add monthly_approval_budget column to expense_approver_limit.

Revision ID: 20260210_add_approver_monthly_budget
Revises: 20260209_add_settings_bank_directory
Create Date: 2026-02-10
"""

import sqlalchemy as sa

from alembic import op

revision = "20260210_add_approver_monthly_budget"
down_revision = "20260209_add_settings_bank_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: only add if column doesn't exist
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [
        c["name"]
        for c in inspector.get_columns("expense_approver_limit", schema="expense")
    ]
    if "monthly_approval_budget" not in columns:
        op.add_column(
            "expense_approver_limit",
            sa.Column(
                "monthly_approval_budget",
                sa.Numeric(15, 2),
                nullable=True,
                comment="Monthly budget cap for total approvals. NULL = unlimited.",
            ),
            schema="expense",
        )


def downgrade() -> None:
    op.drop_column(
        "expense_approver_limit", "monthly_approval_budget", schema="expense"
    )
