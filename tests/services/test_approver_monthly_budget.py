"""
Tests for approver monthly budget enforcement.

Verifies that monthly_approval_budget on ExpenseApproverLimit is enforced
when an approver attempts to approve a claim.  The budget is keyed on the
claim's expense_date month (not the current date), which handles backdated
expenses correctly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.expense.limit_service import (
    ApproverBudgetExhaustedError,
    ExpenseLimitService,
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


def _make_employee(employee_id, *, grade_id=None, designation_id=None):
    emp = MagicMock()
    emp.employee_id = employee_id
    emp.grade_id = grade_id
    emp.designation_id = designation_id
    return emp


def _make_approver_limit(
    org_id,
    *,
    scope_type="EMPLOYEE",
    scope_id=None,
    monthly_budget=None,
    is_active=True,
):
    """Build a mock ExpenseApproverLimit row."""
    limit = MagicMock()
    limit.organization_id = org_id
    limit.scope_type = scope_type
    limit.scope_id = scope_id
    limit.monthly_approval_budget = monthly_budget
    limit.is_active = is_active
    return limit


# ---------------------------------------------------------------------------
# Tests for check_approver_monthly_budget
# ---------------------------------------------------------------------------


class TestApproverMonthlyBudget:
    """Core budget enforcement on the limit service."""

    def test_passes_when_no_budget_configured(self, org_id, approver_id):
        """No monthly_approval_budget → unlimited, no error."""
        db = MagicMock()
        employee = _make_employee(approver_id)
        db.get.return_value = employee

        svc = ExpenseLimitService(db)

        with patch.object(svc, "_get_approver_monthly_budget", return_value=None):
            # Should not raise
            svc.check_approver_monthly_budget(
                org_id, approver_id, Decimal("999999"), date(2026, 1, 15)
            )

    def test_passes_when_within_budget(self, org_id, approver_id):
        """Claim fits within remaining budget."""
        db = MagicMock()
        employee = _make_employee(approver_id)
        db.get.return_value = employee
        # Already used 200k of 500k budget
        db.scalar.return_value = Decimal("200000")

        svc = ExpenseLimitService(db)

        with patch.object(
            svc, "_get_approver_monthly_budget", return_value=Decimal("500000")
        ):
            # 200k used + 50k new = 250k ≤ 500k → pass
            svc.check_approver_monthly_budget(
                org_id, approver_id, Decimal("50000"), date(2026, 1, 15)
            )

    def test_passes_when_exactly_at_budget(self, org_id, approver_id):
        """Claim exactly fills remaining budget (boundary)."""
        db = MagicMock()
        employee = _make_employee(approver_id)
        db.get.return_value = employee
        db.scalar.return_value = Decimal("400000")

        svc = ExpenseLimitService(db)

        with patch.object(
            svc, "_get_approver_monthly_budget", return_value=Decimal("500000")
        ):
            # 400k used + 100k new = 500k exactly → pass
            svc.check_approver_monthly_budget(
                org_id, approver_id, Decimal("100000"), date(2026, 1, 15)
            )

    def test_raises_when_budget_exceeded(self, org_id, approver_id):
        """Claim would exceed budget → ApproverBudgetExhaustedError."""
        db = MagicMock()
        employee = _make_employee(approver_id)
        db.get.return_value = employee
        db.scalar.return_value = Decimal("450000")

        svc = ExpenseLimitService(db)

        with patch.object(
            svc, "_get_approver_monthly_budget", return_value=Decimal("500000")
        ):
            with pytest.raises(ApproverBudgetExhaustedError) as exc_info:
                # 450k used + 100k new = 550k > 500k → blocked
                svc.check_approver_monthly_budget(
                    org_id, approver_id, Decimal("100000"), date(2026, 1, 15)
                )

            assert exc_info.value.budget == Decimal("500000")
            assert exc_info.value.used == Decimal("450000")
            assert exc_info.value.claim_amount == Decimal("100000")
            assert "January 2026" in exc_info.value.expense_month

    def test_raises_when_budget_fully_used(self, org_id, approver_id):
        """Zero remaining budget blocks any new approval."""
        db = MagicMock()
        employee = _make_employee(approver_id)
        db.get.return_value = employee
        db.scalar.return_value = Decimal("500000")

        svc = ExpenseLimitService(db)

        with patch.object(
            svc, "_get_approver_monthly_budget", return_value=Decimal("500000")
        ):
            with pytest.raises(ApproverBudgetExhaustedError):
                svc.check_approver_monthly_budget(
                    org_id, approver_id, Decimal("1"), date(2026, 3, 10)
                )

    def test_skips_when_approver_not_found(self, org_id, approver_id):
        """Unknown approver → skip check (defensive)."""
        db = MagicMock()
        db.get.return_value = None  # Employee not found

        svc = ExpenseLimitService(db)

        # Should not raise
        svc.check_approver_monthly_budget(
            org_id, approver_id, Decimal("999999"), date(2026, 1, 15)
        )


class TestBackdatedExpenses:
    """Budget deduction keyed on expense_date month, not current date."""

    def test_january_expense_deducts_from_january_budget(self, org_id, approver_id):
        """Expense dated January submitted in February still uses January bucket."""
        db = MagicMock()
        employee = _make_employee(approver_id)
        db.get.return_value = employee
        # Jan already has 300k used
        db.scalar.return_value = Decimal("300000")

        svc = ExpenseLimitService(db)

        with patch.object(
            svc, "_get_approver_monthly_budget", return_value=Decimal("500000")
        ):
            # 300k + 100k = 400k ≤ 500k → pass
            svc.check_approver_monthly_budget(
                org_id, approver_id, Decimal("100000"), date(2026, 1, 20)
            )

    def test_different_months_have_independent_budgets(self, org_id, approver_id):
        """February expense uses February budget, not January."""
        db = MagicMock()
        employee = _make_employee(approver_id)
        db.get.return_value = employee
        # Feb has 0 used
        db.scalar.return_value = Decimal("0")

        svc = ExpenseLimitService(db)

        with patch.object(
            svc, "_get_approver_monthly_budget", return_value=Decimal("500000")
        ):
            svc.check_approver_monthly_budget(
                org_id, approver_id, Decimal("500000"), date(2026, 2, 1)
            )

    def test_december_to_january_rollover(self, org_id, approver_id):
        """December expense uses December budget, not January of next year."""
        db = MagicMock()
        employee = _make_employee(approver_id)
        db.get.return_value = employee
        db.scalar.return_value = Decimal("490000")

        svc = ExpenseLimitService(db)

        with patch.object(
            svc, "_get_approver_monthly_budget", return_value=Decimal("500000")
        ):
            with pytest.raises(ApproverBudgetExhaustedError) as exc_info:
                svc.check_approver_monthly_budget(
                    org_id, approver_id, Decimal("20000"), date(2025, 12, 15)
                )
            assert "December 2025" in exc_info.value.expense_month


class TestBudgetLookupPriority:
    """_get_approver_monthly_budget resolves in employee → grade → designation order."""

    def test_employee_specific_budget_takes_precedence(self, org_id):
        """Employee-scoped budget wins over grade-scoped."""
        db = MagicMock()
        employee_id = uuid4()
        employee = _make_employee(employee_id, grade_id=uuid4())

        # First call: employee-specific returns 100k
        db.scalar.return_value = Decimal("100000")

        svc = ExpenseLimitService(db)
        result = svc._get_approver_monthly_budget(org_id, employee)
        assert result == Decimal("100000")

    def test_falls_through_to_grade_when_no_employee_budget(self, org_id):
        """When no employee-specific budget, falls to grade."""
        db = MagicMock()
        grade_id = uuid4()
        employee = _make_employee(uuid4(), grade_id=grade_id)

        # First call returns None (no employee budget), second returns grade budget
        db.scalar.side_effect = [None, Decimal("200000")]

        svc = ExpenseLimitService(db)
        result = svc._get_approver_monthly_budget(org_id, employee)
        assert result == Decimal("200000")

    def test_falls_through_to_designation(self, org_id):
        """When no employee or grade budget, falls to designation."""
        db = MagicMock()
        designation_id = uuid4()
        employee = _make_employee(
            uuid4(), grade_id=uuid4(), designation_id=designation_id
        )

        # employee → None, grade → None, designation → 300k
        db.scalar.side_effect = [None, None, Decimal("300000")]

        svc = ExpenseLimitService(db)
        result = svc._get_approver_monthly_budget(org_id, employee)
        assert result == Decimal("300000")

    def test_returns_none_when_no_budget_anywhere(self, org_id):
        """No budget configured at any level → None (unlimited)."""
        db = MagicMock()
        employee = _make_employee(uuid4(), grade_id=uuid4(), designation_id=uuid4())

        db.scalar.side_effect = [None, None, None]

        svc = ExpenseLimitService(db)
        result = svc._get_approver_monthly_budget(org_id, employee)
        assert result is None


class TestIntegrationWithApproveFlow:
    """Test _validate_approver_monthly_budget on ExpenseService."""

    def test_approve_calls_budget_check(self, org_id, approver_id, claim_id):
        """approve_claim invokes monthly budget validation."""
        from app.services.expense.expense_service import ExpenseService

        db = MagicMock()
        claim = MagicMock()
        claim.claim_id = claim_id
        claim.organization_id = org_id
        claim.status = MagicMock()
        claim.status.__eq__ = lambda self, other: False  # not APPROVED/PAID
        claim.total_claimed_amount = Decimal("50000")
        claim.claim_date = date(2026, 1, 15)

        svc = ExpenseService(db)

        with (
            patch.object(svc, "_validate_approver_authority"),
            patch.object(svc, "_validate_approver_monthly_budget") as mock_budget,
        ):
            # Call the private method directly
            svc._validate_approver_monthly_budget(org_id, claim, approver_id)

            mock_budget.assert_called_once_with(org_id, claim, approver_id)

    def test_budget_exhausted_blocks_approval(self, org_id, approver_id, claim_id):
        """ApproverBudgetExhaustedError propagates from approve flow."""
        from app.services.expense.expense_service import ExpenseService

        db = MagicMock()
        claim = MagicMock()
        claim.claim_id = claim_id
        claim.organization_id = org_id
        claim.total_claimed_amount = Decimal("100000")
        claim.claim_date = date(2026, 2, 10)

        svc = ExpenseService(db)

        with patch(
            "app.services.expense.limit_service.ExpenseLimitService"
        ) as MockLimitSvc:
            mock_instance = MockLimitSvc.return_value
            mock_instance.check_approver_monthly_budget.side_effect = (
                ApproverBudgetExhaustedError(
                    budget=Decimal("500000"),
                    used=Decimal("450000"),
                    claim_amount=Decimal("100000"),
                    expense_month="February 2026",
                )
            )

            with pytest.raises(ApproverBudgetExhaustedError):
                svc._validate_approver_monthly_budget(org_id, claim, approver_id)


class TestExceptionMessage:
    """ApproverBudgetExhaustedError produces a clear, informative message."""

    def test_message_includes_all_amounts(self):
        err = ApproverBudgetExhaustedError(
            budget=Decimal("500000"),
            used=Decimal("350000"),
            claim_amount=Decimal("200000"),
            expense_month="January 2026",
        )
        msg = str(err)
        assert "January 2026" in msg
        assert "500,000.00" in msg
        assert "350,000.00" in msg
        assert "150,000.00" in msg  # remaining = 500k - 350k
        assert "200,000.00" in msg

    def test_attributes_stored(self):
        err = ApproverBudgetExhaustedError(
            budget=Decimal("100"),
            used=Decimal("90"),
            claim_amount=Decimal("20"),
            expense_month="March 2026",
        )
        assert err.budget == Decimal("100")
        assert err.used == Decimal("90")
        assert err.claim_amount == Decimal("20")
        assert err.expense_month == "March 2026"
