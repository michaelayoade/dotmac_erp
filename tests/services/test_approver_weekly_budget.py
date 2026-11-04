"""Tests for weekly approver budget enforcement and manual resets."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.expense.limit_service import (
    ApproverWeeklyBudgetExhaustedError,
    ExpenseLimitService,
)


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def approver_id():
    return uuid4()


@pytest.fixture
def reviewer_person_id():
    return uuid4()


def _make_employee(employee_id, *, person_id=None, grade_id=None, designation_id=None):
    emp = MagicMock()
    emp.employee_id = employee_id
    emp.person_id = person_id or uuid4()
    emp.grade_id = grade_id
    emp.designation_id = designation_id
    return emp


class TestWeeklyBudgetCheck:
    @pytest.mark.parametrize(
        "amount,committed,blocked",
        [
            ("3000", "0", False),
            ("3001", "0", True),
            ("2000", "2000", True),
        ],
    )
    def test_paid_balance_gate(self, org_id, approver_id, amount, committed, blocked):
        db = MagicMock()
        svc = ExpenseLimitService(db)
        with (
            patch.object(
                svc,
                "_get_approver_weekly_budget",
                return_value=(Decimal("200000"), uuid4()),
            ),
            patch.object(
                svc,
                "get_approver_weekly_budget_balance",
                return_value=SimpleNamespace(
                    budget=Decimal("200000"), used=Decimal("197000"), usage_label="week"
                ),
            ),
            patch.object(svc, "_committed_transfers", return_value=Decimal(committed)),
        ):
            if blocked:
                with pytest.raises(ApproverWeeklyBudgetExhaustedError):
                    svc.check_approver_weekly_budget(
                        org_id, approver_id, Decimal(amount)
                    )
            else:
                svc.check_approver_weekly_budget(org_id, approver_id, Decimal(amount))

    def test_future_reset_is_excluded(self, org_id, approver_id):
        svc = ExpenseLimitService(MagicMock())
        as_of = datetime(2026, 11, 1, 12, tzinfo=UTC)
        with patch.object(svc, "get_latest_weekly_reset", return_value=None) as reset:
            start, _ = svc._get_weekly_budget_window(
                org_id, approver_id, uuid4(), as_of=as_of
            )
        assert start == datetime(2026, 10, 26, tzinfo=UTC)
        assert reset.call_args.kwargs["to_datetime"] == as_of


class TestWeeklyReset:
    def test_reset_created_for_approver(self, org_id, approver_id, reviewer_person_id):
        db = MagicMock()
        db.get.return_value = _make_employee(approver_id)
        db.scalar.return_value = 4

        svc = ExpenseLimitService(db)
        limit_id = uuid4()
        with patch.object(
            svc,
            "_get_approver_weekly_budget",
            return_value=(Decimal("500000"), limit_id),
        ):
            reset = svc.create_weekly_budget_reset(
                org_id,
                approver_id=approver_id,
                reviewed_by_id=reviewer_person_id,
                reset_reason="Reviewed all approvals",
            )

        db.add.assert_called_once()
        db.flush.assert_called_once()
        assert reset.approver_id == approver_id
        assert reset.approver_limit_id == limit_id

    def test_reset_requires_budget_configuration(
        self, org_id, approver_id, reviewer_person_id
    ):
        db = MagicMock()
        db.get.return_value = _make_employee(approver_id)

        svc = ExpenseLimitService(db)
        with patch.object(svc, "_get_approver_weekly_budget", return_value=None):
            with pytest.raises(ValueError, match="weekly budget"):
                svc.create_weekly_budget_reset(
                    org_id,
                    approver_id=approver_id,
                    reviewed_by_id=reviewer_person_id,
                    reset_reason="No budget",
                )


class TestWeeklyBudgetLookupPriority:
    def test_falls_through_to_role_when_no_employee_grade_designation(self, org_id):
        db = MagicMock()
        role_limit_id = uuid4()
        employee = _make_employee(uuid4(), grade_id=uuid4(), designation_id=uuid4())

        # employee -> None, grade -> None, designation -> None, role -> row
        db.execute.return_value.first.side_effect = [
            None,
            None,
            None,
            (Decimal("400000"), role_limit_id),
        ]

        svc = ExpenseLimitService(db)
        result = svc._get_approver_weekly_budget(org_id, employee)
        assert result == (Decimal("400000"), role_limit_id)

    def test_designation_takes_precedence_over_role(self, org_id):
        db = MagicMock()
        designation_limit_id = uuid4()
        employee = _make_employee(uuid4(), grade_id=uuid4(), designation_id=uuid4())

        # employee -> None, grade -> None, designation -> row (role should not run)
        db.execute.return_value.first.side_effect = [
            None,
            None,
            (Decimal("300000"), designation_limit_id),
        ]

        svc = ExpenseLimitService(db)
        result = svc._get_approver_weekly_budget(org_id, employee)
        assert result == (Decimal("300000"), designation_limit_id)
        assert db.execute.call_count == 3
