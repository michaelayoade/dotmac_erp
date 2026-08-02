"""
OutboxPublisher - Transactional outbox pattern for reliable event delivery.

Events are written to the database atomically with business data, then
delivered asynchronously by the relay task using a claim/deliver/settle
protocol with applied-result semantics:

- **Claim**: a bounded batch is claimed atomically with
  ``SELECT ... FOR UPDATE SKIP LOCKED`` in a short transaction that stamps
  a claim token and lease; expired leases are reclaimable.
- **Deliver**: handler execution happens outside the claim transaction,
  one event per transaction, with settlement staged in the same
  transaction so a commit failure can never record success.
- **Settle**: settlement requires the matching claim token; a stale
  claimant (token mismatch after reclaim) cannot settle.

Transaction boundary: the relay task (worker/session adapter) owns
commit/rollback. Every method here mutates and ``flush()``\\ es only — it
never commits or rolls back.
"""

import logging
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.audit import AuditActorType, AuditEvent
from app.models.finance.platform.event_outbox import (
    EventOutbox,
    EventStatus,
    TerminalReason,
)
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


class StaleClaimError(RuntimeError):
    """Raised when settlement is attempted with a token that no longer
    matches the event's current claim (lease expired and reclaimed)."""


class OutboxPublisher(ListResponseMixin):
    """
    Service for publishing events via transactional outbox pattern.

    Events are written to the database atomically with business data,
    then published asynchronously by a background processor.

    All methods flush only; the caller owns commit/rollback.
    """

    MAX_RETRY_COUNT: int = 5
    RETRY_DELAYS: list[int] = [60, 300, 900, 3600, 86400]  # seconds
    DEFAULT_LEASE_SECONDS: int = 300
    MAX_ERROR_LENGTH: int = 2000

    @staticmethod
    def publish_event(
        db: Session,
        event_name: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        headers: dict[str, Any],
        producer_module: str,
        correlation_id: str,
        idempotency_key: str,
        causation_id: UUID | None = None,
        event_version: int = 1,
    ) -> EventOutbox:
        """
        Publish an event to the outbox.

        Call this within the same transaction as your business logic.
        This method does not commit.

        Args:
            db: Database session
            event_name: Event name (e.g., "ledger.posting.completed")
            aggregate_type: Type of aggregate (e.g., "JournalEntry")
            aggregate_id: ID of the aggregate
            payload: Event payload data
            headers: Required headers (organization_id, user_id, etc.)
            producer_module: Module producing the event (e.g., "GL")
            correlation_id: Correlation ID for tracing
            idempotency_key: Unique key for deduplication
            causation_id: Optional ID of causing event
            event_version: Schema version (default: 1)

        Returns:
            Created EventOutbox record
        """
        event = EventOutbox(
            event_name=event_name,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            headers=headers,
            producer_module=producer_module,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            causation_id=coerce_uuid(causation_id) if causation_id else None,
            event_version=event_version,
            status=EventStatus.PENDING,
            retry_count=0,
        )

        db.add(event)
        db.flush()
        db.refresh(event)
        return event

    @staticmethod
    def claim_pending_events(
        db: Session,
        worker_id: str,
        batch_size: int = 100,
        max_retry_count: int | None = None,
        lease_seconds: int | None = None,
    ) -> tuple[UUID, list[EventOutbox]]:
        """
        Atomically claim a bounded batch of deliverable events.

        Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so two concurrent
        claimants can never claim the same rows, then stamps a fresh claim
        token and lease on each claimed row. The caller must commit this
        short claim transaction before delivering; expired leases are
        reclaimable by any worker.

        Args:
            db: Database session (short claim transaction; caller commits)
            worker_id: Claimant identity (e.g. ``host:pid``)
            batch_size: Maximum events to claim
            max_retry_count: Filter by max retries (default: MAX_RETRY_COUNT)
            lease_seconds: Lease duration (default: DEFAULT_LEASE_SECONDS)

        Returns:
            Tuple of (claim_token, claimed events)
        """
        now = datetime.now(UTC)
        max_retries = max_retry_count or OutboxPublisher.MAX_RETRY_COUNT
        lease = lease_seconds or OutboxPublisher.DEFAULT_LEASE_SECONDS
        claim_token = uuid_lib.uuid4()

        stmt = (
            select(EventOutbox)
            .where(
                and_(
                    EventOutbox.status.in_([EventStatus.PENDING, EventStatus.FAILED]),
                    EventOutbox.retry_count < max_retries,
                    or_(
                        EventOutbox.next_retry_at.is_(None),
                        EventOutbox.next_retry_at <= now,
                    ),
                    # An unexpired lease means another worker holds the claim.
                    or_(
                        EventOutbox.lease_expires_at.is_(None),
                        EventOutbox.lease_expires_at <= now,
                    ),
                )
            )
            .order_by(EventOutbox.occurred_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )

        events = list(db.scalars(stmt).all())
        for event in events:
            event.claimed_by = worker_id
            event.claim_token = claim_token
            event.claimed_at = now
            event.lease_expires_at = now + timedelta(seconds=lease)

        db.flush()
        return claim_token, events

    @staticmethod
    def _get_claimed_event(
        db: Session,
        event_id: UUID,
        claim_token: UUID,
    ) -> EventOutbox:
        """Load an event for settlement, verifying the claim token.

        Locks the row (``FOR UPDATE``) so settlement serializes against a
        concurrent reclaim, then requires the presented token to match the
        row's current claim. A stale claimant — one whose lease expired and
        whose claim was taken over — raises :class:`StaleClaimError` and
        must not settle.
        """
        event = db.get(
            EventOutbox, coerce_uuid(event_id), with_for_update={"nowait": False}
        )
        if not event:
            raise ValueError(f"Event not found: {event_id}")
        if event.claim_token != claim_token:
            raise StaleClaimError(
                f"Claim token mismatch for event {event_id}: "
                "lease was reclaimed by another worker"
            )
        return event

    @staticmethod
    def get_pending_events(
        db: Session,
        batch_size: int = 100,
        max_retry_count: int | None = None,
    ) -> list[EventOutbox]:
        """
        Read-only view of events ready for delivery (monitoring/inspection).

        Excludes events currently held under an unexpired claim lease.
        This is NOT a claim: use :meth:`claim_pending_events` in the relay.

        Args:
            db: Database session
            batch_size: Maximum events to return
            max_retry_count: Filter by max retries (default: MAX_RETRY_COUNT)

        Returns:
            List of EventOutbox records
        """
        now = datetime.now(UTC)
        max_retries = max_retry_count or OutboxPublisher.MAX_RETRY_COUNT

        stmt = select(EventOutbox).where(
            and_(
                EventOutbox.status.in_([EventStatus.PENDING, EventStatus.FAILED]),
                EventOutbox.retry_count < max_retries,
            )
        )

        # Filter by next_retry_at (NULL or <= now)
        stmt = stmt.where(
            (EventOutbox.next_retry_at.is_(None)) | (EventOutbox.next_retry_at <= now)
        )
        # Exclude actively-claimed events (unexpired lease)
        stmt = stmt.where(
            (EventOutbox.lease_expires_at.is_(None))
            | (EventOutbox.lease_expires_at <= now)
        )

        ordered = stmt.order_by(EventOutbox.occurred_at.asc()).limit(batch_size)

        # In some environments, SQLAlchemy can raise NotImplementedError while
        # materializing scalar ORM results (for example with certain driver /
        # metadata edge cases). Fall back to the classic Query API so we can still
        # continue processing outbox events.
        try:
            return list(db.scalars(ordered).all())
        except NotImplementedError:
            logger.warning(
                "Fallback to ORM query API for pending outbox events due scalar"
                " result materialization issue"
            )
            return list(
                db.query(EventOutbox)
                .filter(
                    EventOutbox.status.in_([EventStatus.PENDING, EventStatus.FAILED]),
                    EventOutbox.retry_count < max_retries,
                    or_(
                        EventOutbox.next_retry_at.is_(None),
                        EventOutbox.next_retry_at <= now,
                    ),
                    or_(
                        EventOutbox.lease_expires_at.is_(None),
                        EventOutbox.lease_expires_at <= now,
                    ),
                )
                .order_by(EventOutbox.occurred_at.asc())
                .limit(batch_size)
                .all()
            )

    @staticmethod
    def _release_lease(event: EventOutbox) -> None:
        """Clear the active lease (claimed_by/claimed_at kept as evidence)."""
        event.claim_token = None
        event.lease_expires_at = None

    @staticmethod
    def _trim_error(error_message: str) -> str:
        return str(error_message)[: OutboxPublisher.MAX_ERROR_LENGTH]

    @staticmethod
    def mark_published(
        db: Session,
        event_id: UUID,
        claim_token: UUID,
        terminal_reason: str | None = None,
    ) -> EventOutbox:
        """
        Mark a claimed event as successfully published (flush only).

        Must be staged in the SAME transaction as the handler's mutations
        so a commit failure can never record success. Requires the claim
        token; a stale claimant raises :class:`StaleClaimError`.

        Args:
            db: Database session
            event_id: Event ID
            claim_token: Token returned by :meth:`claim_pending_events`
            terminal_reason: Optional note (e.g.
                ``TerminalReason.DECLARED_NO_CONSEQUENCE`` for documented
                no-op events)

        Returns:
            Updated EventOutbox record
        """
        event = OutboxPublisher._get_claimed_event(db, event_id, claim_token)

        event.status = EventStatus.PUBLISHED
        event.published_at = datetime.now(UTC)
        event.last_error = None
        event.error_class = None
        event.terminal_reason = terminal_reason
        OutboxPublisher._release_lease(event)

        db.flush()
        return event

    @staticmethod
    def handle_retry(
        db: Session,
        event_id: UUID,
        claim_token: UUID,
        error_message: str,
        error_class: str | None = None,
    ) -> EventOutbox:
        """
        Settle a failed delivery attempt (flush only).

        Increments retry count and schedules the next retry on the
        deterministic RETRY_DELAYS ladder; dead-letters when max retries
        are exceeded. Requires the claim token.

        Args:
            db: Database session
            event_id: Event ID
            claim_token: Token returned by :meth:`claim_pending_events`
            error_message: Error description
            error_class: Exception class name of the failure

        Returns:
            Updated EventOutbox record
        """
        event = OutboxPublisher._get_claimed_event(db, event_id, claim_token)

        event.retry_count += 1
        event.last_error = OutboxPublisher._trim_error(error_message)
        event.error_class = error_class

        if event.retry_count >= OutboxPublisher.MAX_RETRY_COUNT:
            # Max retries exceeded - mark as dead
            event.status = EventStatus.DEAD
            event.terminal_reason = TerminalReason.MAX_RETRIES_EXCEEDED
        else:
            # Schedule next retry
            delay_seconds = OutboxPublisher.RETRY_DELAYS[
                min(event.retry_count - 1, len(OutboxPublisher.RETRY_DELAYS) - 1)
            ]
            event.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            event.status = EventStatus.FAILED

        OutboxPublisher._release_lease(event)
        db.flush()
        return event

    @staticmethod
    def mark_dead(
        db: Session,
        event_id: UUID,
        error_message: str,
        claim_token: UUID | None = None,
        error_class: str | None = None,
        terminal_reason: str = TerminalReason.MANUAL_DEAD_LETTER,
    ) -> EventOutbox:
        """
        Mark an event as dead (failed permanently). Flush only.

        Args:
            db: Database session
            event_id: Event ID
            error_message: Final error description
            claim_token: Required when settling a claimed delivery; ``None``
                only for administrative dead-lettering outside the relay
            error_class: Exception class name, if applicable
            terminal_reason: Why the event was dead-lettered

        Returns:
            Updated EventOutbox record
        """
        if claim_token is not None:
            event = OutboxPublisher._get_claimed_event(db, event_id, claim_token)
        else:
            found = db.get(EventOutbox, coerce_uuid(event_id))
            if not found:
                raise ValueError(f"Event not found: {event_id}")
            event = found

        event.status = EventStatus.DEAD
        event.last_error = OutboxPublisher._trim_error(error_message)
        event.error_class = error_class
        event.terminal_reason = terminal_reason
        OutboxPublisher._release_lease(event)

        db.flush()
        return event

    @staticmethod
    def mark_unsupported(
        db: Session,
        event_id: UUID,
        claim_token: UUID,
    ) -> EventOutbox:
        """
        Dead-letter a claimed event that has no registered handler and no
        declared no-consequence decision. Flush only; never PUBLISHED.
        """
        return OutboxPublisher.mark_dead(
            db,
            event_id,
            error_message=(
                "No registered handler and event is not declared no-consequence"
            ),
            claim_token=claim_token,
            error_class="UnsupportedEventError",
            terminal_reason=TerminalReason.UNSUPPORTED_EVENT,
        )

    @staticmethod
    def get_failed_events(
        db: Session,
        status: EventStatus = EventStatus.FAILED,
        limit: int = 100,
    ) -> list[EventOutbox]:
        """
        Get failed or dead events for manual review.

        Args:
            db: Database session
            status: Filter by status (FAILED or DEAD)
            limit: Maximum results

        Returns:
            List of EventOutbox records
        """
        return list(
            db.scalars(
                select(EventOutbox)
                .where(EventOutbox.status == status)
                .order_by(EventOutbox.occurred_at.desc())
                .limit(limit)
            ).all()
        )

    @staticmethod
    def retry_dead_event(
        db: Session,
        event_id: UUID,
        actor_id: str,
        reason: str,
    ) -> EventOutbox:
        """
        Authorized replay: reset a DEAD event for redelivery. Flush only.

        Replay is an explicit, audited operation — it records the actor and
        reason as an :class:`AuditEvent` staged in the SAME transaction as
        the state reset, so the evidence commits (or fails) atomically with
        the replay itself.

        Args:
            db: Database session (caller commits)
            event_id: Event ID
            actor_id: Identity of the person/system authorizing the replay
            reason: Why the event is being replayed

        Returns:
            Updated EventOutbox record with PENDING status
        """
        if not actor_id or not actor_id.strip():
            raise ValueError("Replay requires an actor_id")
        if not reason or not reason.strip():
            raise ValueError("Replay requires a reason")

        event = db.get(EventOutbox, coerce_uuid(event_id))
        if not event:
            raise ValueError(f"Event not found: {event_id}")
        if event.status != EventStatus.DEAD:
            raise ValueError(
                f"Only DEAD events can be replayed (event {event_id} is "
                f"{getattr(event.status, 'value', event.status)})"
            )

        org_header = (event.headers or {}).get("organization_id")

        audit = AuditEvent(
            actor_type=AuditActorType.user,
            actor_id=actor_id.strip(),
            organization_id=coerce_uuid(org_header) if org_header else None,
            action="outbox.replay_dead_event",
            entity_type="platform.event_outbox",
            entity_id=str(event.event_id),
            status_code=200,
            is_success=True,
            metadata_={
                "reason": reason.strip(),
                "event_name": event.event_name,
                "idempotency_key": event.idempotency_key,
                "previous_terminal_reason": event.terminal_reason,
                "previous_retry_count": event.retry_count,
            },
        )
        db.add(audit)

        event.status = EventStatus.PENDING
        event.retry_count = 0
        event.next_retry_at = None
        event.last_error = None
        event.error_class = None
        event.terminal_reason = None
        event.claimed_by = None
        event.claimed_at = None
        OutboxPublisher._release_lease(event)

        db.flush()

        from app.metrics import observe_outbox_replay

        observe_outbox_replay()
        return event

    @staticmethod
    def get_event(
        db: Session,
        event_id: str,
    ) -> EventOutbox:
        """
        Get an event by ID.

        Args:
            db: Database session
            event_id: Event ID

        Returns:
            EventOutbox record
        """
        event = db.get(EventOutbox, coerce_uuid(event_id))
        if not event:
            raise ValueError(f"Event not found: {event_id}")
        return event

    @staticmethod
    def get_events_by_aggregate(
        db: Session,
        aggregate_type: str,
        aggregate_id: str,
        limit: int = 50,
    ) -> list[EventOutbox]:
        """
        Get events for a specific aggregate.

        Args:
            db: Database session
            aggregate_type: Type of aggregate
            aggregate_id: Aggregate ID
            limit: Maximum results

        Returns:
            List of EventOutbox records
        """
        return list(
            db.scalars(
                select(EventOutbox)
                .where(
                    and_(
                        EventOutbox.aggregate_type == aggregate_type,
                        EventOutbox.aggregate_id == aggregate_id,
                    )
                )
                .order_by(EventOutbox.occurred_at.desc())
                .limit(limit)
            ).all()
        )

    @staticmethod
    def get_events_by_correlation(
        db: Session,
        correlation_id: str,
        limit: int = 50,
    ) -> list[EventOutbox]:
        """
        Get events by correlation ID.

        Args:
            db: Database session
            correlation_id: Correlation ID
            limit: Maximum results

        Returns:
            List of EventOutbox records
        """
        return list(
            db.scalars(
                select(EventOutbox)
                .where(EventOutbox.correlation_id == correlation_id)
                .order_by(EventOutbox.occurred_at.asc())
                .limit(limit)
            ).all()
        )

    @staticmethod
    def list(
        db: Session,
        status: EventStatus | None = None,
        producer_module: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EventOutbox]:
        """
        List events (for ListResponseMixin compatibility).

        Args:
            db: Database session
            status: Filter by status
            producer_module: Filter by producer module
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of EventOutbox objects
        """
        stmt = select(EventOutbox)

        if status:
            stmt = stmt.where(EventOutbox.status == status)

        if producer_module:
            stmt = stmt.where(EventOutbox.producer_module == producer_module)

        return list(
            db.scalars(
                stmt.order_by(EventOutbox.occurred_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        )


# Module-level singleton instance
outbox_publisher = OutboxPublisher()
