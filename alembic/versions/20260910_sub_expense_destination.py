"""Add secure per-expense reimbursement destination evidence.

Revision ID: 20260910_sub_expense_destination
Revises: 20260912_merge_automation
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260910_sub_expense_destination"
down_revision = "20260912_merge_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "expense_claim",
        sa.Column("recipient_account_number_encrypted", sa.Text(), nullable=True),
        schema="expense",
    )
    op.add_column(
        "expense_claim",
        sa.Column("recipient_account_number_last4", sa.String(4), nullable=True),
        schema="expense",
    )
    op.add_column(
        "expense_claim",
        sa.Column("payment_destination_mode", sa.String(30), nullable=True),
        schema="expense",
    )
    op.add_column(
        "expense_claim",
        sa.Column("payment_destination_fingerprint", sa.String(64), nullable=True),
        schema="expense",
    )
    op.add_column(
        "expense_claim",
        sa.Column(
            "payment_destination_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="expense",
    )
    op.create_check_constraint(
        "ck_expense_claim_destination_mode",
        "expense_claim",
        "payment_destination_mode IS NULL OR payment_destination_mode IN "
        "('erp_profile', 'expense_override')",
        schema="expense",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_expense_claim_destination_mode",
        "expense_claim",
        schema="expense",
        type_="check",
    )
    for column in (
        "payment_destination_verified_at",
        "payment_destination_fingerprint",
        "payment_destination_mode",
        "recipient_account_number_last4",
        "recipient_account_number_encrypted",
    ):
        op.drop_column("expense_claim", column, schema="expense")
