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


def _entity_id(payload: dict[str, Any]) -> str | None:
    """Pull the referenced entity id out of a webhook payload."""
    raw = payload.get("data")
    data = raw if isinstance(raw, dict) else payload
    for key in ("id", "entity_id", "object_id"):
        val = data.get(key) or payload.get(key)
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

    entity_id = _entity_id(payload)
    if not entity_id:
        return {"status": "ignored", "reason": "no entity id in payload"}

    domain = (event_type or "").split(".", 1)[0].lower()
    if domain not in {"subscriber", "invoice", "payment"}:
        return {"status": "ignored", "reason": f"unhandled event {event_type}"}

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
