"""Unit tests for the claim/deliver/settle outbox relay (E3).

These cover the applied-result canaries that don't need PostgreSQL:

- an unknown (undeclared) event never publishes — it dead-letters as
  UNSUPPORTED;
- a declared no-consequence event settles published with a recorded note;
- a failed ledger line fails the whole event (no swallowed per-line
  errors);
- a commit failure emits no success acknowledgement.

The SKIP LOCKED / lease-expiry concurrency canaries live in
tests/integration/platform/test_outbox_applied_result_canaries.py (they
require a real PostgreSQL database).
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.tasks.outbox_relay import (
    _HANDLERS,
    _ClaimedEvent,
    _get_handler,
    NonRetryableEventError,
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
def test_handler_missing_batch_id_is_non_retryable(mock_update: MagicMock) -> None:
    db = MagicMock()
    event = SimpleNamespace(
        event_id=uuid4(),
        event_name="ledger.posting.completed",
        payload={"organization_id": str(uuid4())},
    )
    # A malformed payload can never succeed — fail closed, don't publish.
    with pytest.raises(NonRetryableEventError):
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
def test_handler_raises_on_per_line_failure(mock_update: MagicMock) -> None:
    """CANARY: one failed ledger line prevents whole-event success.

    The old handler swallowed per-line exceptions and returned normally,
    letting the relay settle the event as PUBLISHED with balances only
    partially applied. update_balance_for_posting is additive (re-applying
    a line double-counts), so the failure MUST raise and roll back the
    whole delivery transaction — no unrecorded partial can settle.
    """
    db = MagicMock()
    line1 = _make_line()
    line2 = _make_line()
    db.scalars.return_value.all.return_value = [line1, line2]
    event = _make_event()

    with pytest.raises(RuntimeError, match="boom"):
        handle_ledger_posting_completed(db, event)

    # Failed on the first line and stopped — no further application.
    assert mock_update.call_count == 1


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
# relay_outbox_events tests (claim/deliver/settle)
# ---------------------------------------------------------------------------


def _claimed(
    event_name: str = "ledger.posting.completed",
    producer_module: str = "GL",
    organization_id: str | None = None,
) -> _ClaimedEvent:
    return _ClaimedEvent(
        event_id=uuid4(),
        event_name=event_name,
        producer_module=producer_module,
        organization_id=organization_id or str(uuid4()),
    )


@contextmanager
def _mock_session(db: MagicMock):
    yield db


def _patch_sessions(db: MagicMock):
    """Patch both session context managers to yield the given mock."""
    return (
        patch(
            "app.tasks.outbox_relay.cross_org_session",
            return_value=_mock_session(db),
        ),
        patch(
            "app.tasks.outbox_relay.session_for_org",
            return_value=_mock_session(db),
        ),
    )


def test_relay_no_pending_events() -> None:
    from app.tasks.outbox_relay import relay_outbox_events

    with patch("app.tasks.outbox_relay._claim_batch", return_value=(None, [])):
        result = relay_outbox_events()

    assert result["published"] == 0
    assert result["errors"] == []


def test_relay_dispatches_to_handler_and_settles_in_same_txn() -> None:
    from app.tasks.outbox_relay import relay_outbox_events

    db = MagicMock()
    token = uuid4()
    claimed = _claimed()
    event = SimpleNamespace(event_id=claimed.event_id, event_name=claimed.event_name)
    db.get.return_value = event

    mock_handler = MagicMock()
    cross_patch, org_patch = _patch_sessions(db)
    with (
        patch(
            "app.tasks.outbox_relay._claim_batch",
            return_value=(token, [claimed]),
        ),
        patch("app.tasks.outbox_relay._get_handler", return_value=mock_handler),
        patch("app.tasks.outbox_relay.OutboxPublisher") as mock_publisher,
        cross_patch,
        org_patch,
    ):
        result = relay_outbox_events()

    assert result["published"] == 1
    mock_handler.assert_called_once_with(db, event)
    mock_publisher.mark_published.assert_called_once_with(
        db, claimed.event_id, claim_token=token
    )
    # Settlement and handler mutations share one commit.
    db.commit.assert_called_once()


def test_relay_unknown_event_never_publishes() -> None:
    """CANARY: an undeclared unknown event dead-letters as UNSUPPORTED.

    The old relay marked events without a handler as PUBLISHED ("skipped").
    Fail closed: unknown consequences are never recorded as applied.
    """
    from app.tasks.outbox_relay import relay_outbox_events

    db = MagicMock()
    token = uuid4()
    claimed = _claimed(event_name="unknown.event.type", producer_module="GL")

    cross_patch, org_patch = _patch_sessions(db)
    with (
        patch(
            "app.tasks.outbox_relay._claim_batch",
            return_value=(token, [claimed]),
        ),
        patch("app.tasks.outbox_relay.OutboxPublisher") as mock_publisher,
        cross_patch,
        org_patch,
    ):
        result = relay_outbox_events()

    assert result["unsupported"] == 1
    assert result["published"] == 0
    mock_publisher.mark_unsupported.assert_called_once_with(
        db, claimed.event_id, claim_token=token
    )
    mock_publisher.mark_published.assert_not_called()


def test_relay_declared_no_consequence_settles_with_note() -> None:
    """A producer-declared observe-only event settles published with a
    recorded no-op note (documented decision, not a silent skip)."""
    from app.models.finance.platform.event_outbox import TerminalReason
    from app.tasks.outbox_relay import relay_outbox_events

    db = MagicMock()
    token = uuid4()
    claimed = _claimed(event_name="hook.custom.event", producer_module="hooks")

    cross_patch, org_patch = _patch_sessions(db)
    with (
        patch(
            "app.tasks.outbox_relay._claim_batch",
            return_value=(token, [claimed]),
        ),
        patch("app.tasks.outbox_relay.OutboxPublisher") as mock_publisher,
        cross_patch,
        org_patch,
    ):
        result = relay_outbox_events()

    assert result["no_consequence"] == 1
    assert result["unsupported"] == 0
    mock_publisher.mark_published.assert_called_once_with(
        db,
        claimed.event_id,
        claim_token=token,
        terminal_reason=TerminalReason.DECLARED_NO_CONSEQUENCE,
    )


def test_relay_handler_failure_rolls_back_and_schedules_retry() -> None:
    from app.models.finance.platform.event_outbox import EventStatus
    from app.tasks.outbox_relay import relay_outbox_events

    db = MagicMock()
    token = uuid4()
    claimed = _claimed()
    event = SimpleNamespace(event_id=claimed.event_id, event_name=claimed.event_name)
    db.get.return_value = event

    cross_patch, org_patch = _patch_sessions(db)
    with (
        patch(
            "app.tasks.outbox_relay._claim_batch",
            return_value=(token, [claimed]),
        ),
        patch(
            "app.tasks.outbox_relay._get_handler",
            return_value=MagicMock(side_effect=RuntimeError("handler exploded")),
        ),
        patch("app.tasks.outbox_relay.OutboxPublisher") as mock_publisher,
        cross_patch,
        org_patch,
    ):
        mock_publisher.handle_retry.return_value = SimpleNamespace(
            status=EventStatus.FAILED
        )
        result = relay_outbox_events()

    assert result["retried"] == 1
    assert result["published"] == 0
    assert len(result["errors"]) == 1
    db.rollback.assert_called_once()
    mock_publisher.handle_retry.assert_called_once_with(
        db,
        claimed.event_id,
        claim_token=token,
        error_message="handler exploded",
        error_class="RuntimeError",
    )
    mock_publisher.mark_published.assert_not_called()


def test_relay_commit_failure_emits_no_success() -> None:
    """CANARY: a commit failure never records success.

    The handler and mark_published run, but the transaction commit fails —
    the event must NOT count as published (it stays claimed and is
    redelivered after lease expiry).
    """
    from app.tasks.outbox_relay import relay_outbox_events

    db = MagicMock()
    db.commit.side_effect = OperationalError("COMMIT", {}, Exception("db gone"))
    token = uuid4()
    claimed = _claimed()
    event = SimpleNamespace(event_id=claimed.event_id, event_name=claimed.event_name)
    db.get.return_value = event

    cross_patch, org_patch = _patch_sessions(db)
    with (
        patch(
            "app.tasks.outbox_relay._claim_batch",
            return_value=(token, [claimed]),
        ),
        patch("app.tasks.outbox_relay._get_handler", return_value=MagicMock()),
        patch("app.tasks.outbox_relay.OutboxPublisher") as mock_publisher,
        cross_patch,
        org_patch,
    ):
        result = relay_outbox_events()

    assert result["published"] == 0
    assert result["commit_failed"] >= 1
    # mark_published was staged, but no success was acknowledged.
    mock_publisher.mark_published.assert_called_once()
    assert db.rollback.called


def test_relay_stale_claim_is_not_settled() -> None:
    """A claimant whose lease was reclaimed must not settle."""
    from app.services.finance.platform.outbox_publisher import StaleClaimError
    from app.tasks.outbox_relay import relay_outbox_events

    db = MagicMock()
    token = uuid4()
    claimed = _claimed()
    event = SimpleNamespace(event_id=claimed.event_id, event_name=claimed.event_name)
    db.get.return_value = event

    cross_patch, org_patch = _patch_sessions(db)
    with (
        patch(
            "app.tasks.outbox_relay._claim_batch",
            return_value=(token, [claimed]),
        ),
        patch("app.tasks.outbox_relay._get_handler", return_value=MagicMock()),
        patch("app.tasks.outbox_relay.OutboxPublisher") as mock_publisher,
        cross_patch,
        org_patch,
    ):
        mock_publisher.mark_published.side_effect = StaleClaimError("reclaimed")
        result = relay_outbox_events()

    assert result["stale_claims"] == 1
    assert result["published"] == 0
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_relay_missing_org_context_dead_letters() -> None:
    from app.tasks.outbox_relay import relay_outbox_events

    db = MagicMock()
    token = uuid4()
    claimed = _ClaimedEvent(
        event_id=uuid4(),
        event_name="ledger.posting.completed",
        producer_module="GL",
        organization_id=None,
    )

    cross_patch, org_patch = _patch_sessions(db)
    with (
        patch(
            "app.tasks.outbox_relay._claim_batch",
            return_value=(token, [claimed]),
        ),
        patch("app.tasks.outbox_relay.OutboxPublisher") as mock_publisher,
        cross_patch,
        org_patch,
    ):
        result = relay_outbox_events()

    assert result["dead"] == 1
    mock_publisher.mark_dead.assert_called_once()
    mock_publisher.mark_published.assert_not_called()


def test_relay_retries_on_operational_error() -> None:
    class RetryCalled(Exception):
        pass

    with (
        patch(
            "app.tasks.outbox_relay.cross_org_session",
            side_effect=OperationalError(
                "SELECT",
                {},
                Exception("db gone"),
            ),
        ),
        patch("app.tasks.outbox_relay.relay_outbox_events.retry") as mock_retry,
    ):
        from app.tasks.outbox_relay import relay_outbox_events

        mock_retry.side_effect = RetryCalled("retry")

        try:
            relay_outbox_events()
        except RetryCalled:
            pass

    mock_retry.assert_called_once()
    assert isinstance(mock_retry.call_args.kwargs["exc"], OperationalError)


def test_relay_does_not_retry_on_not_implemented_error() -> None:
    from app.tasks.outbox_relay import relay_outbox_events

    with (
        patch("app.tasks.outbox_relay.cross_org_session"),
        patch("app.tasks.outbox_relay.OutboxPublisher") as mock_publisher_cls,
        patch("app.tasks.outbox_relay.relay_outbox_events.retry") as mock_retry,
    ):
        mock_publisher_cls.claim_pending_events.side_effect = NotImplementedError(
            "not implemented"
        )
        with pytest.raises(NotImplementedError):
            relay_outbox_events()

    mock_retry.assert_not_called()


# ---------------------------------------------------------------------------
# cleanup_published_outbox_events tests
# ---------------------------------------------------------------------------


@patch("app.tasks.outbox_relay.cross_org_session")
def test_cleanup_deletes_old_published(mock_session_local: MagicMock) -> None:
    from app.tasks.outbox_relay import cleanup_published_outbox_events

    db = MagicMock()
    db.execute.return_value.rowcount = 42
    mock_session_local.return_value.__enter__.return_value = db
    mock_session_local.return_value.__exit__.return_value = False

    result = cleanup_published_outbox_events(retention_days=7, batch_size=100)

    assert result == {"deleted": 42}
    db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# reconcile_outbox_balance_projection tests
# ---------------------------------------------------------------------------


def test_reconciler_repairs_drift_through_canonical_writer() -> None:
    from app.tasks.outbox_relay import reconcile_outbox_balance_projection

    db = MagicMock()
    org_id, period_id = uuid4(), uuid4()
    record = {
        "organization_id": str(org_id),
        "fiscal_period_id": str(period_id),
        "drifted_accounts": 2,
        "drift": [],
        "repaired": True,
        "rebuilt_balance_records": 5,
    }

    with (
        patch(
            "app.tasks.outbox_relay.cross_org_session",
            return_value=_mock_session(db),
        ),
        patch(
            "app.tasks.outbox_relay.session_for_org",
            return_value=_mock_session(db),
        ),
        patch(
            "app.services.finance.platform.outbox_reconciler.OutboxBalanceReconciler"
            ".collect_recent_posting_scopes",
            return_value=[(org_id, period_id)],
        ),
        patch(
            "app.services.finance.platform.outbox_reconciler.OutboxBalanceReconciler"
            ".reconcile_period",
            return_value=record,
        ) as mock_reconcile,
    ):
        result = reconcile_outbox_balance_projection()

    assert result["scopes_checked"] == 1
    assert result["drift_found"] == 1
    assert result["repaired"] == 1
    mock_reconcile.assert_called_once_with(db, org_id, period_id)
    db.commit.assert_called_once()


def test_reconciler_clean_period_repairs_nothing() -> None:
    from app.tasks.outbox_relay import reconcile_outbox_balance_projection

    db = MagicMock()
    org_id, period_id = uuid4(), uuid4()
    record = {
        "organization_id": str(org_id),
        "fiscal_period_id": str(period_id),
        "drifted_accounts": 0,
        "drift": [],
        "repaired": False,
        "rebuilt_balance_records": 0,
    }

    with (
        patch(
            "app.tasks.outbox_relay.cross_org_session",
            return_value=_mock_session(db),
        ),
        patch(
            "app.tasks.outbox_relay.session_for_org",
            return_value=_mock_session(db),
        ),
        patch(
            "app.services.finance.platform.outbox_reconciler.OutboxBalanceReconciler"
            ".collect_recent_posting_scopes",
            return_value=[(org_id, period_id)],
        ),
        patch(
            "app.services.finance.platform.outbox_reconciler.OutboxBalanceReconciler"
            ".reconcile_period",
            return_value=record,
        ),
    ):
        result = reconcile_outbox_balance_projection()

    assert result["scopes_checked"] == 1
    assert result["drift_found"] == 0
    assert result["repaired"] == 0
