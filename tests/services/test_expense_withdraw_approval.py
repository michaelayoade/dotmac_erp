"""Tests for withdrawing an expense approval before payment activity starts."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.expense import ExpenseClaimStatus
from app.services.expense.expense_service import ExpenseService, ExpenseServiceError


def _approved_claim(org_id, approver_id):
    return SimpleNamespace(
        claim_id=uuid4(),
        organization_id=org_id,
        claim_number="EXP-TEST-001",
        status=ExpenseClaimStatus.APPROVED,
        approver_id=approver_id,
        payment_reference=None,
        supplier_invoice_id=None,
        journal_entry_id=None,
        reimbursement_journal_id=None,
        paid_on=None,
        rejection_reason=None,
        status_changed_at=None,
    )


def test_withdraw_approval_records_reason_and_preserves_approval_history():
    org_id = uuid4()
    approver_id = uuid4()
    claim = _approved_claim(org_id, approver_id)
    db = MagicMock()
    db.scalar.return_value = None
    service = ExpenseService(db)
    service.get_claim = MagicMock(return_value=claim)
    service._begin_action = MagicMock(return_value=True)
    service._set_action_status = MagicMock()

    with patch("app.services.expense.service_claims.fire_audit_event") as audit_event:
        result = service.withdraw_approval(
            org_id,
            claim.claim_id,
            approver_id=approver_id,
            reason="Payment is no longer required.",
        )

    assert result.status == ExpenseClaimStatus.APPROVAL_WITHDRAWN
    assert result.rejection_reason == "Payment is no longer required."
    db.scalars.assert_not_called()
    audit_event.assert_called_once()


@pytest.mark.parametrize(
    "field",
    [
        "payment_reference",
        "supplier_invoice_id",
        "journal_entry_id",
        "reimbursement_journal_id",
        "paid_on",
    ],
)
def test_withdraw_approval_blocks_existing_financial_activity(field):
    org_id = uuid4()
    approver_id = uuid4()
    claim = _approved_claim(org_id, approver_id)
    setattr(claim, field, "present")
    service = ExpenseService(MagicMock())
    service.get_claim = MagicMock(return_value=claim)

    with pytest.raises(ExpenseServiceError, match="activity exists"):
        service.withdraw_approval(
            org_id,
            claim.claim_id,
            approver_id=approver_id,
            reason="No longer needed",
        )


def test_withdraw_approval_is_limited_to_original_approver():
    org_id = uuid4()
    claim = _approved_claim(org_id, uuid4())
    service = ExpenseService(MagicMock())
    service.get_claim = MagicMock(return_value=claim)

    with pytest.raises(ExpenseServiceError, match="Only the approver"):
        service.withdraw_approval(
            org_id,
            claim.claim_id,
            approver_id=uuid4(),
            reason="No longer needed",
        )
