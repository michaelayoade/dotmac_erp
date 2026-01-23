"""
Expense Management Models.

Independent module for expense claims, cash advances, and corporate cards.
Integrates with HR for employee tracking and Finance for AP/GL posting.
"""

from app.models.expense.expense_claim import (
    ExpenseClaim,
    ExpenseClaimStatus,
    ExpenseClaimItem,
    ExpenseCategory,
)
from app.models.expense.cash_advance import CashAdvance, CashAdvanceStatus
from app.models.expense.corporate_card import CorporateCard, CardTransaction, CardTransactionStatus
from app.models.expense.limit_rule import (
    ExpenseLimitRule,
    ExpenseApproverLimit,
    ExpenseLimitEvaluation,
    ExpensePeriodUsage,
    LimitScopeType,
    LimitPeriodType,
    LimitActionType,
    LimitResultType,
)

__all__ = [
    # Expense Claims
    "ExpenseClaim",
    "ExpenseClaimStatus",
    "ExpenseClaimItem",
    "ExpenseCategory",
    # Cash Advance
    "CashAdvance",
    "CashAdvanceStatus",
    # Corporate Cards
    "CorporateCard",
    "CardTransaction",
    "CardTransactionStatus",
    # Expense Limits
    "ExpenseLimitRule",
    "ExpenseApproverLimit",
    "ExpenseLimitEvaluation",
    "ExpensePeriodUsage",
    "LimitScopeType",
    "LimitPeriodType",
    "LimitActionType",
    "LimitResultType",
]
