"""
dotmac_sub Integration API Routes.

Inbound webhook receiver for the dotmac_sub subscriber-management system. The
endpoint is unauthenticated and instead verifies an HMAC-SHA256 signature
(mirrors the CRM webhook handler in app/api/crm.py).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
import hmac
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.db.session_context import prime_tenant_context

logger = logging.getLogger(__name__)

webhook_router = APIRouter(prefix="/dotmac-sub", tags=["dotmac-sub-webhooks"])


def get_db():  # type: ignore[no-untyped-def]
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class WebhookResponse(BaseModel):
    status: str
    message: str | None = None


def verify_dotmac_sub_signature(
    payload: bytes, signature: str, secret: str | None = None
) -> bool:
    """Verify an inbound webhook HMAC-SHA256 signature.

    ``secret`` defaults to the env-configured signing secret; per-org secrets
    (IntegrationConfig.api_secret) pass theirs explicitly.
    """
    key = secret if secret is not None else settings.dotmac_sub_webhook_secret
    if not key:
        logger.error("dotmac_sub webhook secret not configured - verification failed")
        return False
    # Tolerate a "sha256=" prefix some senders include.
    sig = signature.split("=", 1)[1] if "=" in signature else signature
    expected = hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def _resolve_webhook_org(db: Session, raw_body: bytes, signature: str):
    """Authenticate the webhook AND resolve its organization (audit S4b).

    Order: the env secret authenticates the env-configured default org
    (single-org behaviour, unchanged); otherwise each active per-org
    ``IntegrationConfig(DOTMAC_SUB)`` row's ``api_secret`` is tried — a match
    both authenticates the delivery and identifies the org, so onboarding a
    second org is a config row, not a code change. Returns the organization id
    or ``None`` when nothing verifies.
    """
    if settings.dotmac_sub_webhook_secret and settings.default_organization_id:
        if verify_dotmac_sub_signature(raw_body, signature):
            return UUID(settings.default_organization_id)

    from sqlalchemy import select

    from app.models.sync import IntegrationType
    from app.models.sync.integration_config import IntegrationConfig
    from app.services.integration_config import decrypt_credential

    rows = (
        db.execute(
            select(IntegrationConfig).where(
                IntegrationConfig.integration_type == IntegrationType.DOTMAC_SUB,
                IntegrationConfig.is_active == True,  # noqa: E712
            )
        )
        .scalars()
        .all()
    )
    for cfg in rows:
        org_secret = decrypt_credential(cfg.api_secret, db)
        if org_secret and verify_dotmac_sub_signature(
            raw_body, signature, secret=org_secret
        ):
            return cfg.organization_id
    return None


@webhook_router.post("/webhook", response_model=WebhookResponse)
async def dotmac_sub_webhook(
    request: Request,
    x_webhook_signature_256: str | None = Header(None, alias="X-Webhook-Signature-256"),
    x_dotmacsub_signature: str | None = Header(None, alias="X-DotmacSub-Signature"),
    x_webhook_delivery_id: str | None = Header(None, alias="X-Webhook-Delivery-Id"),
    db: Session = Depends(get_db),
) -> WebhookResponse:
    """Handle a dotmac_sub webhook event (HMAC-verified, no auth dependency)."""
    if not settings.dotmac_sub_webhook_secret:
        raise HTTPException(
            status_code=503,
            detail="dotmac_sub webhook authentication is not configured",
        )

    raw_body = await request.body()
    # dotmac_sub sends X-Webhook-Signature-256 ("sha256=<hex>"); keep the older
    # X-DotmacSub-Signature alias as a fallback for any legacy sender.
    signature = x_webhook_signature_256 or x_dotmacsub_signature
    if not signature:
        logger.warning("dotmac_sub webhook received without signature")
        raise HTTPException(status_code=400, detail="Missing signature")
    # Authenticate AND resolve the org in one step (audit S4b): the env secret
    # maps to the default org; per-org IntegrationConfig secrets map to theirs.
    organization_id = _resolve_webhook_org(db, raw_body, signature)
    if organization_id is None:
        logger.warning("dotmac_sub webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = (
        payload.get("event_type") or payload.get("event") or payload.get("type")
    )
    if not event_type:
        raise HTTPException(status_code=400, detail="Missing event_type")

    prime_tenant_context(db, organization_id)

    # Dedupe on dotmac_sub's delivery id: the sender retries the SAME delivery
    # id with backoff, and we ACK before processing below, so a replayed
    # delivery must not enqueue duplicate work. (Correctness is additionally
    # guarded by the idempotent handler + DB uniques; this avoids re-work.)
    if x_webhook_delivery_id:
        if not _record_webhook_delivery(db, organization_id, x_webhook_delivery_id):
            logger.info(
                "dotmac_sub webhook duplicate delivery %s ignored",
                x_webhook_delivery_id,
            )
            return WebhookResponse(status="ok", message="duplicate delivery")

    # ACK fast and process asynchronously. The dispatch reads back into
    # dotmac_sub, whose rate limiter throttles synchronous bursts (observed
    # ~94% 503s under load); the Celery task paces those reads (rate_limit)
    # and retries locally with backoff instead of bouncing the delivery.
    from app.tasks.dotmac_sub import process_dotmac_sub_webhook

    logger.info("Accepted dotmac_sub webhook: %s", event_type)
    try:
        process_dotmac_sub_webhook.delay(str(organization_id), str(event_type), payload)
    except Exception as e:  # noqa: BLE001
        # Queue unavailable: surface 503 so the sender's bounded retry redelivers.
        logger.exception("dotmac_sub webhook enqueue failed")
        raise HTTPException(
            status_code=503, detail=f"Webhook enqueue failed: {e}"
        ) from e

    return WebhookResponse(status="accepted", message="queued for processing")


def _record_webhook_delivery(
    db: Session, organization_id: UUID, delivery_id: str
) -> bool:
    """Record a webhook delivery id; False when it was already recorded.

    Uses the platform idempotency table's (organization, endpoint, key) unique
    constraint as the arbiter so concurrent replays cannot both win.
    """
    from hashlib import sha256

    from sqlalchemy.exc import IntegrityError

    from app.models.finance.platform.idempotency_record import IdempotencyRecord

    record = IdempotencyRecord(
        organization_id=organization_id,
        endpoint="dotmac_sub_webhook",
        idempotency_key=delivery_id[:200],
        request_hash=sha256(delivery_id.encode()).hexdigest(),
        response_status=202,
        response_body=None,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True
