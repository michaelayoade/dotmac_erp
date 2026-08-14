"""Merge the coverage-expand and expense-withdrawal heads

Both branched from `20260811_balance_due_generated` and both landed in the same
integration, so the chain has two heads. This joins them; it does nothing else.

Neither touches the other's tables — `20260812_coverage_expand` adds
`amount_paid`/`balance_due` to payroll, expense and lease, while
`20260811_expense_withdraw` adds two enum VALUES — so there is no ordering
constraint between them and nothing to reconcile beyond the lineage itself.

The one adjacency worth stating: both touch `expense.expense_claim`. The
withdrawal migration extends `expense.expense_claim_status` with
APPROVAL_WITHDRAWN; the expand migration adds columns to the same table. An
`ALTER TYPE ... ADD VALUE` and an `ADD COLUMN` do not conflict, and the
column's generated expression reads `net_payable_amount`, never the status.

Revision ID: 20260812_merge_expand_withdrawal
Revises: 20260812_coverage_expand, 20260811_expense_withdraw
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "20260812_merge_expand_withdrawal"
down_revision: tuple[str, str] = (
    "20260812_coverage_expand",
    "20260811_expense_withdraw",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """A merge point carries no schema change of its own."""


def downgrade() -> None:
    """Splitting the lineage again is the inverse; there is nothing to undo."""
