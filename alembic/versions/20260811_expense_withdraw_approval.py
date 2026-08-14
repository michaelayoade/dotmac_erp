"""Add expense approval-withdrawal workflow values.

Revision ID: 20260811_expense_withdraw
Revises: 20260808_open_setting_domain, 20260811_balance_due_generated
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_expense_withdraw"
down_revision: tuple[str, str] = (
    "20260808_open_setting_domain",
    "20260811_balance_due_generated",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE expense.expense_claim_status ADD VALUE IF NOT EXISTS 'APPROVAL_WITHDRAWN'"
    )
    op.execute(
        "ALTER TYPE expense_claim_action_type "
        "ADD VALUE IF NOT EXISTS 'WITHDRAW_APPROVAL'"
    )


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely while dependent rows exist.
    pass
