"""
dotmac_academy Integration API Routes.

Inbound webhook receiver for the Fiber Academy LMS. Unauthenticated; verifies an
HMAC-SHA256 signature (mirrors app/api/dotmac_sub.py). Records training
completions against employees' HR records.
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

webhook_router = APIRouter(prefix="/dotmac-academy", tags=["dotmac-academy-webhooks"])


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


def verify_dotmac_academy_signature(payload: bytes, signature: str) -> bool:
    """Verify an inbound webhook HMAC-SHA256 signature."""
    if not settings.dotmac_academy_webhook_secret:
        logger.error(
            "dotmac_academy webhook secret not configured - verification failed"
        )
        return False
    sig = signature.split("=", 1)[1] if "=" in signature else signature
    expected = hmac.new(
        settings.dotmac_academy_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


@webhook_router.post("/webhook", response_model=WebhookResponse)
async def dotmac_academy_webhook(
    request: Request,
    x_webhook_signature_256: str | None = Header(None, alias="X-Webhook-Signature-256"),
    db: Session = Depends(get_db),
) -> WebhookResponse:
    """Handle a Fiber Academy webhook event (HMAC-verified, no auth dependency)."""
    if not settings.dotmac_academy_webhook_secret:
        raise HTTPException(
            status_code=503,
            detail="dotmac_academy webhook authentication is not configured",
        )

    raw_body = await request.body()
    if not x_webhook_signature_256:
        logger.warning("dotmac_academy webhook received without signature")
        raise HTTPException(status_code=400, detail="Missing signature")
    if not verify_dotmac_academy_signature(raw_body, x_webhook_signature_256):
        logger.warning("dotmac_academy webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from None

    event_type = (
        payload.get("event_type") or payload.get("event") or payload.get("type")
    )
    if not event_type:
        raise HTTPException(status_code=400, detail="Missing event_type")

    if not settings.default_organization_id:
        logger.error("No default organization configured for dotmac_academy webhooks")
        return WebhookResponse(
            status="error", message="No default organization configured"
        )

    organization_id = UUID(settings.default_organization_id)
    prime_tenant_context(db, organization_id)

    from app.services.dotmac_academy.training_sync import record_course_completion

    logger.info("Processing dotmac_academy webhook: %s", event_type)
    if event_type != "course_completed":
        return WebhookResponse(
            status="ignored", message=f"Unhandled event: {event_type}"
        )

    try:
        result = record_course_completion(
            db, organization_id=organization_id, payload=payload
        )
    except Exception as e:  # noqa: BLE001
        # 503 so the academy retries a transient failure rather than us swallowing
        # it as 200. The handler is idempotent (upsert keyed on credential_id).
        logger.exception("dotmac_academy webhook processing failed")
        raise HTTPException(
            status_code=503, detail=f"Webhook processing failed: {e}"
        ) from e

    return WebhookResponse(status=result.get("status", "ok"), message=str(result))
