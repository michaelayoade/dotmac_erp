"""
Expense Management Models.

Independent module for expense claims, cash advances, and corporate cards.
Integrates with HR for employee tracking and Finance for AP/GL posting.
"""

from app.models.expense.cash_advance import CashAdvance, CashAdvanceStatus
from app.models.expense.corporate_card import (
    CardTransaction,
    CardTransactionStatus,
    CorporateCard,
)
from app.models.expense.expense_claim import (
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)
from app.models.expense.expense_claim_action import (
    ExpenseClaimAction,
    ExpenseClaimActionStatus,
    ExpenseClaimActionType,
)
from app.models.expense.expense_claim_approval_step import ExpenseClaimApprovalStep
from app.models.expense.limit_rule import (
    ExpenseApproverBudgetAdjustment,
    ExpenseApproverLimit,
    ExpenseApproverLimitReset,
    ExpenseLimitEvaluation,
    ExpenseLimitRule,
    ExpensePeriodUsage,
    LimitActionType,
    LimitPeriodType,
    LimitResultType,
    LimitScopeType,
)

__all__ = [
    # Expense Claims
    "ExpenseClaim",
    "ExpenseClaimStatus",
    "ExpenseClaimItem",
    "ExpenseCategory",
    "ExpenseClaimAction",
    "ExpenseClaimActionType",
    "ExpenseClaimActionStatus",
    "ExpenseClaimApprovalStep",
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
    "ExpenseApproverBudgetAdjustment",
    "ExpenseApproverLimitReset",
    "ExpenseLimitEvaluation",
    "ExpensePeriodUsage",
    "LimitScopeType",
    "LimitPeriodType",
    "LimitActionType",
    "LimitResultType",
]
