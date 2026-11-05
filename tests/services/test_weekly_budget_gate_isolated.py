"""Exercise the real budget gate and its payment/reset query boundaries."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.services.expense.limit_service import ExpenseLimitServiceError


def production_methods():
    from app.services.expense.limit_service import ExpenseLimitService

    return SimpleNamespace(
        **{
            name: getattr(ExpenseLimitService, name)
            for name in (
                "check_approver_weekly_budget",
                "_weekly_paid_usage",
                "_get_weekly_budget_window",
            )
        }
    )


@pytest.mark.parametrize(
    "amount,committed,blocked",
    [("3000", "0", False), ("3001", "0", True), ("2000", "2000", True)],
)
def test_197k_paid_leaves_only_3k(amount, committed, blocked):
    methods = production_methods()
    svc = SimpleNamespace(
        db=MagicMock(),
        _get_approver_weekly_budget=lambda *a: (Decimal("200000"), uuid4()),
        get_approver_weekly_budget_balance=lambda *a, **kw: SimpleNamespace(
            used=Decimal("197000"), budget=Decimal("200000"), usage_label="week"
        ),
        _committed_transfers=lambda *a: Decimal(committed),
    )
    if blocked:
        with pytest.raises(ExpenseLimitServiceError, match="exhausted"):
            methods.check_approver_weekly_budget(svc, uuid4(), uuid4(), Decimal(amount))
    else:
        methods.check_approver_weekly_budget(svc, uuid4(), uuid4(), Decimal(amount))


def test_missing_budget_blocks():
    svc = SimpleNamespace(db=MagicMock(), _get_approver_weekly_budget=lambda *a: None)
    with pytest.raises(ExpenseLimitServiceError, match="No weekly budget"):
        production_methods().check_approver_weekly_budget(
            svc, uuid4(), uuid4(), Decimal("1")
        )


def test_reset_lookup_is_bounded_by_payment_time():
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    svc = SimpleNamespace(
        get_weekly_boundary=lambda org, dt: datetime(2026, 10, 26, tzinfo=UTC),
        get_latest_weekly_reset=MagicMock(return_value=None),
    )
    production_methods()._get_weekly_budget_window(
        svc, uuid4(), uuid4(), uuid4(), as_of=now
    )
    assert svc.get_latest_weekly_reset.call_args.kwargs["to_datetime"] == now


def test_usage_sums_actual_payments_and_bounds_payment_dates():
    svc = SimpleNamespace(db=MagicMock())
    production_methods()._weekly_paid_usage(
        svc,
        uuid4(),
        uuid4(),
        datetime(2026, 10, 26, tzinfo=UTC),
        datetime(2026, 11, 1, tzinfo=UTC),
    )
    sql = str(svc.db.scalar.call_args.args[0])
    assert "sum(expense.expense_claim.amount_paid)" in sql
    assert "net_payable_amount" not in sql
    assert "paid_on >=" in sql and "paid_on <=" in sql
    assert "paid_at >=" in sql and "paid_at <=" in sql


def test_same_day_reset_uses_payment_action_time():
    svc = SimpleNamespace(db=MagicMock())
    production_methods()._weekly_paid_usage(
        svc,
        uuid4(),
        uuid4(),
        datetime(2026, 10, 28, 12, tzinfo=UTC),
        datetime(2026, 10, 29, tzinfo=UTC),
    )
    sql = str(svc.db.scalar.call_args.args[0])
    assert "paid_at >=" in sql and "paid_at IS NULL" in sql
