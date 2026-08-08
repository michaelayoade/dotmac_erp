"""
Expense Management Services.

This package is imported by many modules (and tests) for submodules like
`app.services.expense.limit_service`. Keep this `__init__` import-light to avoid
pulling in the entire expense dependency graph during import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from app.services.expense.approval_service import (  # noqa: F401
        ApprovalChain,
        ApprovalStep,
        ExpenseApprovalService,
        ReceiptValidationResult,
    )
    from app.services.expense.expense_notifications import (  # noqa: F401
        ExpenseNotificationService,
        get_expense_notification_service,
    )
    from app.services.expense.expense_posting_adapter import (  # noqa: F401
        ExpensePostingAdapter,
        ExpensePostingResult,
    )
    from app.services.expense.expense_service import (  # noqa: F401
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
    from app.services.expense.limit_service import (  # noqa: F401
        EligibleApprover,
        EvaluationResult,
        ExpenseApproverLimitNotFoundError,
        ExpenseLimitExceededError,
        ExpenseLimitRuleNotFoundError,
        ExpenseLimitService,
        ExpenseLimitServiceError,
    )
    from app.services.expense.limit_web import expense_limit_web_service  # noqa: F401
    from app.services.expense.web import (  # noqa: F401
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


_NAME_TO_MODULE = {
    # approval_service
    "ApprovalChain": "approval_service",
    "ApprovalStep": "approval_service",
    "ExpenseApprovalService": "approval_service",
    "ReceiptValidationResult": "approval_service",
    # expense_notifications
    "ExpenseNotificationService": "expense_notifications",
    "get_expense_notification_service": "expense_notifications",
    # expense_posting_adapter
    "ExpensePostingAdapter": "expense_posting_adapter",
    "ExpensePostingResult": "expense_posting_adapter",
    # expense_service
    "ExpenseService": "expense_service",
    "ExpenseServiceError": "expense_service",
    "ExpenseCategoryNotFoundError": "expense_service",
    "ExpenseClaimNotFoundError": "expense_service",
    "ExpenseClaimStatusError": "expense_service",
    "ExpenseLimitBlockedError": "expense_service",
    "ApproverAuthorityError": "expense_service",
    "CashAdvanceNotFoundError": "expense_service",
    "CorporateCardNotFoundError": "expense_service",
    "CardTransactionNotFoundError": "expense_service",
    "SubmitClaimResult": "expense_service",
    # limit_service
    "ExpenseLimitService": "limit_service",
    "ExpenseLimitServiceError": "limit_service",
    "ExpenseLimitRuleNotFoundError": "limit_service",
    "ExpenseApproverLimitNotFoundError": "limit_service",
    "ExpenseLimitExceededError": "limit_service",
    "EvaluationResult": "limit_service",
    "EligibleApprover": "limit_service",
    # web
    "ExpenseClaimsWebService": "web",
    "expense_claims_web_service": "web",
    # limit_web
    "expense_limit_web_service": "limit_web",
}


def __getattr__(name: str) -> Any:  # pragma: no cover
    module_name = _NAME_TO_MODULE.get(name)
    if not module_name:
        raise AttributeError(name)
    module = __import__(f"{__name__}.{module_name}", fromlist=[name])
    return getattr(module, name)


from app.services.setting_domain_declaration import ModuleSettingDomains  # noqa: E402

# Setting domain(s) this module owns — expense routing and approval.
# Validated by `app.services.setting_domains` at startup and at every write;
# see that module for why ownership lives here rather than in a central list.
SETTING_DOMAINS = ModuleSettingDomains(setting_domains=("expense",))
