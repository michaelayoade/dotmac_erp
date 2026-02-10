"""
DotMac CRM Sync API - Endpoints for CRM entity synchronization.

Handles:
- Bulk sync from CRM (projects, tickets, work orders)
- Webhook for real-time entity updates
- Entity lookup for expense claim dropdowns
- Expense totals for CRM entities
- Inventory data for CRM field service
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.auth import ApiKey
from app.models.person import Person
from app.rls import set_current_organization_sync
from app.schemas.sync.dotmac_crm import (
    BulkSyncRequest,
    BulkSyncResponse,
    CompanyListResponse,
    CRMMaterialRequestPayload,
    CRMMaterialRequestResponse,
    CRMMaterialRequestStatusRead,
    CRMProjectPayload,
    CRMProjectRead,
    CRMTicketPayload,
    CRMTicketRead,
    CRMWorkOrderPayload,
    CRMWorkOrderRead,
    DepartmentListResponse,
    ExpenseTotalsRequest,
    ExpenseTotalsResponse,
    InventoryItemDetail,
    InventoryListResponse,
    PersonListResponse,
    SyncError,
)
from app.services.auth import hash_api_key
from app.services.auth_dependencies import require_tenant_auth
from app.services.sync.dotmac_crm_sync_service import DotMacCRMSyncService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync/crm", tags=["crm-sync"])

# Maximum error detail length to avoid leaking internals
_MAX_ERROR_LEN = 200


def _get_db():
    """Database session dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _sanitize_error(e: Exception) -> str:
    """Truncate error message to avoid leaking internal details."""
    msg = str(e)
    if len(msg) > _MAX_ERROR_LEN:
        return msg[:_MAX_ERROR_LEN] + "..."
    return msg


# ============ Service Account Authentication ============


def require_service_auth(
    x_api_key: str = Header(..., description="CRM service API key"),
    db: Session = Depends(_get_db),
) -> dict:
    """
    Authenticate service-to-service calls from DotMac CRM.

    Validates the API key, sets RLS context, and returns the organization context.

    Returns:
        dict with organization_id and service info
    """
    now = datetime.now(UTC)

    # Find API key by hash
    stmt = select(ApiKey).where(
        ApiKey.key_hash == hash_api_key(x_api_key),
        ApiKey.is_active.is_(True),
        ApiKey.revoked_at.is_(None),
    )
    api_key = db.scalar(stmt)

    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Check expiration
    if api_key.expires_at and api_key.expires_at <= now:
        raise HTTPException(status_code=401, detail="API key expired")

    # Get organization from associated person
    if not api_key.person_id:
        raise HTTPException(
            status_code=403,
            detail="API key not associated with a user",
        )

    person = db.get(Person, api_key.person_id)
    if not person or not person.organization_id:
        raise HTTPException(
            status_code=403,
            detail="User has no organization access",
        )

    # Set RLS context for data isolation
    set_current_organization_sync(db, person.organization_id)

    # Update last used
    api_key.last_used_at = now

    logger.info(
        "CRM service authenticated: org=%s, key=%s",
        person.organization_id,
        api_key.label or api_key.id,
    )

    return {
        "organization_id": person.organization_id,
        "api_key_id": api_key.id,
        "service_label": api_key.label,
    }


# ============ Sync Endpoints (CRM → ERP) ============


@router.post("/bulk", response_model=BulkSyncResponse, status_code=200)
def bulk_sync(
    payload: BulkSyncRequest,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
) -> BulkSyncResponse:
    """
    Bulk sync projects, tickets, and work orders from DotMac CRM.

    Idempotent - safe to retry. Uses CRM entity IDs for deduplication.
    Processes in order: projects -> tickets -> work orders (respects dependencies).
    Payload lists are capped at 500 items each.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    errors: list[SyncError] = []

    # Sync projects first (work orders may reference them)
    projects_synced = 0
    for proj in payload.projects:
        savepoint = db.begin_nested()
        try:
            service.sync_project(org_id, proj)
            savepoint.commit()
            projects_synced += 1
        except Exception as e:
            savepoint.rollback()
            logger.exception("Failed to sync project %s", proj.crm_id)
            errors.append(
                SyncError(
                    entity_type="project",
                    crm_id=proj.crm_id,
                    error=_sanitize_error(e),
                )
            )

    # Sync tickets
    tickets_synced = 0
    for ticket in payload.tickets:
        savepoint = db.begin_nested()
        try:
            service.sync_ticket(org_id, ticket)
            savepoint.commit()
            tickets_synced += 1
        except Exception as e:
            savepoint.rollback()
            logger.exception("Failed to sync ticket %s", ticket.crm_id)
            errors.append(
                SyncError(
                    entity_type="ticket",
                    crm_id=ticket.crm_id,
                    error=_sanitize_error(e),
                )
            )

    # Sync work orders last (references projects/tickets)
    work_orders_synced = 0
    for wo in payload.work_orders:
        savepoint = db.begin_nested()
        try:
            service.sync_work_order(org_id, wo)
            savepoint.commit()
            work_orders_synced += 1
        except Exception as e:
            savepoint.rollback()
            logger.exception("Failed to sync work order %s", wo.crm_id)
            errors.append(
                SyncError(
                    entity_type="work_order",
                    crm_id=wo.crm_id,
                    error=_sanitize_error(e),
                )
            )

    db.commit()

    logger.info(
        "CRM bulk sync complete: %d projects, %d tickets, %d work_orders, %d errors",
        projects_synced,
        tickets_synced,
        work_orders_synced,
        len(errors),
    )

    return BulkSyncResponse(
        projects_synced=projects_synced,
        tickets_synced=tickets_synced,
        work_orders_synced=work_orders_synced,
        errors=errors,
    )


# ============ Webhook Endpoint (CRM → ERP real-time) ============


@router.post("/webhook/{entity_type}", status_code=200)
def handle_webhook(
    entity_type: str,
    payload: CRMProjectPayload | CRMTicketPayload | CRMWorkOrderPayload,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
) -> dict:
    """
    Handle real-time entity updates from CRM webhook.

    Accepts a single entity payload and syncs it immediately.
    Supported entity_type values: project, ticket, work_order.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)

    try:
        if entity_type == "project" and isinstance(payload, CRMProjectPayload):
            service.sync_project(org_id, payload)
        elif entity_type == "ticket" and isinstance(payload, CRMTicketPayload):
            service.sync_ticket(org_id, payload)
        elif entity_type == "work_order" and isinstance(payload, CRMWorkOrderPayload):
            service.sync_work_order(org_id, payload)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown entity type: {entity_type}",
            )
        db.commit()
        return {"status": "ok", "entity_type": entity_type, "crm_id": payload.crm_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Webhook sync failed for %s %s", entity_type, payload.crm_id)
        raise HTTPException(
            status_code=500,
            detail=_sanitize_error(e),
        ) from e


# ============ List Endpoints (for ERP UI dropdowns) ============


@router.get("/projects", response_model=list[CRMProjectRead])
def list_crm_projects(
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(_get_db),
    search: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[CRMProjectRead]:
    """
    List CRM projects for expense claim dropdown.

    Used by ERP UI when creating expense claims to select related CRM project.
    """
    org_id = UUID(auth["organization_id"])
    service = DotMacCRMSyncService(db)
    return service.list_projects(
        org_id, search=search, status=status, limit=min(limit, 100)
    )


@router.get("/tickets", response_model=list[CRMTicketRead])
def list_crm_tickets(
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(_get_db),
    search: str | None = None,
    limit: int = 50,
) -> list[CRMTicketRead]:
    """
    List CRM tickets for expense claim dropdown.

    Used by ERP UI when creating expense claims to select related CRM ticket.
    """
    org_id = UUID(auth["organization_id"])
    service = DotMacCRMSyncService(db)
    return service.list_tickets(org_id, search=search, limit=min(limit, 100))


@router.get("/work-orders", response_model=list[CRMWorkOrderRead])
def list_crm_work_orders(
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(_get_db),
    search: str | None = None,
    employee_id: UUID | None = None,
    limit: int = 50,
) -> list[CRMWorkOrderRead]:
    """
    List CRM work orders for expense claim dropdown.

    Used by ERP UI when creating expense claims to select related CRM work order.
    Optionally filter by assigned employee.
    """
    org_id = UUID(auth["organization_id"])
    service = DotMacCRMSyncService(db)
    return service.list_work_orders(
        org_id, search=search, employee_id=employee_id, limit=min(limit, 100)
    )


# ============ Expense Totals Endpoint (ERP → CRM) ============


@router.post("/expense-totals", response_model=ExpenseTotalsResponse)
def get_expense_totals(
    payload: ExpenseTotalsRequest,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
) -> ExpenseTotalsResponse:
    """
    Get expense totals for CRM entities (batch).

    Called by DotMac CRM to display expense summaries on project/ticket detail pages.
    Uses batched queries instead of per-entity lookups for efficiency.
    List sizes capped at 200 per entity type.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)

    result = service.get_batch_expense_totals(
        org_id,
        project_crm_ids=payload.project_crm_ids,
        ticket_crm_ids=payload.ticket_crm_ids,
        work_order_crm_ids=payload.work_order_crm_ids,
    )

    return ExpenseTotalsResponse(totals=result)


# ============ Inventory Sync Endpoints (ERP → CRM) ============


@router.get("/inventory/meta/categories")
def list_inventory_categories(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
) -> list[dict]:
    """
    Get list of item categories for filtering inventory.

    Returns list of {code, name} objects.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    return service.get_categories(org_id)


@router.get("/inventory/meta/warehouses")
def list_warehouses(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
) -> list[dict]:
    """
    Get list of warehouses for filtering inventory.

    Returns list of {warehouse_id, code, name} objects.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    return service.get_warehouses(org_id)


@router.get("/inventory/{item_id}", response_model=InventoryItemDetail)
def get_inventory_item(
    item_id: UUID,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
) -> InventoryItemDetail:
    """
    Get detailed inventory item with warehouse-level stock breakdown.

    Called by DotMac CRM to get full item details including stock per warehouse.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)

    item_detail = service.get_inventory_item_detail(org_id, item_id)
    if not item_detail:
        raise HTTPException(status_code=404, detail="Item not found")

    return item_detail


@router.get("/inventory", response_model=InventoryListResponse)
def list_inventory(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
    search: str | None = None,
    category_code: str | None = None,
    warehouse_id: UUID | None = None,
    include_zero_stock: bool = False,
    only_below_reorder: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> InventoryListResponse:
    """
    List inventory items with current stock levels for CRM.

    Called by DotMac CRM to retrieve available inventory for installation assignments.
    Returns items with stock quantities (on-hand, reserved, available).
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)

    return service.list_inventory_items(
        org_id,
        search=search,
        category_code=category_code,
        warehouse_id=warehouse_id,
        include_zero_stock=include_zero_stock,
        only_below_reorder=only_below_reorder,
        limit=limit,
        offset=offset,
    )


# ============ Workforce / Department Endpoints (ERP → CRM) ============


@router.get("/workforce/departments", response_model=DepartmentListResponse)
def list_departments(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
    include_inactive: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> DepartmentListResponse:
    """
    List departments with members for CRM service team mapping.

    Returns active departments with their managers and employee members.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    return service.list_departments(
        org_id,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )


# ============ Contact Sync Endpoints (ERP → CRM) ============


@router.get("/contacts/companies", response_model=CompanyListResponse)
def list_companies(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
    updated_since: datetime | None = None,
    include_inactive: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> CompanyListResponse:
    """
    List company/government customers for CRM contacts sync.

    Supports incremental sync via updated_since parameter.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    return service.list_companies(
        org_id,
        updated_since=updated_since,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )


@router.get("/contacts/people", response_model=PersonListResponse)
def list_people_contacts(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
    updated_since: datetime | None = None,
    include_inactive: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> PersonListResponse:
    """
    List individual customers as person contacts for CRM sync.

    Extracts email/phone from the primary_contact JSONB.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    return service.list_people_contacts(
        org_id,
        updated_since=updated_since,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )


# ============ Material Request Endpoints (CRM → ERP) ============


@router.post(
    "/material-requests",
    response_model=CRMMaterialRequestResponse,
    status_code=201,
)
def create_material_request(
    payload: CRMMaterialRequestPayload,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
) -> CRMMaterialRequestResponse:
    """
    Create a material request from CRM.

    Idempotent: if omni_id already exists, returns the existing request.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)

    try:
        result = service.create_material_request(org_id, payload)
        db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        db.rollback()
        logger.exception(
            "Failed to create material request omni_id=%s", payload.omni_id
        )
        raise HTTPException(status_code=500, detail=_sanitize_error(e)) from e


@router.get(
    "/material-requests/{omni_id}",
    response_model=CRMMaterialRequestStatusRead,
)
def get_material_request_status(
    omni_id: str,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
) -> CRMMaterialRequestStatusRead:
    """
    Get material request status by CRM omni_id.

    Used by CRM to poll request status after creation.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)

    result = service.get_material_request_by_crm_id(org_id, omni_id)
    if not result:
        raise HTTPException(
            status_code=404, detail=f"Material request not found: {omni_id}"
        )
    return result
