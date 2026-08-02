"""PostgreSQL canaries for the E3 outbox applied-result semantics.

These require a real PostgreSQL database (SKIP LOCKED, row locks) with the
``20260802_add_outbox_claim_lease_columns`` migration applied. They cover
the plan-mandated concurrency canaries:

- two workers cannot deliver one claim concurrently (SKIP LOCKED + token);
- worker death is repairable: lease expiry -> reclaim -> redelivery;
- a stale claimant (token mismatch after reclaim) cannot settle;
- one failed ledger line rolls back the whole event's balance updates.

The multi-worker tests use independent sessions with real commits (the
shared transactional ``db`` fixture cannot express two concurrent
transactions), so they clean up their own rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator, Iterator
from datetime import datetime, timedelta, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import Decimal

import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.finance.platform.event_outbox import (
    EventOutbox,
    EventStatus,
    TerminalReason,
)
from app.services.finance.platform.outbox_publisher import (
    OutboxPublisher,
    StaleClaimError,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _require_claim_columns(engine) -> None:
    """Skip cleanly when the E3 migration has not been applied yet."""
    insp = inspect(engine)
    try:
        columns = {
            c["name"] for c in insp.get_columns("event_outbox", schema="platform")
        }
    except Exception as exc:  # pragma: no cover - env-specific
        pytest.skip(f"platform.event_outbox not inspectable: {exc}")
    missing = {"claim_token", "lease_expires_at", "terminal_reason"} - columns
    if missing:
        pytest.skip(
            "platform.event_outbox is missing E3 columns "
            f"{sorted(missing)} — run `alembic upgrade head` on the test DB"
        )


@pytest.fixture()
def committed_session_factory(engine) -> Iterator[sessionmaker]:
    """Real sessions with real commits (needed for lock/claim tests)."""
    yield sessionmaker(bind=engine)


@pytest.fixture()
def outbox_events(
    committed_session_factory: sessionmaker,
) -> Generator[list[uuid.UUID], None, None]:
    """Insert a committed batch of PENDING events; delete them afterwards."""
    marker = f"e3-canary-{uuid.uuid4().hex[:12]}"
    event_ids: list[uuid.UUID] = []
    with committed_session_factory() as db:
        for i in range(4):
            event = EventOutbox(
                event_name="e3.canary.event",
                aggregate_type="Canary",
                aggregate_id=f"{marker}-{i}",
                payload={"marker": marker},
                headers={"organization_id": str(uuid.uuid4())},
                producer_module="TEST",
                correlation_id=marker,
                idempotency_key=f"{marker}-{i}",
                status=EventStatus.PENDING,
                retry_count=0,
            )
            db.add(event)
            db.flush()
            event_ids.append(event.event_id)
        db.commit()

    yield event_ids

    with committed_session_factory() as db:
        db.execute(delete(EventOutbox).where(EventOutbox.correlation_id == marker))
        db.commit()


def _claim(
    db: Session,
    worker_id: str,
    batch_size: int = 1000,
    lease_seconds: int = 300,
) -> tuple[uuid.UUID, list[EventOutbox]]:
    return OutboxPublisher.claim_pending_events(
        db,
        worker_id=worker_id,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
    )


def test_two_workers_cannot_claim_the_same_events(
    committed_session_factory: sessionmaker,
    outbox_events: list[uuid.UUID],
) -> None:
    """CANARY: SKIP LOCKED makes concurrent claims disjoint.

    Worker A claims while its claim transaction is still open; worker B
    claiming concurrently must skip A's locked rows entirely instead of
    blocking or double-claiming.
    """
    ours = set(outbox_events)
    with committed_session_factory() as session_a:
        _, claimed_a = _claim(session_a, "worker-a")
        got_a = {e.event_id for e in claimed_a} & ours
        assert got_a == ours  # A claimed the whole batch

        # Concurrent claimant while A's transaction holds the row locks.
        with committed_session_factory() as session_b:
            _, claimed_b = _claim(session_b, "worker-b")
            got_b = {e.event_id for e in claimed_b} & ours
            assert got_b == set(), "second worker must skip locked claims"
            session_b.rollback()

        session_a.commit()

    # After A's claim committed, the unexpired lease still excludes B.
    with committed_session_factory() as session_b:
        _, claimed_b = _claim(session_b, "worker-b")
        got_b = {e.event_id for e in claimed_b} & ours
        session_b.rollback()
        assert got_b == set(), "unexpired lease must not be reclaimable"


def test_lease_expiry_allows_reclaim_and_redelivery(
    committed_session_factory: sessionmaker,
    outbox_events: list[uuid.UUID],
) -> None:
    """CANARY: worker death after claim is repairable via lease expiry."""
    with committed_session_factory() as db:
        token_a, claimed = _claim(db, "worker-a", lease_seconds=300)
        assert {e.event_id for e in claimed} >= set(outbox_events)
        db.commit()
        # Worker A dies here: no delivery, no settlement.

    # Simulate lease expiry (rather than sleeping out the lease).
    with committed_session_factory() as db:
        for event_id in outbox_events:
            event = db.get(EventOutbox, event_id)
            assert event is not None
            event.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    with committed_session_factory() as db:
        token_b, reclaimed = _claim(db, "worker-b")
        got = {e.event_id for e in reclaimed} & set(outbox_events)
        assert got == set(outbox_events), "expired leases must be reclaimable"
        assert token_b != token_a
        db.commit()

        # Worker B (the current claimant) settles successfully.
        OutboxPublisher.mark_published(db, outbox_events[0], claim_token=token_b)
        db.commit()

    with committed_session_factory() as db:
        event = db.get(EventOutbox, outbox_events[0])
        assert event is not None
        assert event.status == EventStatus.PUBLISHED


def test_stale_claimant_cannot_settle(
    committed_session_factory: sessionmaker,
    outbox_events: list[uuid.UUID],
) -> None:
    """CANARY: after reclaim, the previous claimant's token must not settle."""
    with committed_session_factory() as db:
        token_a, _ = _claim(db, "worker-a", lease_seconds=300)
        db.commit()

    # Lease expires; worker B reclaims.
    with committed_session_factory() as db:
        for event_id in outbox_events:
            event = db.get(EventOutbox, event_id)
            assert event is not None
            event.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    with committed_session_factory() as db:
        token_b, _ = _claim(db, "worker-b")
        db.commit()

    # Worker A wakes up and tries to settle with its stale token.
    with committed_session_factory() as db:
        with pytest.raises(StaleClaimError):
            OutboxPublisher.mark_published(db, outbox_events[0], claim_token=token_a)
        db.rollback()

    with committed_session_factory() as db:
        event = db.get(EventOutbox, outbox_events[0])
        assert event is not None
        assert event.status == EventStatus.PENDING, "stale settle must not stick"
        assert event.claim_token == token_b

        # And the retry path is equally token-gated.
        with pytest.raises(StaleClaimError):
            OutboxPublisher.handle_retry(
                db, outbox_events[0], claim_token=token_a, error_message="stale"
            )
        db.rollback()


def test_unsupported_event_dead_letters_never_publishes(
    committed_session_factory: sessionmaker,
    outbox_events: list[uuid.UUID],
) -> None:
    """CANARY: an unknown event settles DEAD/unsupported — never PUBLISHED."""
    with committed_session_factory() as db:
        token, claimed = _claim(db, "worker-a")
        assert claimed
        db.commit()

        OutboxPublisher.mark_unsupported(db, outbox_events[0], claim_token=token)
        db.commit()

    with committed_session_factory() as db:
        event = db.get(EventOutbox, outbox_events[0])
        assert event is not None
        assert event.status == EventStatus.DEAD
        assert event.terminal_reason == TerminalReason.UNSUPPORTED_EVENT
        assert event.published_at is None


def test_update_balance_reapplication_double_counts(
    db: Session,
    org_id: uuid.UUID,
    fiscal_period,
    gl_account,
) -> None:
    """EVIDENCE: ``update_balance_for_posting`` is NOT per-line idempotent.

    Re-applying the same line doubles the period movement. This is why the
    relay must never let a partially-applied event settle or retry without
    a full rollback: whole-event atomicity (one event per transaction) is
    the idempotency mechanism.
    """
    from app.services.finance.gl.account_balance import AccountBalanceService

    kwargs = dict(
        organization_id=org_id,
        account_id=gl_account.account_id,
        fiscal_period_id=fiscal_period.fiscal_period_id,
        debit_amount=Decimal("100"),
        credit_amount=Decimal("0"),
        currency_code="USD",
    )
    first = AccountBalanceService.update_balance_for_posting(db, **kwargs)
    assert first.period_debit == Decimal("100")

    second = AccountBalanceService.update_balance_for_posting(db, **kwargs)
    assert second.period_debit == Decimal("200"), (
        "re-application double-counts — retry without rollback is unsafe"
    )


def test_failed_line_rollback_leaves_no_partial_balance(
    db: Session,
    org_id: uuid.UUID,
    fiscal_period,
    gl_account,
) -> None:
    """CANARY: one failed line prevents whole-event success.

    The rewritten ``handle_ledger_posting_completed`` re-raises per-line
    failures (unit-covered in tests/tasks/test_outbox_relay.py); the relay
    then rolls back the whole delivery transaction. This proves the DB half:
    the rollback removes every balance row the event had already applied,
    so an unrecorded partial application cannot survive to be re-applied
    (which would double-count, per the test above).
    """
    from app.models.finance.gl.account_balance import AccountBalance
    from app.services.finance.gl.account_balance import AccountBalanceService

    # Line 1 applies inside the delivery transaction...
    AccountBalanceService.update_balance_for_posting(
        db,
        organization_id=org_id,
        account_id=gl_account.account_id,
        fiscal_period_id=fiscal_period.fiscal_period_id,
        debit_amount=Decimal("100"),
        credit_amount=Decimal("0"),
        currency_code="USD",
    )
    assert (
        db.scalars(
            select(AccountBalance).where(AccountBalance.organization_id == org_id)
        ).all()
        != []
    )

    # ...then line 2 fails, the handler raises, and the relay rolls back.
    db.rollback()

    remaining = db.scalars(
        select(AccountBalance).where(AccountBalance.organization_id == org_id)
    ).all()
    assert remaining == [], "no partial balance application may survive"
