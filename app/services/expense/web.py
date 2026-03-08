"""Public expense web-service facade composed from focused mixin modules."""

from app.services.expense.web_advances import ExpenseAdvancesWebMixin
from app.services.expense.web_categories_reports import (
    ExpenseCategoriesReportsWebMixin,
)
from app.services.expense.web_claims import ExpenseClaimsWebMixin
from app.services.expense.web_common import ExpenseWebCommonMixin
from app.services.storage import get_storage
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

__all__ = [
    "ExpenseClaimsWebService",
    "expense_claims_web_service",
    "WebAuthContext",
    "base_context",
    "get_storage",
    "templates",
]


class ExpenseClaimsWebService(
    ExpenseAdvancesWebMixin,
    ExpenseCategoriesReportsWebMixin,
    ExpenseClaimsWebMixin,
    ExpenseWebCommonMixin,
):
    """Composite expense web-service preserving the existing API."""

    pass


expense_claims_web_service = ExpenseClaimsWebService()
