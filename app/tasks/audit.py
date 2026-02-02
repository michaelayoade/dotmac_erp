"""
Audit Module Background Tasks - Celery tasks for audit logging.

Handles:
- Async audit event logging (non-blocking for HTTP requests)
"""

import logging
from typing import Any, Optional

from celery import shared_task

from app.db import SessionLocal

logger = logging.getLogger(__name__)


@shared_task
def log_audit_event(
    actor_type: str,
    actor_id: Optional[str],
    action: str,
    entity_type: str,
    entity_id: Optional[str],
    status_code: int,
    is_success: bool,
    ip_address: Optional[str],
    user_agent: Optional[str],
    request_id: Optional[str],
    metadata_: dict[str, Any],
) -> dict:
    """
    Asynchronously log an audit event.

    Called from the audit middleware to avoid blocking HTTP responses.

    Args:
        actor_type: Type of actor (user, system, api_key)
        actor_id: ID of the actor
        action: HTTP method (GET, POST, etc.)
        entity_type: Request path
        entity_id: Optional entity ID from header
        status_code: HTTP response status code
        is_success: Whether request was successful (status < 400)
        ip_address: Client IP address
        user_agent: Client user agent
        request_id: Request correlation ID
        metadata_: Additional metadata (path, query params)

    Returns:
        Dict with event_id if successful, or error details
    """
    from app.models.audit import AuditEvent, AuditActorType
    from app.schemas.audit import AuditEventCreate

    logger.debug("Logging audit event: %s %s", action, entity_type)

    result: dict[str, Any] = {
        "success": False,
        "event_id": None,
        "error": None,
    }

    try:
        # Resolve actor type
        try:
            resolved_actor_type = AuditActorType(actor_type)
        except ValueError:
            resolved_actor_type = AuditActorType.system

        with SessionLocal() as db:
            payload = AuditEventCreate(
                actor_type=resolved_actor_type,
                actor_id=actor_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                status_code=status_code,
                is_success=is_success,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                metadata_=metadata_,
            )

            data = payload.model_dump()
            if payload.occurred_at is None:
                data.pop("occurred_at", None)

            event = AuditEvent(**data)
            db.add(event)
            db.commit()

            result["success"] = True
            result["event_id"] = str(event.id)
            logger.debug("Audit event logged: %s", event.id)

    except Exception as e:
        logger.exception("Failed to log audit event")
        result["error"] = str(e)

    return result
