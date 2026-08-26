"""Payment-execution permissions consumed by Expense reimbursement."""

from __future__ import annotations

EXPENSE_PAYOUT_PERMISSION_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("payments:read", "View payment intents and resolve bank details"),
    ("payments:expense:initialize", "Prepare an expense reimbursement payout"),
    (
        "payments:transfer:initiate",
        "Execute an outbound expense transfer (moves money)",
    ),
)

__all__ = ["EXPENSE_PAYOUT_PERMISSION_DEFINITIONS"]
