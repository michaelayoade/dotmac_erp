"""Persist reset balances and reliable payment timestamps without inventing history."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "20261104_expense_reset_audit"
down_revision = "20260930_recon_rule_bank_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "expense_approver_limit_reset",
        sa.Column("legacy_paid_claim_ids", JSONB(), nullable=True),
        schema="expense",
    )
    op.add_column(
        "expense_claim",
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        schema="expense",
    )
    op.add_column(
        "expense_approver_limit_reset",
        sa.Column("reset_kind", sa.String(20), nullable=False, server_default="MANUAL"),
        schema="expense",
    )
    for name in (
        "budget_snapshot",
        "paid_before_reset",
        "committed_at_reset",
        "remaining_before_reset",
        "remaining_after_reset",
    ):
        op.add_column(
            "expense_approver_limit_reset",
            sa.Column(name, sa.Numeric(19, 4), nullable=True),
            schema="expense",
        )
    op.add_column(
        "expense_approver_limit_reset",
        sa.Column("currency_snapshot", sa.String(3), nullable=True),
        schema="expense",
    )
    op.add_column(
        "expense_approver_limit_reset",
        sa.Column("reviewer_name_snapshot", sa.String(200), nullable=True),
        schema="expense",
    )
    # Classification is known from the old scheduler. Historical balances and
    # payment times are deliberately left NULL, rather than reconstructed.
    op.execute(
        sa.text(
            "UPDATE expense.expense_approver_limit_reset SET reset_kind = 'AUTOMATIC' WHERE reset_reason = 'Scheduled weekly budget reset'"
        )
    )


def downgrade() -> None:
    op.drop_column(
        "expense_approver_limit_reset", "legacy_paid_claim_ids", schema="expense"
    )
    for name in (
        "reviewer_name_snapshot",
        "currency_snapshot",
        "remaining_after_reset",
        "remaining_before_reset",
        "committed_at_reset",
        "paid_before_reset",
        "budget_snapshot",
        "reset_kind",
    ):
        op.drop_column("expense_approver_limit_reset", name, schema="expense")
    op.drop_column("expense_claim", "paid_at", schema="expense")
