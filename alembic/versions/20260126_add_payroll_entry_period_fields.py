"""Add payroll period identification fields to payroll_entry.

Revision ID: 20260126_add_payroll_entry_period_fields
Revises: add_expense_claim_action_seq
Create Date: 2026-01-26
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260126_add_payroll_entry_period_fields"
down_revision = "add_expense_claim_action_seq"
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

    if "payroll_year" not in columns:
        op.add_column(
            "payroll_entry",
            sa.Column("payroll_year", sa.Integer(), nullable=True),
            schema="payroll",
        )

    if "payroll_month" not in columns:
        op.add_column(
            "payroll_entry",
            sa.Column("payroll_month", sa.Integer(), nullable=True),
            schema="payroll",
        )

    if "entry_name" not in columns:
        op.add_column(
            "payroll_entry",
            sa.Column("entry_name", sa.String(100), nullable=True),
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

    if "entry_name" in columns:
        op.drop_column("payroll_entry", "entry_name", schema="payroll")

    if "payroll_month" in columns:
        op.drop_column("payroll_entry", "payroll_month", schema="payroll")

    if "payroll_year" in columns:
        op.drop_column("payroll_entry", "payroll_year", schema="payroll")
