from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.expense.expense_claim import ExpenseClaimStatus
from app.services.expense.expense_service import ExpenseService


def _make_claim(
    *,
    status: ExpenseClaimStatus,
    approver_id=None,
    total_claimed: Decimal = Decimal("50000.00"),
    total_approved: Decimal | None = Decimal("45000.00"),
):
    claim = MagicMock()
    claim.claim_id = uuid4()
    claim.organization_id = uuid4()
    claim.status = status
    claim.approver_id = approver_id
    claim.total_claimed_amount = total_claimed
    claim.total_approved_amount = total_approved
    claim.net_payable_amount = total_approved
    claim.advance_adjusted = Decimal("0")
    claim.claim_number = "EXP-TEST-001"
    claim.claim_date = date(2026, 4, 9)
    claim.items = []
    claim.employee = None
    claim.employee_id = None
    claim.payment_reference = None
    claim.paid_on = None
    return claim


def test_approve_claim_checks_balance_without_marking_paid():
    db = MagicMock()
    org_id = uuid4()
    approver_id = uuid4()
    claim = _make_claim(status=ExpenseClaimStatus.SUBMITTED, approver_id=approver_id)

    svc = ExpenseService(db)
    svc.get_claim = MagicMock(return_value=claim)
    svc._begin_action = MagicMock(return_value=True)
    svc._set_action_status = MagicMock()

    with (
        patch.object(svc, "_validate_approver_authority"),
        patch(
            "app.services.expense.limit_service.ExpenseLimitService.check_approver_weekly_budget"
        ) as mock_weekly_budget,
    ):
        svc.approve_claim(org_id, claim.claim_id, approver_id=approver_id)

    mock_weekly_budget.assert_called_once()
    assert claim.status == ExpenseClaimStatus.APPROVED
    assert claim.paid_on is None


def test_mark_paid_validates_budget_using_paid_event():
    db = MagicMock()
    org_id = uuid4()
    approver_id = uuid4()
    claim = _make_claim(status=ExpenseClaimStatus.APPROVED, approver_id=approver_id)

    svc = ExpenseService(db)
    svc.get_claim = MagicMock(return_value=claim)
    svc._begin_action = MagicMock(return_value=True)
    svc._set_action_status = MagicMock()

    with (
        patch.object(svc, "_validate_approver_weekly_budget") as mock_weekly_budget,
        patch.object(svc, "has_payment_in_flight", return_value=False),
    ):
        svc.mark_paid(
            org_id,
            claim.claim_id,
            payment_reference="PAY-001",
            payment_date=date(2026, 4, 10),
            send_notification=False,
        )

    _, _, weekly_approver_id = mock_weekly_budget.call_args.args
    assert weekly_approver_id == approver_id
    assert mock_weekly_budget.call_args.kwargs["approval_at"] == claim.paid_at
    assert claim.paid_on == date(2026, 4, 10)
