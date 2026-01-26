"""
Expense Management Service.

Independent module for expense claims, cash advances, and corporate cards.
Includes expense limit enforcement for multi-dimensional spending controls.
Provides AP/GL integration via ExpensePostingAdapter.
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
from app.services.expense.expense_posting_adapter import (
    ExpensePostingAdapter,
    ExpensePostingResult,
)
from app.services.expense.approval_service import (
    ExpenseApprovalService,
    ApprovalStep,
    ApprovalChain,
    ReceiptValidationResult,
)
from app.services.expense.expense_notifications import (
    ExpenseNotificationService,
    get_expense_notification_service,
)
from app.services.expense.web import (
    ExpenseClaimsWebService,
    expense_claims_web_service,
)
from app.services.expense.limit_web import expense_limit_web_service

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
    # Posting Adapter
    "ExpensePostingAdapter",
    "ExpensePostingResult",
    # Approval Service
    "ExpenseApprovalService",
    "ApprovalStep",
    "ApprovalChain",
    "ReceiptValidationResult",
    # Notification Service
    "ExpenseNotificationService",
    "get_expense_notification_service",
    # Web Service
    "ExpenseClaimsWebService",
    "expense_claims_web_service",
    "expense_limit_web_service",
]
