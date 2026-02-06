"""
CRM Integration API Routes.

Handles webhook events from crm.dotmac.io and sync operations.
"""

import hashlib
import hmac
import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.config import settings
from app.db import SessionLocal

logger = logging.getLogger(__name__)

# Main router for authenticated sync endpoints
router = APIRouter(prefix="/crm", tags=["crm-integration"])

# Webhook router (no authentication - uses signature verification)
webhook_router = APIRouter(prefix="/crm", tags=["crm-webhooks"])


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Pydantic Schemas
# =============================================================================


class SyncRequest(BaseModel):
    """Request to trigger a sync operation."""

    entity_type: str = Field(
        ...,
        description="Entity type to sync: 'ticket', 'project', or 'all'",
    )
    incremental: bool = Field(
        default=True,
        description="If True, only sync records modified since last sync",
    )


class SyncResponse(BaseModel):
    """Response from sync operation."""

    entity_type: str
    total_records: int
    created: int
    updated: int
    skipped: int
    errors: int
    success_rate: str


class WebhookEvent(BaseModel):
    """CRM webhook event payload."""

    event_type: str = Field(..., description="Event type (created, updated, deleted)")
    entity_type: str = Field(..., description="Entity type (ticket, project)")
    entity_id: str = Field(..., description="Entity ID in CRM")
    subscriber_id: Optional[str] = Field(None, description="Subscriber/customer ID")
    data: dict[str, Any] = Field(default_factory=dict, description="Entity data")
    timestamp: Optional[str] = Field(None, description="Event timestamp ISO format")


class WebhookResponse(BaseModel):
    """Response to webhook."""

    status: str
    message: Optional[str] = None


class HealthResponse(BaseModel):
    """CRM connection health check."""

    healthy: bool
    crm_url: str
    message: Optional[str] = None


# =============================================================================
# Webhook Signature Verification
# =============================================================================


def verify_crm_signature(payload: bytes, signature: str) -> bool:
    """
    Verify CRM webhook signature using HMAC-SHA256.

    Args:
        payload: Raw request body bytes
        signature: Signature from X-CRM-Signature header

    Returns:
        True if signature is valid
    """
    if not settings.crm_webhook_secret:
        logger.warning("CRM webhook secret not configured - skipping verification")
        return True  # Allow in development

    expected = hmac.new(
        settings.crm_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


# =============================================================================
# Authenticated Sync Endpoints
# =============================================================================


@router.post("/sync", response_model=list[SyncResponse])
def trigger_sync(
    request_data: SyncRequest,
    organization_id: UUID = Depends(require_organization_id),
    _auth=Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """
    Manually trigger a CRM sync operation.

    Requires: crm.sync permission

    Args:
        entity_type: 'ticket', 'project', or 'all'
        incremental: Only sync modified records if True
    """
    from app.services.crm import CRMClient
    from app.services.crm.sync import ProjectSyncService, TicketSyncService

    results = []

    try:
        with CRMClient() as client:
            # Health check first
            if not client.health_check():
                raise HTTPException(
                    status_code=502,
                    detail="CRM API is not accessible",
                )

            entity_type = request_data.entity_type.lower()

            if entity_type in ("ticket", "all"):
                ticket_service = TicketSyncService(db, organization_id)
                ticket_result = ticket_service.sync(
                    client,
                    incremental=request_data.incremental,
                )
                results.append(
                    SyncResponse(
                        entity_type="ticket",
                        total_records=ticket_result.total_records,
                        created=ticket_result.created_count,
                        updated=ticket_result.updated_count,
                        skipped=ticket_result.skipped_count,
                        errors=ticket_result.error_count,
                        success_rate=f"{ticket_result.success_rate:.1f}%",
                    )
                )

            if entity_type in ("project", "all"):
                project_service = ProjectSyncService(db, organization_id)
                project_result = project_service.sync(
                    client,
                    incremental=request_data.incremental,
                )
                results.append(
                    SyncResponse(
                        entity_type="project",
                        total_records=project_result.total_records,
                        created=project_result.created_count,
                        updated=project_result.updated_count,
                        skipped=project_result.skipped_count,
                        errors=project_result.error_count,
                        success_rate=f"{project_result.success_rate:.1f}%",
                    )
                )

            if not results:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown entity type: {request_data.entity_type}",
                )

            db.commit()

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("CRM sync failed: %s", str(e))
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")

    return results


@router.get("/health", response_model=HealthResponse)
def check_crm_health(
    organization_id: UUID = Depends(require_organization_id),
    _auth=Depends(require_tenant_auth),
):
    """
    Check CRM API connectivity.

    Returns health status and CRM URL.
    """
    from app.services.crm import CRMClient

    try:
        with CRMClient() as client:
            is_healthy = client.health_check()

            return HealthResponse(
                healthy=is_healthy,
                crm_url=settings.crm_api_url,
                message="CRM API accessible"
                if is_healthy
                else "CRM API not responding",
            )

    except Exception as e:
        logger.error("CRM health check failed: %s", str(e))
        return HealthResponse(
            healthy=False,
            crm_url=settings.crm_api_url,
            message=str(e),
        )


@router.get("/ticket/{crm_ticket_id}")
def lookup_ticket_by_crm_id(
    crm_ticket_id: str,
    organization_id: UUID = Depends(require_organization_id),
    _auth=Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """
    Look up synced ticket by CRM ticket ID.

    Returns the ERP ticket if it has been synced.
    """
    from app.services.crm.sync import TicketSyncService

    service = TicketSyncService(db, organization_id)
    ticket = service.get_by_crm_id(crm_ticket_id)

    if not ticket:
        raise HTTPException(
            status_code=404,
            detail=f"Ticket with CRM ID {crm_ticket_id} not found",
        )

    return {
        "ticket_id": str(ticket.ticket_id),
        "ticket_number": ticket.ticket_number,
        "subject": ticket.subject,
        "status": ticket.status.value,
        "priority": ticket.priority.value,
    }


@router.get("/project/{crm_project_id}")
def lookup_project_by_crm_id(
    crm_project_id: str,
    organization_id: UUID = Depends(require_organization_id),
    _auth=Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """
    Look up synced project by CRM project ID.

    Returns the ERP project if it has been synced.
    """
    from app.services.crm.sync import ProjectSyncService

    service = ProjectSyncService(db, organization_id)
    project = service.get_by_crm_id(crm_project_id)

    if not project:
        raise HTTPException(
            status_code=404,
            detail=f"Project with CRM ID {crm_project_id} not found",
        )

    return {
        "project_id": str(project.project_id),
        "project_code": project.project_code,
        "project_name": project.project_name,
        "status": project.status.value,
        "project_type": project.project_type.value,
    }


# =============================================================================
# Webhook Endpoint (No Authentication - Uses Signature Verification)
# =============================================================================


@webhook_router.post("/webhook", response_model=WebhookResponse)
async def crm_webhook(
    request: Request,
    x_crm_signature: Optional[str] = Header(None, alias="X-CRM-Signature"),
    db: Session = Depends(get_db),
):
    """
    Handle CRM webhook events.

    This endpoint does NOT require authentication - it uses HMAC
    signature verification instead.

    CRM will send webhooks for events like:
    - ticket.created: New ticket created
    - ticket.updated: Ticket modified
    - ticket.deleted: Ticket deleted
    - project.created: New project created
    - project.updated: Project modified
    - project.deleted: Project deleted
    """
    from app.services.crm import CRMClient
    from app.services.crm.sync import ProjectSyncService, TicketSyncService

    # Read raw body for signature verification
    raw_body = await request.body()

    # Verify signature
    if settings.crm_webhook_secret:
        if not x_crm_signature:
            logger.warning("CRM webhook received without signature")
            raise HTTPException(status_code=400, detail="Missing signature")

        if not verify_crm_signature(raw_body, x_crm_signature):
            logger.warning("CRM webhook signature verification failed")
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse payload
    try:
        payload = await request.json()
    except Exception as e:
        logger.error("Invalid CRM webhook payload: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Extract event info
    event_type = payload.get("event_type") or payload.get("event")
    entity_type = payload.get("entity_type") or payload.get("type")
    entity_id = payload.get("entity_id") or payload.get("id")
    data = payload.get("data", payload)

    if not event_type or not entity_type:
        logger.warning("CRM webhook missing event_type or entity_type")
        raise HTTPException(
            status_code=400,
            detail="Missing event_type or entity_type",
        )

    # Determine organization from subscriber_id mapping or data
    # For now, use default organization if configured
    if not settings.default_organization_id:
        logger.error("No default organization configured for CRM webhooks")
        return WebhookResponse(
            status="error",
            message="No default organization configured",
        )

    organization_id = UUID(settings.default_organization_id)

    logger.info(
        "Processing CRM webhook: %s.%s for %s",
        entity_type,
        event_type,
        entity_id,
    )

    try:
        with CRMClient() as client:
            if entity_type.lower() == "ticket":
                result = TicketSyncService(db, organization_id).handle_webhook(
                    client, event_type, data
                )

            elif entity_type.lower() == "project":
                result = ProjectSyncService(db, organization_id).handle_webhook(
                    client, event_type, data
                )

            else:
                logger.warning("Unknown entity type in webhook: %s", entity_type)
                return WebhookResponse(
                    status="ignored",
                    message=f"Unknown entity type: {entity_type}",
                )

            db.commit()

            if result.error_count > 0:
                return WebhookResponse(
                    status="error",
                    message=f"Processed with {result.error_count} errors",
                )

            return WebhookResponse(
                status="success",
                message=f"Synced {result.synced_count} records",
            )

    except Exception as e:
        logger.exception("CRM webhook processing failed: %s", str(e))
        db.rollback()
        return WebhookResponse(
            status="error",
            message=str(e),
        )
