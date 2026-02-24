"""
Tests for approver budget adjustment feature.

Verifies that one-time monthly budget adjustments:
- Can be created, listed, and deleted
- Are applied additively to the base monthly budget during enforcement
- Enforce unique constraint (one adjustment per limit per month)
- Are irrelevant when no base budget is configured (unlimited)
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
def limit_id():
    return uuid4()


@pytest.fixture
def person_id():
    return uuid4()


def _make_employee(employee_id, *, grade_id=None, designation_id=None):
    emp = MagicMock()
    emp.employee_id = employee_id
    emp.grade_id = grade_id
    emp.designation_id = designation_id
    return emp


# ---------------------------------------------------------------------------
# Budget adjustment CRUD
# ---------------------------------------------------------------------------


class TestCreateBudgetAdjustment:
    """Service.create_budget_adjustment creates an adjustment record."""

    def test_create_adjustment_persists(self, org_id, limit_id, person_id):
        """Successfully creates an adjustment and flushes."""
        db = MagicMock()
        # get_approver_limit returns a valid limit
        mock_limit = MagicMock()
        mock_limit.approver_limit_id = limit_id

        svc = ExpenseLimitService(db)

        with patch.object(svc, "get_approver_limit", return_value=mock_limit):
            # No existing adjustment for this month
            db.scalar.return_value = None

            svc.create_budget_adjustment(
                org_id,
                limit_id,
                month=date(2026, 2, 15),  # Should normalize to 2026-02-01
                additional_amount=Decimal("50000"),
                reason="Quarter-end surge",
                adjusted_by_id=person_id,
            )

        db.add.assert_called_once()
        db.flush.assert_called_once()
        added_obj = db.add.call_args[0][0]
        assert added_obj.adjustment_month == date(2026, 2, 1)
        assert added_obj.additional_amount == Decimal("50000")
        assert added_obj.reason == "Quarter-end surge"

    def test_duplicate_month_raises_error(self, org_id, limit_id, person_id):
        """Creating a second adjustment for the same month raises ValueError."""
        db = MagicMock()
        mock_limit = MagicMock()
        mock_limit.approver_limit_id = limit_id

        svc = ExpenseLimitService(db)

        with patch.object(svc, "get_approver_limit", return_value=mock_limit):
            # Existing adjustment found
            db.scalar.return_value = MagicMock()

            with pytest.raises(ValueError, match="already exists"):
                svc.create_budget_adjustment(
                    org_id,
                    limit_id,
                    month=date(2026, 2, 1),
                    additional_amount=Decimal("10000"),
                    reason="Duplicate attempt",
                    adjusted_by_id=person_id,
                )

    def test_normalizes_month_to_first_of_month(self, org_id, limit_id, person_id):
        """Any day in the month gets normalized to the 1st."""
        db = MagicMock()
        mock_limit = MagicMock()

        svc = ExpenseLimitService(db)

        with patch.object(svc, "get_approver_limit", return_value=mock_limit):
            db.scalar.return_value = None  # No duplicate

            svc.create_budget_adjustment(
                org_id,
                limit_id,
                month=date(2026, 3, 28),
                additional_amount=Decimal("25000"),
                reason="End of March",
                adjusted_by_id=person_id,
            )

        added_obj = db.add.call_args[0][0]
        assert added_obj.adjustment_month == date(2026, 3, 1)


class TestListBudgetAdjustments:
    """Service.list_budget_adjustments returns all adjustments for a limit."""

    def test_returns_list(self, org_id, limit_id):
        db = MagicMock()
        adj1 = MagicMock()
        adj2 = MagicMock()
        db.scalars.return_value.all.return_value = [adj1, adj2]

        svc = ExpenseLimitService(db)
        result = svc.list_budget_adjustments(org_id, limit_id)

        assert len(result) == 2
        assert result[0] is adj1
        assert result[1] is adj2


class TestDeleteBudgetAdjustment:
    """Service.delete_budget_adjustment removes an adjustment."""

    def test_delete_existing(self, org_id):
        db = MagicMock()
        adj = MagicMock()
        db.scalar.return_value = adj
        adjustment_id = uuid4()

        svc = ExpenseLimitService(db)
        svc.delete_budget_adjustment(org_id, adjustment_id)

        db.delete.assert_called_once_with(adj)
        db.flush.assert_called_once()

    def test_delete_nonexistent_raises(self, org_id):
        db = MagicMock()
        db.scalar.return_value = None

        svc = ExpenseLimitService(db)
        with pytest.raises(ValueError, match="not found"):
            svc.delete_budget_adjustment(org_id, uuid4())


class TestGetBudgetAdjustmentForMonth:
    """Service.get_budget_adjustment_for_month returns the adjustment amount."""

    def test_returns_amount_when_exists(self, limit_id):
        db = MagicMock()
        db.scalar.return_value = Decimal("50000")

        svc = ExpenseLimitService(db)
        result = svc.get_budget_adjustment_for_month(limit_id, date(2026, 2, 15))
        assert result == Decimal("50000")

    def test_returns_zero_when_no_adjustment(self, limit_id):
        db = MagicMock()
        db.scalar.return_value = None

        svc = ExpenseLimitService(db)
        result = svc.get_budget_adjustment_for_month(limit_id, date(2026, 2, 15))
        assert result == Decimal("0")


# ---------------------------------------------------------------------------
# Budget check with adjustments
# ---------------------------------------------------------------------------


class TestBudgetCheckWithAdjustment:
    """check_approver_monthly_budget uses adjustments when present."""

    def test_adjustment_makes_room_for_claim(self, org_id, approver_id):
        """Base budget: 200k, used: 180k, claim: 50k = over base.
        But adjustment of +50k → effective 250k → 180k + 50k = 230k ≤ 250k → passes.
        """
        db = MagicMock()
        employee = _make_employee(approver_id)
        db.get.return_value = employee

        limit_id = uuid4()

        svc = ExpenseLimitService(db)

        with (
            patch.object(
                svc,
                "_get_approver_monthly_budget",
                return_value=(Decimal("200000"), limit_id),
            ),
            patch.object(
                svc,
                "get_budget_adjustment_for_month",
                return_value=Decimal("50000"),
            ),
        ):
            # used = 180k
            db.scalar.return_value = Decimal("180000")

            # Should NOT raise — 180k + 50k = 230k ≤ 250k
            svc.check_approver_monthly_budget(
                org_id, approver_id, Decimal("50000"), date(2026, 2, 15)
            )

    def test_adjustment_not_enough_still_fails(self, org_id, approver_id):
        """Adjustment helps but isn't enough: still raises error."""
        db = MagicMock()
        employee = _make_employee(approver_id)
        db.get.return_value = employee

        limit_id = uuid4()

        svc = ExpenseLimitService(db)

        with (
            patch.object(
                svc,
                "_get_approver_monthly_budget",
                return_value=(Decimal("200000"), limit_id),
            ),
            patch.object(
                svc,
                "get_budget_adjustment_for_month",
                return_value=Decimal("20000"),
            ),
        ):
            # used = 180k, claim = 50k, effective = 220k
            # 180k + 50k = 230k > 220k → blocked
            db.scalar.return_value = Decimal("180000")

            with pytest.raises(ApproverBudgetExhaustedError) as exc_info:
                svc.check_approver_monthly_budget(
                    org_id, approver_id, Decimal("50000"), date(2026, 2, 15)
                )

            # Effective budget should include adjustment
            assert exc_info.value.budget == Decimal("220000")

    def test_no_adjustment_uses_base_budget(self, org_id, approver_id):
        """When no adjustment exists (returns 0), base budget alone is used."""
        db = MagicMock()
        employee = _make_employee(approver_id)
        db.get.return_value = employee

        limit_id = uuid4()

        svc = ExpenseLimitService(db)

        with (
            patch.object(
                svc,
                "_get_approver_monthly_budget",
                return_value=(Decimal("200000"), limit_id),
            ),
            patch.object(
                svc,
                "get_budget_adjustment_for_month",
                return_value=Decimal("0"),
            ),
        ):
            db.scalar.return_value = Decimal("150000")

            # 150k + 60k = 210k > 200k → blocked
            with pytest.raises(ApproverBudgetExhaustedError) as exc_info:
                svc.check_approver_monthly_budget(
                    org_id, approver_id, Decimal("60000"), date(2026, 2, 15)
                )

            assert exc_info.value.budget == Decimal("200000")

    def test_unlimited_budget_ignores_adjustment(self, org_id, approver_id):
        """When no base budget (unlimited), adjustments are irrelevant."""
        db = MagicMock()
        employee = _make_employee(approver_id)
        db.get.return_value = employee

        svc = ExpenseLimitService(db)

        with patch.object(svc, "_get_approver_monthly_budget", return_value=None):
            # Should pass with any amount — unlimited
            svc.check_approver_monthly_budget(
                org_id, approver_id, Decimal("999999"), date(2026, 2, 15)
            )


class TestBudgetLookupReturnsTuple:
    """_get_approver_monthly_budget now returns (budget, limit_id) tuple."""

    def test_employee_specific_returns_tuple(self, org_id):
        db = MagicMock()
        employee_id = uuid4()
        limit_id = uuid4()
        employee = _make_employee(employee_id, grade_id=uuid4())

        # db.execute().first() returns a Row-like tuple
        mock_row = (Decimal("100000"), limit_id)
        db.execute.return_value.first.return_value = mock_row

        svc = ExpenseLimitService(db)
        result = svc._get_approver_monthly_budget(org_id, employee)
        assert result == (Decimal("100000"), limit_id)

    def test_returns_none_when_no_budget(self, org_id):
        db = MagicMock()
        employee = _make_employee(uuid4(), grade_id=uuid4(), designation_id=uuid4())

        # All lookups return None (no matching row)
        db.execute.return_value.first.return_value = None

        svc = ExpenseLimitService(db)
        result = svc._get_approver_monthly_budget(org_id, employee)
        assert result is None

    def test_falls_through_to_grade(self, org_id):
        db = MagicMock()
        grade_id = uuid4()
        limit_id = uuid4()
        employee = _make_employee(uuid4(), grade_id=grade_id)

        # First call (employee) → None, second (grade) → row
        db.execute.return_value.first.side_effect = [
            None,
            (Decimal("200000"), limit_id),
        ]

        svc = ExpenseLimitService(db)
        result = svc._get_approver_monthly_budget(org_id, employee)
        assert result == (Decimal("200000"), limit_id)
