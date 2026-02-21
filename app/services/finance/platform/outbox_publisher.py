"""
OutboxPublisher - Transactional outbox pattern for reliable event delivery.

Events are written to the database atomically with business data,
then published asynchronously by a background processor.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.finance.platform.event_outbox import EventOutbox, EventStatus
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


class OutboxPublisher(ListResponseMixin):
    """
    Service for publishing events via transactional outbox pattern.

    Events are written to the database atomically with business data,
    then published asynchronously by a background processor.
    """

    MAX_RETRY_COUNT: int = 5
    RETRY_DELAYS: list[int] = [60, 300, 900, 3600, 86400]  # seconds

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
    def get_pending_events(
        db: Session,
        batch_size: int = 100,
        max_retry_count: int | None = None,
    ) -> list[EventOutbox]:
        """
        Get pending events ready for publishing.

        Returns events with PENDING or FAILED status and next_retry_at <= now.

        Args:
            db: Database session
            batch_size: Maximum events to return
            max_retry_count: Filter by max retries (default: MAX_RETRY_COUNT)

        Returns:
            List of EventOutbox records
        """
        now = datetime.now(UTC)
        max_retries = max_retry_count or OutboxPublisher.MAX_RETRY_COUNT

        stmt = db.query(EventOutbox).filter(
            and_(
                EventOutbox.status.in_([EventStatus.PENDING, EventStatus.FAILED]),
                EventOutbox.retry_count < max_retries,
            )
        )

        # Filter by next_retry_at (NULL or <= now)
        stmt = stmt.filter(
            (EventOutbox.next_retry_at.is_(None)) | (EventOutbox.next_retry_at <= now)
        )

        return stmt.order_by(EventOutbox.occurred_at.asc()).limit(batch_size).all()

    @staticmethod
    def mark_published(
        db: Session,
        event_id: UUID,
    ) -> EventOutbox:
        """
        Mark an event as successfully published.

        Args:
            db: Database session
            event_id: Event ID

        Returns:
            Updated EventOutbox record
        """
        event = db.get(EventOutbox, coerce_uuid(event_id))
        if not event:
            raise ValueError(f"Event not found: {event_id}")

        event.status = EventStatus.PUBLISHED
        event.published_at = datetime.now(UTC)
        event.last_error = None

        db.commit()
        db.refresh(event)
        return event

    @staticmethod
    def handle_retry(
        db: Session,
        event_id: UUID,
        error_message: str,
    ) -> EventOutbox:
        """
        Handle a failed publish attempt.

        Increments retry count and schedules next retry.
        Marks as DEAD if max retries exceeded.

        Args:
            db: Database session
            event_id: Event ID
            error_message: Error description

        Returns:
            Updated EventOutbox record
        """
        event = db.get(EventOutbox, coerce_uuid(event_id))
        if not event:
            raise ValueError(f"Event not found: {event_id}")

        event.retry_count += 1
        event.last_error = error_message

        if event.retry_count >= OutboxPublisher.MAX_RETRY_COUNT:
            # Max retries exceeded - mark as dead
            event.status = EventStatus.DEAD
        else:
            # Schedule next retry
            delay_seconds = OutboxPublisher.RETRY_DELAYS[
                min(event.retry_count - 1, len(OutboxPublisher.RETRY_DELAYS) - 1)
            ]
            event.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            event.status = EventStatus.FAILED

        db.commit()
        db.refresh(event)
        return event

    @staticmethod
    def mark_dead(
        db: Session,
        event_id: UUID,
        error_message: str,
    ) -> EventOutbox:
        """
        Mark an event as dead (failed permanently).

        Args:
            db: Database session
            event_id: Event ID
            error_message: Final error description

        Returns:
            Updated EventOutbox record
        """
        event = db.get(EventOutbox, coerce_uuid(event_id))
        if not event:
            raise ValueError(f"Event not found: {event_id}")

        event.status = EventStatus.DEAD
        event.last_error = error_message

        db.commit()
        db.refresh(event)
        return event

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
        return (
            db.query(EventOutbox)
            .filter(EventOutbox.status == status)
            .order_by(EventOutbox.occurred_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def retry_dead_event(
        db: Session,
        event_id: UUID,
    ) -> EventOutbox:
        """
        Reset a dead event for retry.

        Args:
            db: Database session
            event_id: Event ID

        Returns:
            Updated EventOutbox record with PENDING status
        """
        event = db.get(EventOutbox, coerce_uuid(event_id))
        if not event:
            raise ValueError(f"Event not found: {event_id}")

        event.status = EventStatus.PENDING
        event.retry_count = 0
        event.next_retry_at = None
        event.last_error = None

        db.commit()
        db.refresh(event)
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
        return (
            db.query(EventOutbox)
            .filter(
                and_(
                    EventOutbox.aggregate_type == aggregate_type,
                    EventOutbox.aggregate_id == aggregate_id,
                )
            )
            .order_by(EventOutbox.occurred_at.desc())
            .limit(limit)
            .all()
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
        return (
            db.query(EventOutbox)
            .filter(EventOutbox.correlation_id == correlation_id)
            .order_by(EventOutbox.occurred_at.asc())
            .limit(limit)
            .all()
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
        stmt = db.query(EventOutbox)

        if status:
            stmt = stmt.filter(EventOutbox.status == status)

        if producer_module:
            stmt = stmt.filter(EventOutbox.producer_module == producer_module)

        return (
            stmt.order_by(EventOutbox.occurred_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )


# Module-level singleton instance
outbox_publisher = OutboxPublisher()
