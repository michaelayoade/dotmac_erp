"""
Audit Module Background Tasks - Celery tasks for audit logging.

Handles:
- Async audit event logging (non-blocking for HTTP requests)
"""

import logging
from typing import Any

from celery import shared_task

from app.db import SessionLocal

logger = logging.getLogger(__name__)


@shared_task
def log_audit_event(
    actor_type: str,
    organization_id: str | None,
    actor_person_id: str | None,
    actor_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    status_code: int,
    is_success: bool,
    ip_address: str | None,
    user_agent: str | None,
    request_id: str | None,
    metadata_: dict[str, Any],
) -> dict:
    """
    Asynchronously log an audit event.

    Called from the audit middleware to avoid blocking HTTP responses.

    Args:
        actor_type: Type of actor (user, system, api_key)
        organization_id: Tenant organization ID (if available)
        actor_person_id: Person UUID for actor (if resolvable)
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
    from app.models.audit import AuditActorType, AuditEvent
    from app.schemas.audit import AuditEventCreate
    from app.services.common import coerce_uuid

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
            org_uuid = coerce_uuid(organization_id, raise_http=False)
            actor_person_uuid = coerce_uuid(actor_person_id, raise_http=False)
            payload = AuditEventCreate(
                actor_type=resolved_actor_type,
                organization_id=org_uuid,
                actor_person_id=actor_person_uuid,
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
