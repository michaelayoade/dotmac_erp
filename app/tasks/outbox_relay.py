"""
Outbox Relay — Celery task that polls the event outbox and dispatches events.

The transactional outbox pattern writes events atomically with business data,
then this relay task asynchronously reads and dispatches them to handlers.

Handler registry maps event_name patterns to handler functions.
Each handler receives (db, event) and should not commit — the relay
commits after marking the event as PUBLISHED.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from celery import shared_task

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.finance.platform.outbox_publisher import OutboxPublisher

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

EventHandler = Any  # Callable[[Session, EventOutbox], None]

_HANDLERS: dict[str, EventHandler] = {}


def register_handler(event_name: str, handler: EventHandler) -> None:
    """Register a handler for an event name."""
    _HANDLERS[event_name] = handler
    logger.debug("Registered handler for %s: %s", event_name, handler.__name__)


def _get_handler(event_name: str) -> EventHandler | None:
    """Get the handler for an event name (exact match)."""
    return _HANDLERS.get(event_name)


# ---------------------------------------------------------------------------
# Built-in handlers
# ---------------------------------------------------------------------------


def handle_ledger_posting_completed(db: Session, event: Any) -> None:
    """
    Handle ledger.posting.completed events.

    Reads posted ledger lines for the batch and incrementally updates
    account_balance rows. This provides near-real-time balance updates
    (the daily rebuild_account_balances task serves as a safety net).
    """
    from sqlalchemy import select

    from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
    from app.services.finance.gl.account_balance import AccountBalanceService

    payload = event.payload
    batch_id = payload.get("batch_id")
    org_id = UUID(payload["organization_id"])

    if not batch_id:
        logger.warning(
            "ledger.posting.completed event missing batch_id: %s", event.event_id
        )
        return

    # Get all posted ledger lines for this batch
    lines = list(
        db.scalars(
            select(PostedLedgerLine).where(
                PostedLedgerLine.posting_batch_id == UUID(batch_id)
            )
        ).all()
    )

    if not lines:
        logger.info("No ledger lines for batch %s (may already be processed)", batch_id)
        return

    updated = 0
    for line in lines:
        try:
            AccountBalanceService.update_balance_for_posting(
                db,
                organization_id=org_id,
                account_id=line.account_id,
                fiscal_period_id=line.fiscal_period_id,
                debit_amount=line.debit_amount or Decimal("0"),
                credit_amount=line.credit_amount or Decimal("0"),
                business_unit_id=line.business_unit_id,
                cost_center_id=line.cost_center_id,
                project_id=line.project_id,
                segment_id=line.segment_id,
            )
            updated += 1
        except Exception:
            logger.exception(
                "Failed to update balance for line %s in batch %s",
                line.ledger_line_id,
                batch_id,
            )

    logger.info(
        "Updated %d account balances for batch %s (%d lines)",
        updated,
        batch_id,
        len(lines),
    )


# Register built-in handlers
register_handler("ledger.posting.completed", handle_ledger_posting_completed)


# ---------------------------------------------------------------------------
# Relay task
# ---------------------------------------------------------------------------


@shared_task
def relay_outbox_events(
    batch_size: int = 100,
    max_retry_count: int = 5,
) -> dict[str, Any]:
    """
    Poll the event outbox and dispatch events to registered handlers.

    For each pending event:
    1. Look up a handler by event_name
    2. Call the handler (which should NOT commit)
    3. Mark as PUBLISHED on success, or handle_retry on failure
    4. Skip events with no registered handler (mark as PUBLISHED with note)

    Args:
        batch_size: Max events to process per run.
        max_retry_count: Max retries before marking dead.

    Returns:
        Dict with processing counts.
    """
    logger.info("Relay: polling outbox (batch=%d)", batch_size)

    published = 0
    skipped = 0
    failed = 0
    errors: list[str] = []

    with SessionLocal() as db:
        events = OutboxPublisher.get_pending_events(
            db, batch_size=batch_size, max_retry_count=max_retry_count
        )

        if not events:
            logger.debug("Relay: no pending events")
            return {"published": 0, "skipped": 0, "failed": 0, "errors": []}

        logger.info("Relay: processing %d events", len(events))

        for event in events:
            handler = _get_handler(event.event_name)

            if handler is None:
                # No handler registered — mark as published (no-op event)
                logger.debug(
                    "No handler for event %s (%s) — marking published",
                    event.event_id,
                    event.event_name,
                )
                OutboxPublisher.mark_published(db, event.event_id)
                skipped += 1
                continue

            try:
                handler(db, event)
                OutboxPublisher.mark_published(db, event.event_id)
                published += 1
            except Exception as e:
                logger.exception(
                    "Handler failed for event %s (%s): %s",
                    event.event_id,
                    event.event_name,
                    e,
                )
                db.rollback()
                OutboxPublisher.handle_retry(db, event.event_id, str(e))
                failed += 1
                errors.append(f"{event.event_id}: {e}")

    logger.info(
        "Relay complete: %d published, %d skipped, %d failed",
        published,
        skipped,
        failed,
    )
    return {
        "published": published,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }


@shared_task
def cleanup_published_outbox_events(
    retention_days: int = 30,
    batch_size: int = 5000,
) -> dict[str, Any]:
    """
    Delete old PUBLISHED events to keep the outbox table lean.

    Args:
        retention_days: Keep published events for this many days.
        batch_size: Max events to delete per run.

    Returns:
        Dict with deletion count.
    """
    logger.info(
        "Cleaning up published outbox events older than %d days", retention_days
    )

    deleted = 0

    with SessionLocal() as db:
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import delete

        from app.models.finance.platform.event_outbox import EventOutbox, EventStatus

        cutoff = datetime.now(UTC) - timedelta(days=retention_days)

        result = db.execute(
            delete(EventOutbox).where(
                EventOutbox.status == EventStatus.PUBLISHED,
                EventOutbox.published_at < cutoff,
            )
        )
        deleted = result.rowcount
        db.commit()

    logger.info("Deleted %d old published outbox events", deleted)
    return {"deleted": deleted}
