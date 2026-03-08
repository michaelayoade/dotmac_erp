from app.services.expense import (
    ExpenseClaimsWebService,
    ExpenseService,
    expense_claims_web_service,
)
from app.services.expense.dashboard_web import (
    ExpenseDashboardService,
    expense_dashboard_service,
)
from app.services.expense.expense_service import (
    REPORTABLE_EXPENSE_CLAIM_STATUSES,
)
from app.services.expense.service_common import (
    REPORTABLE_EXPENSE_CLAIM_STATUSES as COMMON_REPORTABLE_STATUSES,
)


def test_expense_service_facade_re_exports_reportable_statuses():
    assert REPORTABLE_EXPENSE_CLAIM_STATUSES == COMMON_REPORTABLE_STATUSES


def test_expense_service_instances_use_public_facades():
    assert isinstance(expense_claims_web_service, ExpenseClaimsWebService)
    assert isinstance(expense_dashboard_service, ExpenseDashboardService)
    assert ExpenseService.__name__ == "ExpenseService"
