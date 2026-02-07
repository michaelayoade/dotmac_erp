"""
Expense Management Service.

Independent module for expense claims, cash advances, and corporate cards.
Includes expense limit enforcement for multi-dimensional spending controls.
Provides AP/GL integration via ExpensePostingAdapter.
"""

from app.services.expense.approval_service import (
    ApprovalChain,
    ApprovalStep,
    ExpenseApprovalService,
    ReceiptValidationResult,
)
from app.services.expense.expense_notifications import (
    ExpenseNotificationService,
    get_expense_notification_service,
)
from app.services.expense.expense_posting_adapter import (
    ExpensePostingAdapter,
    ExpensePostingResult,
)
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
    SubmitClaimResult,
)
from app.services.expense.limit_service import (
    EligibleApprover,
    EvaluationResult,
    ExpenseApproverLimitNotFoundError,
    ExpenseLimitExceededError,
    ExpenseLimitRuleNotFoundError,
    ExpenseLimitService,
    ExpenseLimitServiceError,
)
from app.services.expense.limit_web import expense_limit_web_service
from app.services.expense.web import (
    ExpenseClaimsWebService,
    expense_claims_web_service,
)

__all__ = [
    # Expense Service
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
