"""Public expense service facade assembled from focused mixin modules."""

from app.services.expense.service_advances import ExpenseAdvanceMixin
from app.services.expense.service_cards import ExpenseCardMixin
from app.services.expense.service_categories import ExpenseCategoryMixin
from app.services.expense.service_claims import ExpenseClaimMixin
from app.services.expense.service_common import (
    CLAIM_STATUS_TRANSITIONS,
    REPORTABLE_EXPENSE_CLAIM_STATUSES,
    STALE_ACTION_MINUTES,
    ApproverAuthorityError,
    CardTransactionNotFoundError,
    CashAdvanceNotFoundError,
    CorporateCardNotFoundError,
    ExpenseCategoryNotFoundError,
    ExpenseClaimNotFoundError,
    ExpenseClaimStatusError,
    ExpenseLimitBlockedError,
    ExpenseServiceBase,
    ExpenseServiceError,
    SubmitClaimResult,
)
from app.services.expense.service_reports import ExpenseReportingMixin

__all__ = [
    "ApproverAuthorityError",
    "CLAIM_STATUS_TRANSITIONS",
    "CardTransactionNotFoundError",
    "CashAdvanceNotFoundError",
    "CorporateCardNotFoundError",
    "ExpenseCategoryNotFoundError",
    "ExpenseClaimNotFoundError",
    "ExpenseClaimStatusError",
    "ExpenseLimitBlockedError",
    "ExpenseService",
    "ExpenseServiceError",
    "REPORTABLE_EXPENSE_CLAIM_STATUSES",
    "STALE_ACTION_MINUTES",
    "SubmitClaimResult",
]


class ExpenseService(
    ExpenseReportingMixin,
    ExpenseCardMixin,
    ExpenseAdvanceMixin,
    ExpenseClaimMixin,
    ExpenseCategoryMixin,
    ExpenseServiceBase,
):
    """Composite expense service preserving the existing public API."""

    pass
