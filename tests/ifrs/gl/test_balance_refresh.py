"""Tests for balance invalidation and refresh services."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.finance.gl.balance_invalidation import BalanceInvalidationService
from app.services.finance.gl.balance_refresh import BalanceRefreshService


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _ExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def test_invalidate_creates_queue_entry_when_missing() -> None:
    db = MagicMock()
    db.scalar.return_value = None

    service = BalanceInvalidationService(db)
    service.invalidate(uuid4(), uuid4(), uuid4())

    db.execute.assert_called_once()
    db.add.assert_called_once()
    db.flush.assert_called_once()


def test_invalidate_reopens_existing_queue_entry() -> None:
    db = MagicMock()
    existing = SimpleNamespace(
        invalidated_at=datetime(2026, 1, 1, tzinfo=UTC),
        processed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.scalar.return_value = existing

    service = BalanceInvalidationService(db)
    service.invalidate(uuid4(), uuid4(), uuid4())

    assert existing.processed_at is None
    db.add.assert_not_called()
    db.flush.assert_called_once()


def test_invalidate_batch_deduplicates_keys() -> None:
    db = MagicMock()
    service = BalanceInvalidationService(db)
    service.invalidate = MagicMock()

    org = uuid4()
    account_a = uuid4()
    account_b = uuid4()
    period = uuid4()
    count = service.invalidate_batch(
        [
            (org, account_a, period),
            (org, account_a, period),
            (org, account_b, period),
        ]
    )

    assert count == 2
    assert service.invalidate.call_count == 2


def test_process_queue_sets_processed_and_counts_errors() -> None:
    db = MagicMock()
    q1 = SimpleNamespace(
        organization_id=uuid4(),
        account_id=uuid4(),
        fiscal_period_id=uuid4(),
        processed_at=None,
    )
    q2 = SimpleNamespace(
        organization_id=uuid4(),
        account_id=uuid4(),
        fiscal_period_id=uuid4(),
        processed_at=None,
    )
    db.scalars.return_value = _ScalarResult([q1, q2])

    service = BalanceRefreshService(db)
    service._refresh_balance = MagicMock(side_effect=[1, RuntimeError("boom")])

    results = service.process_queue(batch_size=10)

    assert results["processed"] == 2
    assert results["refreshed"] == 1
    assert results["errors"] == 1
    assert q1.processed_at is not None
    assert q2.processed_at is None
    db.flush.assert_called_once()


def test_refresh_balance_updates_existing_row() -> None:
    db = MagicMock()
    org_id = uuid4()
    account_id = uuid4()
    period_id = uuid4()

    row = SimpleNamespace(
        business_unit_id=None,
        cost_center_id=None,
        project_id=None,
        segment_id=None,
        period_debit=Decimal("125.00"),
        period_credit=Decimal("25.00"),
        tx_count=2,
    )
    existing = SimpleNamespace(
        business_unit_id=None,
        cost_center_id=None,
        project_id=None,
        segment_id=None,
        currency_code="USD",
        opening_debit=Decimal("50.00"),
        opening_credit=Decimal("0.00"),
        period_debit=Decimal("0.00"),
        period_credit=Decimal("0.00"),
        closing_debit=Decimal("50.00"),
        closing_credit=Decimal("0.00"),
        net_balance=Decimal("50.00"),
        transaction_count=0,
        is_stale=True,
        stale_since=datetime.now(UTC),
        refresh_count=3,
        last_updated_at=datetime.now(UTC),
    )
    db.execute.return_value = _ExecuteResult([row])
    db.scalars.return_value = _ScalarResult([existing])

    with patch(
        "app.services.finance.gl.balance_refresh.org_context_service.get_functional_currency",
        return_value="USD",
    ):
        refreshed = BalanceRefreshService(db)._refresh_balance(
            org_id, account_id, period_id
        )

    assert refreshed == 1
    assert existing.period_debit == Decimal("125.00")
    assert existing.period_credit == Decimal("25.00")
    assert existing.closing_debit == Decimal("175.00")
    assert existing.closing_credit == Decimal("25.00")
    assert existing.net_balance == Decimal("150.00")
    assert existing.transaction_count == 2
    assert existing.is_stale is False
    assert existing.stale_since is None
    assert existing.refresh_count == 4
