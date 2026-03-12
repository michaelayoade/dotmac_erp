from decimal import Decimal
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, unquote, urlparse

from app.services.expense.expense_service import ExpenseService, ExpenseServiceError
from app.services.expense.limit_service import ApproverBudgetExhaustedError
from app.services.expense.web import ExpenseClaimsWebService


def _make_auth():
    auth = MagicMock()
    auth.organization_id = "00000000-0000-0000-0000-000000000001"
    auth.person_id = "00000000-0000-0000-0000-000000000010"
    auth.has_any_permission.return_value = True
    return auth


def _extract_error_message(location: str) -> str:
    params = parse_qs(urlparse(location).query)
    return unquote(
        params.get("error_message", params.get("error", [""]))[0].replace("+", " ")
    )


def test_approve_claim_response_surfaces_expense_service_error():
    db = MagicMock()
    auth = _make_auth()
    approver = MagicMock()
    approver.employee_id = "00000000-0000-0000-0000-000000000099"
    db.scalars.return_value.first.return_value = approver

    err = ExpenseServiceError("Cannot approve your own expense claim")
    with patch.object(ExpenseService, "approve_claim", side_effect=err):
        response = ExpenseClaimsWebService.approve_claim_response(
            claim_id="11111111-1111-1111-1111-111111111111",
            auth=auth,
            db=db,
            form_data=None,
        )

    assert response.status_code == 303
    assert (
        _extract_error_message(response.headers["location"])
        == "Cannot approve your own expense claim"
    )
    db.rollback.assert_called_once()


def test_approve_claim_response_surfaces_approver_budget_error():
    db = MagicMock()
    auth = _make_auth()
    approver = MagicMock()
    approver.employee_id = "00000000-0000-0000-0000-000000000099"
    db.scalars.return_value.first.return_value = approver

    err = ApproverBudgetExhaustedError(
        budget=Decimal("500.00"),
        used=Decimal("450.00"),
        claim_amount=Decimal("100.00"),
        expense_month="February 2026",
    )
    with patch.object(ExpenseService, "approve_claim", side_effect=err):
        response = ExpenseClaimsWebService.approve_claim_response(
            claim_id="11111111-1111-1111-1111-111111111111",
            auth=auth,
            db=db,
            form_data=None,
        )

    assert response.status_code == 303
    assert (
        _extract_error_message(response.headers["location"])
        == "Monthly approval budget for February 2026 exhausted. Budget: 500.00, Used: 450.00, Remaining: 50.00, Claim: 100.00."
    )
    db.rollback.assert_called_once()


def test_approve_claim_response_surfaces_step_assignment_error():
    db = MagicMock()
    auth = _make_auth()
    approver = MagicMock()
    approver.employee_id = "00000000-0000-0000-0000-000000000099"
    db.scalars.return_value.first.return_value = approver

    with patch.object(
        ExpenseService,
        "approve_claim",
        side_effect=ValueError("Approver is not assigned to the current approval step"),
    ):
        response = ExpenseClaimsWebService.approve_claim_response(
            claim_id="11111111-1111-1111-1111-111111111111",
            auth=auth,
            db=db,
            form_data=None,
        )

    assert response.status_code == 303
    assert (
        _extract_error_message(response.headers["location"])
        == "You cannot approve this claim yet because it is assigned to a different approval step."
    )
    db.rollback.assert_called_once()
