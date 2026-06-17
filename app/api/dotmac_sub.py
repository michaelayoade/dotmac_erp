"""
dotmac_sub Integration API Routes.

Inbound webhook receiver for the dotmac_sub subscriber-management system. The
endpoint is unauthenticated and instead verifies an HMAC-SHA256 signature
(mirrors the CRM webhook handler in app/api/crm.py).
"""

from __future__ import annotations

import hashlib
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


def verify_dotmac_sub_signature(payload: bytes, signature: str) -> bool:
    """Verify an inbound webhook HMAC-SHA256 signature."""
    if not settings.dotmac_sub_webhook_secret:
        logger.error("dotmac_sub webhook secret not configured - verification failed")
        return False
    # Tolerate a "sha256=" prefix some senders include.
    sig = signature.split("=", 1)[1] if "=" in signature else signature
    expected = hmac.new(
        settings.dotmac_sub_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


@webhook_router.post("/webhook", response_model=WebhookResponse)
async def dotmac_sub_webhook(
    request: Request,
    x_dotmacsub_signature: str | None = Header(None, alias="X-DotmacSub-Signature"),
    db: Session = Depends(get_db),
) -> WebhookResponse:
    """Handle a dotmac_sub webhook event (HMAC-verified, no auth dependency)."""
    if not settings.dotmac_sub_webhook_secret:
        raise HTTPException(
            status_code=503,
            detail="dotmac_sub webhook authentication is not configured",
        )

    raw_body = await request.body()
    if not x_dotmacsub_signature:
        logger.warning("dotmac_sub webhook received without signature")
        raise HTTPException(status_code=400, detail="Missing signature")
    if not verify_dotmac_sub_signature(raw_body, x_dotmacsub_signature):
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

    if not settings.default_organization_id:
        logger.error("No default organization configured for dotmac_sub webhooks")
        return WebhookResponse(
            status="error", message="No default organization configured"
        )

    organization_id = UUID(settings.default_organization_id)
    prime_tenant_context(db, organization_id)

    from app.services.dotmac_sub.webhook_dispatch import dispatch_webhook

    logger.info("Processing dotmac_sub webhook: %s", event_type)
    try:
        result = dispatch_webhook(db, organization_id, event_type, payload)
    except Exception as e:  # noqa: BLE001 — never 500 a webhook sender into retries storms
        logger.exception("dotmac_sub webhook processing failed")
        return WebhookResponse(status="error", message=str(e))

    return WebhookResponse(status=result.get("status", "ok"), message=str(result))
