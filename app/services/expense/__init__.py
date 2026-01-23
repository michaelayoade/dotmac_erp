"""
Expense Management Service.

Independent module for expense claims, cash advances, and corporate cards.
Includes expense limit enforcement for multi-dimensional spending controls.
"""

from app.services.expense.expense_service import (
    ExpenseService,
    ExpenseServiceError,
    ExpenseCategoryNotFoundError,
    ExpenseClaimNotFoundError,
    ExpenseClaimStatusError,
    ExpenseLimitBlockedError,
    CashAdvanceNotFoundError,
    CorporateCardNotFoundError,
    CardTransactionNotFoundError,
    SubmitClaimResult,
)
from app.services.expense.limit_service import (
    ExpenseLimitService,
    ExpenseLimitServiceError,
    ExpenseLimitRuleNotFoundError,
    ExpenseApproverLimitNotFoundError,
    ExpenseLimitExceededError,
    EvaluationResult,
    EligibleApprover,
)

__all__ = [
    # Expense Service
    "ExpenseService",
    "ExpenseServiceError",
    "ExpenseCategoryNotFoundError",
    "ExpenseClaimNotFoundError",
    "ExpenseClaimStatusError",
    "ExpenseLimitBlockedError",
    "CashAdvanceNotFoundError",
    "CorporateCardNotFoundError",
    "CardTransactionNotFoundError",
    "SubmitClaimResult",
    # Limit Service
    "ExpenseLimitService",
    "ExpenseLimitServiceError",
    "ExpenseLimitRuleNotFoundError",
    "ExpenseApproverLimitNotFoundError",
    "ExpenseLimitExceededError",
    "EvaluationResult",
    "EligibleApprover",
]
