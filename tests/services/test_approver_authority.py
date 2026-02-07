"""
Tests for approver authority validation in ExpenseService.approve_claim().

Verifies that approval limits configured via ExpenseApproverLimit are
enforced when an approver attempts to approve a claim.
"""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.expense.expense_claim import ExpenseClaimStatus
from app.services.expense.expense_service import (
    ApproverAuthorityError,
    ExpenseService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def approver_id():
    return uuid4()


@pytest.fixture
def claim_id():
    return uuid4()


def _make_claim(
    claim_id,
    org_id,
    *,
    status=ExpenseClaimStatus.SUBMITTED,
    total_claimed=Decimal("50000.00"),
):
    """Build a mock ExpenseClaim with the fields approve_claim() touches."""
    claim = MagicMock()
    claim.claim_id = claim_id
    claim.organization_id = org_id
    claim.status = status
    claim.total_claimed_amount = total_claimed
    claim.total_approved_amount = None
    claim.advance_adjusted = Decimal("0")
    claim.items = []
    claim.employee = None
    claim.employee_id = None
    return claim


def _make_employee(employee_id, org_id, *, grade_id=None, designation_id=None):
    """Build a mock Employee."""
    emp = MagicMock()
    emp.employee_id = employee_id
    emp.organization_id = org_id
    emp.grade_id = grade_id
    emp.designation_id = designation_id
    return emp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestApproverAuthorityValidation:
    """Tests for _validate_approver_authority and its integration in approve_claim."""

    def test_approval_blocked_when_limit_exceeded(self, org_id, approver_id, claim_id):
        """Approver with max_approval_amount < claim total is blocked."""
        db = MagicMock()
        claim = _make_claim(claim_id, org_id, total_claimed=Decimal("50000.00"))
        employee = _make_employee(approver_id, org_id)

        db.get.return_value = employee

        svc = ExpenseService(db)

        with patch(
            "app.services.expense.approval_service.ExpenseApprovalService"
        ) as MockApprovalSvc:
            mock_instance = MockApprovalSvc.return_value
            # Approver can only approve up to 20,000
            mock_instance._get_approver_max_amount.return_value = Decimal("20000.00")

            with pytest.raises(ApproverAuthorityError) as exc_info:
                svc._validate_approver_authority(org_id, claim, approver_id)

            assert exc_info.value.claim_amount == Decimal("50000.00")
            assert exc_info.value.max_approval_amount == Decimal("20000.00")
            assert "20,000.00" in str(exc_info.value)
            assert "50,000.00" in str(exc_info.value)

    def test_approval_allowed_when_within_limit(self, org_id, approver_id, claim_id):
        """Approver with sufficient authority can proceed."""
        db = MagicMock()
        claim = _make_claim(claim_id, org_id, total_claimed=Decimal("15000.00"))
        employee = _make_employee(approver_id, org_id)

        db.get.return_value = employee

        svc = ExpenseService(db)

        with patch(
            "app.services.expense.approval_service.ExpenseApprovalService"
        ) as MockApprovalSvc:
            mock_instance = MockApprovalSvc.return_value
            mock_instance._get_approver_max_amount.return_value = Decimal("20000.00")

            # Should NOT raise
            svc._validate_approver_authority(org_id, claim, approver_id)

    def test_approval_allowed_when_exactly_at_limit(
        self, org_id, approver_id, claim_id
    ):
        """Approver whose limit exactly matches the claim amount can approve."""
        db = MagicMock()
        claim = _make_claim(claim_id, org_id, total_claimed=Decimal("20000.00"))
        employee = _make_employee(approver_id, org_id)

        db.get.return_value = employee

        svc = ExpenseService(db)

        with patch(
            "app.services.expense.approval_service.ExpenseApprovalService"
        ) as MockApprovalSvc:
            mock_instance = MockApprovalSvc.return_value
            mock_instance._get_approver_max_amount.return_value = Decimal("20000.00")

            # Should NOT raise (equal is within limit)
            svc._validate_approver_authority(org_id, claim, approver_id)

    def test_approval_allowed_when_no_limit_configured(
        self, org_id, approver_id, claim_id
    ):
        """When no approval limit is configured, approval is allowed (backward compat)."""
        db = MagicMock()
        claim = _make_claim(claim_id, org_id, total_claimed=Decimal("999999.99"))
        employee = _make_employee(approver_id, org_id)

        db.get.return_value = employee

        svc = ExpenseService(db)

        with patch(
            "app.services.expense.approval_service.ExpenseApprovalService"
        ) as MockApprovalSvc:
            mock_instance = MockApprovalSvc.return_value
            mock_instance._get_approver_max_amount.return_value = None

            # No limit configured → allow
            svc._validate_approver_authority(org_id, claim, approver_id)

    def test_approval_allowed_when_approver_not_found(
        self, org_id, approver_id, claim_id
    ):
        """When the approver employee record is not found, allow (defensive)."""
        db = MagicMock()
        claim = _make_claim(claim_id, org_id)
        db.get.return_value = None  # Employee not found

        svc = ExpenseService(db)

        # Should NOT raise — let downstream handle unknown approver
        svc._validate_approver_authority(org_id, claim, approver_id)

    def test_approval_skipped_when_no_approver_id(self, org_id, claim_id):
        """approve_claim() skips authority check when approver_id is None."""
        db = MagicMock()
        claim = _make_claim(claim_id, org_id)

        # Mock get_claim to return our claim
        svc = ExpenseService(db)
        svc.get_claim = MagicMock(return_value=claim)
        svc._begin_action = MagicMock(return_value=True)
        svc._set_action_status = MagicMock()

        with patch.object(svc, "_validate_approver_authority") as mock_validate:
            svc.approve_claim(org_id, claim_id, approver_id=None)
            # Should never be called when approver_id is None
            mock_validate.assert_not_called()

    def test_approve_claim_calls_authority_check(self, org_id, approver_id, claim_id):
        """approve_claim() calls _validate_approver_authority when approver_id given."""
        db = MagicMock()
        claim = _make_claim(claim_id, org_id)

        svc = ExpenseService(db)
        svc.get_claim = MagicMock(return_value=claim)
        svc._begin_action = MagicMock(return_value=True)
        svc._set_action_status = MagicMock()

        with patch.object(svc, "_validate_approver_authority") as mock_validate:
            svc.approve_claim(org_id, claim_id, approver_id=approver_id)
            mock_validate.assert_called_once_with(org_id, claim, approver_id)

    def test_approve_claim_raises_authority_error(self, org_id, approver_id, claim_id):
        """approve_claim() propagates ApproverAuthorityError to callers."""
        db = MagicMock()
        claim = _make_claim(claim_id, org_id, total_claimed=Decimal("100000.00"))

        svc = ExpenseService(db)
        svc.get_claim = MagicMock(return_value=claim)
        svc._begin_action = MagicMock(return_value=True)
        svc._set_action_status = MagicMock()

        with patch.object(
            svc,
            "_validate_approver_authority",
            side_effect=ApproverAuthorityError(
                Decimal("100000.00"), Decimal("25000.00")
            ),
        ):
            with pytest.raises(ApproverAuthorityError):
                svc.approve_claim(org_id, claim_id, approver_id=approver_id)


class TestSelfApprovalPrevention:
    """Tests that an employee cannot approve their own expense claim."""

    def test_self_approval_blocked(self, org_id, approver_id, claim_id):
        """approve_claim() raises when approver is the claimant."""
        from app.services.expense.expense_service import ExpenseServiceError

        person_id = uuid4()
        db = MagicMock()

        # Both employee records share the same person_id
        approver_emp = MagicMock()
        approver_emp.person_id = person_id

        claimant_emp = MagicMock()
        claimant_emp.person_id = person_id

        employee_id = uuid4()
        claim = _make_claim(claim_id, org_id)
        claim.employee_id = employee_id

        # db.get returns approver_emp for approver_id, claimant_emp for employee_id
        def mock_get(model, pk):
            if pk == approver_id:
                return approver_emp
            if pk == employee_id:
                return claimant_emp
            return None

        db.get = MagicMock(side_effect=mock_get)

        svc = ExpenseService(db)
        svc.get_claim = MagicMock(return_value=claim)
        svc._begin_action = MagicMock(return_value=True)
        svc._set_action_status = MagicMock()

        with patch.object(svc, "_validate_approver_authority"):
            with pytest.raises(ExpenseServiceError, match="Cannot approve your own"):
                svc.approve_claim(org_id, claim_id, approver_id=approver_id)

    def test_different_person_allowed(self, org_id, approver_id, claim_id):
        """approve_claim() succeeds when approver is a different person."""
        db = MagicMock()

        approver_emp = MagicMock()
        approver_emp.person_id = uuid4()

        claimant_emp = MagicMock()
        claimant_emp.person_id = uuid4()

        employee_id = uuid4()
        claim = _make_claim(claim_id, org_id)
        claim.employee_id = employee_id

        def mock_get(model, pk):
            if pk == approver_id:
                return approver_emp
            if pk == employee_id:
                return claimant_emp
            return None

        db.get = MagicMock(side_effect=mock_get)

        svc = ExpenseService(db)
        svc.get_claim = MagicMock(return_value=claim)
        svc._begin_action = MagicMock(return_value=True)
        svc._set_action_status = MagicMock()

        with patch.object(svc, "_validate_approver_authority"):
            result = svc.approve_claim(org_id, claim_id, approver_id=approver_id)
            assert result.status == ExpenseClaimStatus.APPROVED


class TestApproverAuthorityErrorMessage:
    """Tests for the ApproverAuthorityError exception itself."""

    def test_error_message_formatting(self):
        err = ApproverAuthorityError(Decimal("50000.00"), Decimal("20000.00"))
        msg = str(err)
        assert "20,000.00" in msg
        assert "50,000.00" in msg
        assert "escalate" in msg.lower()

    def test_error_attributes(self):
        err = ApproverAuthorityError(Decimal("75000"), Decimal("10000"))
        assert err.claim_amount == Decimal("75000")
        assert err.max_approval_amount == Decimal("10000")

    def test_error_is_expense_service_error(self):
        """ApproverAuthorityError inherits from ExpenseServiceError."""
        from app.services.expense.expense_service import ExpenseServiceError

        err = ApproverAuthorityError(Decimal("1"), Decimal("0"))
        assert isinstance(err, ExpenseServiceError)
