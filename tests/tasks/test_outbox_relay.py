from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.tasks.outbox_relay import (
    _HANDLERS,
    _get_handler,
    handle_ledger_posting_completed,
    register_handler,
)

# ---------------------------------------------------------------------------
# Handler registry tests
# ---------------------------------------------------------------------------


def test_register_and_get_handler() -> None:
    name = f"test.event.{uuid4().hex[:8]}"

    def _handler(db, event):  # noqa: ANN001, ANN202
        pass

    register_handler(name, _handler)
    assert _get_handler(name) is _handler
    # cleanup
    _HANDLERS.pop(name, None)


def test_get_handler_returns_none_for_unknown() -> None:
    assert _get_handler("nonexistent.event.name") is None


# ---------------------------------------------------------------------------
# handle_ledger_posting_completed tests
# ---------------------------------------------------------------------------


def _make_event(
    batch_id: str | None = None, org_id: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        event_id=uuid4(),
        event_name="ledger.posting.completed",
        payload={
            "batch_id": batch_id or str(uuid4()),
            "organization_id": org_id or str(uuid4()),
        },
    )


def _make_line(
    account_id: str | None = None, fiscal_period_id: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        ledger_line_id=uuid4(),
        account_id=account_id or uuid4(),
        fiscal_period_id=fiscal_period_id or uuid4(),
        posting_batch_id=uuid4(),
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0"),
        business_unit_id=None,
        cost_center_id=None,
        project_id=None,
        segment_id=None,
    )


@patch(
    "app.services.finance.gl.account_balance.AccountBalanceService.update_balance_for_posting"
)
def test_handler_missing_batch_id(mock_update: MagicMock) -> None:
    db = MagicMock()
    event = SimpleNamespace(
        event_id=uuid4(),
        event_name="ledger.posting.completed",
        payload={"organization_id": str(uuid4())},
    )
    # Should return early without touching db
    handle_ledger_posting_completed(db, event)
    db.scalars.assert_not_called()
    mock_update.assert_not_called()


@patch(
    "app.services.finance.gl.account_balance.AccountBalanceService.update_balance_for_posting"
)
def test_handler_no_lines_for_batch(mock_update: MagicMock) -> None:
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    event = _make_event()

    handle_ledger_posting_completed(db, event)
    # scalars called but no balance updates
    db.scalars.assert_called_once()
    mock_update.assert_not_called()


@patch(
    "app.services.finance.gl.account_balance.AccountBalanceService.update_balance_for_posting"
)
def test_handler_updates_balances(mock_update: MagicMock) -> None:
    db = MagicMock()
    line1 = _make_line()
    line2 = _make_line()
    db.scalars.return_value.all.return_value = [line1, line2]
    event = _make_event()

    handle_ledger_posting_completed(db, event)

    assert mock_update.call_count == 2


@patch(
    "app.services.finance.gl.account_balance.AccountBalanceService.update_balance_for_posting",
    side_effect=[RuntimeError("boom"), None],
)
def test_handler_continues_on_per_line_failure(mock_update: MagicMock) -> None:
    db = MagicMock()
    line1 = _make_line()
    line2 = _make_line()
    db.scalars.return_value.all.return_value = [line1, line2]
    event = _make_event()

    # Should NOT raise — catches per-line exceptions
    handle_ledger_posting_completed(db, event)

    assert mock_update.call_count == 2


@patch(
    "app.services.finance.gl.account_balance.AccountBalanceService.update_balance_for_posting"
)
def test_handler_passes_decimal_zero_for_none_amounts(mock_update: MagicMock) -> None:
    db = MagicMock()
    line = _make_line()
    line.debit_amount = None
    line.credit_amount = None
    db.scalars.return_value.all.return_value = [line]
    event = _make_event()

    handle_ledger_posting_completed(db, event)

    call_kwargs = mock_update.call_args.kwargs
    assert call_kwargs["debit_amount"] == Decimal("0")
    assert call_kwargs["credit_amount"] == Decimal("0")
    assert isinstance(call_kwargs["debit_amount"], Decimal)
    assert isinstance(call_kwargs["credit_amount"], Decimal)


# ---------------------------------------------------------------------------
# relay_outbox_events tests
# ---------------------------------------------------------------------------


@patch("app.tasks.outbox_relay.SessionLocal")
@patch("app.tasks.outbox_relay.OutboxPublisher")
def test_relay_no_pending_events(
    mock_publisher_cls: MagicMock, mock_session_local: MagicMock
) -> None:
    from app.tasks.outbox_relay import relay_outbox_events

    db = MagicMock()
    mock_session_local.return_value.__enter__ = MagicMock(return_value=db)
    mock_session_local.return_value.__exit__ = MagicMock(return_value=False)
    mock_publisher_cls.get_pending_events.return_value = []

    result = relay_outbox_events()

    assert result == {"published": 0, "skipped": 0, "failed": 0, "errors": []}


@patch("app.tasks.outbox_relay.SessionLocal")
@patch("app.tasks.outbox_relay.OutboxPublisher")
@patch("app.tasks.outbox_relay._get_handler")
def test_relay_dispatches_to_handler(
    mock_get_handler: MagicMock,
    mock_publisher_cls: MagicMock,
    mock_session_local: MagicMock,
) -> None:
    from app.tasks.outbox_relay import relay_outbox_events

    db = MagicMock()
    mock_session_local.return_value.__enter__ = MagicMock(return_value=db)
    mock_session_local.return_value.__exit__ = MagicMock(return_value=False)

    event = SimpleNamespace(
        event_id=uuid4(),
        event_name="ledger.posting.completed",
    )
    mock_publisher_cls.get_pending_events.return_value = [event]
    mock_handler = MagicMock()
    mock_get_handler.return_value = mock_handler

    result = relay_outbox_events()

    assert result["published"] == 1
    mock_handler.assert_called_once_with(db, event)
    mock_publisher_cls.mark_published.assert_called_once_with(db, event.event_id)


@patch("app.tasks.outbox_relay.SessionLocal")
@patch("app.tasks.outbox_relay.OutboxPublisher")
@patch("app.tasks.outbox_relay._get_handler")
def test_relay_skips_unregistered_event(
    mock_get_handler: MagicMock,
    mock_publisher_cls: MagicMock,
    mock_session_local: MagicMock,
) -> None:
    from app.tasks.outbox_relay import relay_outbox_events

    db = MagicMock()
    mock_session_local.return_value.__enter__ = MagicMock(return_value=db)
    mock_session_local.return_value.__exit__ = MagicMock(return_value=False)

    event = SimpleNamespace(
        event_id=uuid4(),
        event_name="unknown.event.type",
    )
    mock_publisher_cls.get_pending_events.return_value = [event]
    mock_get_handler.return_value = None

    result = relay_outbox_events()

    assert result["skipped"] == 1
    assert result["published"] == 0
    mock_publisher_cls.mark_published.assert_called_once_with(db, event.event_id)


@patch("app.tasks.outbox_relay.SessionLocal")
@patch("app.tasks.outbox_relay.OutboxPublisher")
@patch("app.tasks.outbox_relay._get_handler")
def test_relay_handles_handler_failure(
    mock_get_handler: MagicMock,
    mock_publisher_cls: MagicMock,
    mock_session_local: MagicMock,
) -> None:
    from app.tasks.outbox_relay import relay_outbox_events

    db = MagicMock()
    mock_session_local.return_value.__enter__ = MagicMock(return_value=db)
    mock_session_local.return_value.__exit__ = MagicMock(return_value=False)

    event = SimpleNamespace(
        event_id=uuid4(),
        event_name="ledger.posting.completed",
    )
    mock_publisher_cls.get_pending_events.return_value = [event]
    mock_get_handler.return_value = MagicMock(
        side_effect=RuntimeError("handler exploded")
    )

    result = relay_outbox_events()

    assert result["failed"] == 1
    assert len(result["errors"]) == 1
    db.rollback.assert_called_once()
    mock_publisher_cls.handle_retry.assert_called_once_with(
        db, event.event_id, "handler exploded"
    )


# ---------------------------------------------------------------------------
# cleanup_published_outbox_events tests
# ---------------------------------------------------------------------------


@patch("app.tasks.outbox_relay.SessionLocal")
def test_cleanup_deletes_old_published(mock_session_local: MagicMock) -> None:
    from app.tasks.outbox_relay import cleanup_published_outbox_events

    db = MagicMock()
    db.execute.return_value.rowcount = 42
    mock_session_local.return_value.__enter__ = MagicMock(return_value=db)
    mock_session_local.return_value.__exit__ = MagicMock(return_value=False)

    result = cleanup_published_outbox_events(retention_days=7, batch_size=100)

    assert result == {"deleted": 42}
    db.commit.assert_called_once()
