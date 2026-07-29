"""
dotmac_sub webhook dispatch.

Maps inbound ``WebhookEventType`` events to a targeted upsert of the single
referenced entity, reusing the same sync mixins as the polling tasks so both
paths produce identical results.

Event catalogue (dotmac_sub ``WebhookEventType``) handled here:
- ``subscriber.created`` / ``subscriber.updated`` / ``subscriber.*``
- ``invoice.created`` / ``invoice.sent`` / ``invoice.paid`` / ``invoice.overdue``
- ``payment.received`` / ``payment.refunded``

Other events are acknowledged but ignored.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.dotmac_sub import SYSTEM_USER_ID, DotmacSubSyncService

logger = logging.getLogger(__name__)


def _entity_id(envelope: dict[str, Any], domain: str) -> str | None:
    """Pull the referenced upstream entity id out of a dotmac_sub webhook body.

    dotmac_sub sends ``{event_id, event_type, occurred_at, payload, context}``:
    the entity itself is in ``payload`` and its ids are echoed in ``context``.
    We look, per domain, in ``context.<domain>_id`` and ``payload.<domain>_id``
    first (e.g. ``context.invoice_id``, ``payload.payment_id``), then the
    payload's own ``id`` — falling back to the legacy flat/``data`` shape so any
    older sender still resolves.
    """
    context = envelope.get("context")
    context = context if isinstance(context, dict) else {}
    inner = envelope.get("payload")
    inner = inner if isinstance(inner, dict) else {}

    for val in (
        context.get(f"{domain}_id"),
        inner.get(f"{domain}_id"),
        inner.get("id"),
        inner.get("entity_id"),
    ):
        if val:
            return str(val)

    # Legacy / other-sender fallback: flat body or a nested "data" object.
    raw = envelope.get("data")
    data = raw if isinstance(raw, dict) else envelope
    for key in ("id", "entity_id", "object_id"):
        val = data.get(key) or envelope.get(key)
        if val:
            return str(val)
    return None


def dispatch_webhook(
    db: Session,
    organization_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Process one dotmac_sub webhook event. Returns a result summary.

    The caller owns the transaction (commit/rollback) and has already primed
    tenant context. Account resolution mirrors the Celery tasks.
    """
    from app.tasks.dotmac_sub import (
        _resolve_ar_control_account,
        _resolve_default_revenue_account,
    )

    domain = (event_type or "").split(".", 1)[0].lower()
    if domain not in {"subscriber", "invoice", "payment"}:
        return {"status": "ignored", "reason": f"unhandled event {event_type}"}

    entity_id = _entity_id(payload, domain)
    if not entity_id:
        return {"status": "ignored", "reason": "no entity id in payload"}

    ar_control = _resolve_ar_control_account(db, organization_id)
    revenue = _resolve_default_revenue_account(db, organization_id)
    # Only invoice/credit-note syncs need a revenue account (it codes the GL
    # lines). Subscriber upserts and payment receipts never touch revenue, so a
    # missing revenue account must not block them. AR control is required to
    # construct the sync service for any entity.
    if not ar_control or (domain == "invoice" and not revenue):
        logger.error(
            "dotmac_sub webhook: required GL accounts unresolved for org %s (event %s)",
            organization_id,
            event_type,
        )
        return {"status": "error", "reason": "GL accounts unresolved"}

    service = DotmacSubSyncService(
        db=db,
        organization_id=organization_id,
        ar_control_account_id=ar_control,
        default_revenue_account_id=revenue,
    )
    try:
        if domain == "subscriber":
            result = service.sync_subscriber_by_id(entity_id, SYSTEM_USER_ID)
        elif domain == "invoice":
            result = service.sync_invoice_by_id(entity_id, SYSTEM_USER_ID)
        else:  # payment
            result = service.sync_payment_by_id(entity_id, SYSTEM_USER_ID)
    finally:
        service.close()

    return {
        "status": "ok" if not result.errors else "partial",
        "event_type": event_type,
        "entity_id": entity_id,
        "created": result.created,
        "updated": result.updated,
        "skipped": result.skipped,
        "errors": result.errors[:10],
    }
