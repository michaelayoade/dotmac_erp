"""Expense Management Services.

Re-exports from the canonical ``app.services.expense`` module so that
callers in the people module get limit enforcement, authority validation,
notifications, and audit events automatically.
"""

from app.services.expense.expense_service import (
    ApproverAuthorityError,
    CardTransactionNotFoundError,
    CashAdvanceNotFoundError,
    CorporateCardNotFoundError,
    ExpenseCategoryNotFoundError,
    ExpenseClaimNotFoundError,
    ExpenseClaimStatusError,
    ExpenseLimitBlockedError,
    ExpenseService,
    ExpenseServiceError,
)

__all__ = [
    "ExpenseService",
    "ExpenseServiceError",
    "ExpenseCategoryNotFoundError",
    "ExpenseClaimNotFoundError",
    "ExpenseClaimStatusError",
    "ExpenseLimitBlockedError",
    "ApproverAuthorityError",
    "CashAdvanceNotFoundError",
    "CorporateCardNotFoundError",
    "CardTransactionNotFoundError",
]
