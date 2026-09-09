from decimal import Decimal
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, unquote, urlparse

from app.services.expense.expense_service import ExpenseService, ExpenseServiceError
from app.services.expense.limit_service import ApproverWeeklyBudgetExhaustedError
from app.services.people.self_service_web import SelfServiceWebService
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


def test_approve_claim_response_surfaces_weekly_approver_budget_error():
    db = MagicMock()
    auth = _make_auth()
    approver = MagicMock()
    approver.employee_id = "00000000-0000-0000-0000-000000000099"
    db.scalars.return_value.first.return_value = approver

    err = ApproverWeeklyBudgetExhaustedError(
        budget=Decimal("500.00"),
        used=Decimal("450.00"),
        claim_amount=Decimal("100.00"),
        period_label="2026-02-02 to 2026-02-08",
    )
    with patch.object(ExpenseService, "approve_claim", side_effect=err):
        response = ExpenseClaimsWebService.approve_claim_response(
            claim_id="11111111-1111-1111-1111-111111111111",
            auth=auth,
            db=db,
            form_data=None,
        )

    assert response.status_code == 303
    message = _extract_error_message(response.headers["location"])
    assert "approval budget is exhausted" in message
    assert "Budget: 500.00" in message
    assert "remaining: 50.00" in message
    assert "manually reset your approver budget" in message
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


def test_reject_claim_response_surfaces_expense_service_error():
    db = MagicMock()
    auth = _make_auth()
    approver = MagicMock()
    approver.employee_id = "00000000-0000-0000-0000-000000000099"
    db.scalars.return_value.first.return_value = approver

    err = ExpenseServiceError("Cannot reject your own expense claim")
    with patch.object(ExpenseService, "reject_claim", side_effect=err):
        response = ExpenseClaimsWebService.reject_claim_response(
            claim_id="11111111-1111-1111-1111-111111111111",
            reason="Not mine to approve",
            auth=auth,
            db=db,
        )

    assert response.status_code == 303
    assert (
        _extract_error_message(response.headers["location"])
        == "Cannot reject your own expense claim"
    )
    db.rollback.assert_called_once()


def test_team_expense_approve_redirects_with_budget_error_instead_of_json():
    db = MagicMock()
    auth = _make_auth()
    manager_employee_id = "00000000-0000-0000-0000-000000000099"
    claim = MagicMock()
    claim.employee_id = "00000000-0000-0000-0000-000000000123"
    report = MagicMock()
    report.employee_id = claim.employee_id

    err = ApproverWeeklyBudgetExhaustedError(
        budget=Decimal("500.00"),
        used=Decimal("450.00"),
        claim_amount=Decimal("100.00"),
        period_label="since budget tracking began; manual reset required",
    )

    service = SelfServiceWebService()
    with (
        patch.object(service, "_get_employee_id", return_value=manager_employee_id),
        patch.object(ExpenseService, "get_claim", return_value=claim),
        patch("app.services.people.self_service_web.EmployeeService") as employee_cls,
        patch.object(ExpenseService, "approve_claim", side_effect=err),
    ):
        employee_cls.return_value.list_employees.return_value.items = [report]
        response = service.team_expense_approve_response(
            auth,
            db,
            claim_id="11111111-1111-1111-1111-111111111111",
        )

    assert response.status_code == 303
    assert "/people/self/my-approvals?error=" in response.headers["location"]
    message = _extract_error_message(response.headers["location"])
    assert "approval budget is exhausted" in message
    assert "manually reset your approver budget" in message
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
