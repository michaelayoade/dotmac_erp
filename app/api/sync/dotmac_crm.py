"""
DotMac CRM Sync API - Endpoints for CRM entity synchronization.

Handles:
- Bulk sync from CRM (projects, tickets, work orders)
- Entity lookup for expense claim dropdowns
- Expense totals for CRM entities
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.auth import ApiKey
from app.models.person import Person
from app.rls import set_current_organization_sync
from app.schemas.sync.dotmac_crm import (
    BulkSyncRequest,
    BulkSyncResponse,
    CRMProjectRead,
    CRMTicketRead,
    CRMWorkOrderRead,
    ExpenseTotals,
    ExpenseTotalsRequest,
    ExpenseTotalsResponse,
    InventoryItemDetail,
    InventoryListResponse,
    SyncError,
)
from app.services.auth import hash_api_key
from app.services.auth_dependencies import require_tenant_auth
from app.services.sync.dotmac_crm_sync_service import DotMacCRMSyncService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync/crm", tags=["crm-sync"])


def _get_db():
    """Database session dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
    now = datetime.now(timezone.utc)

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
            detail="API key not associated with a user. Service accounts require a user with organization access.",
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
    Processes in order: projects → tickets → work orders (respects dependencies).
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
                SyncError(entity_type="project", crm_id=proj.crm_id, error=str(e))
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
                SyncError(entity_type="ticket", crm_id=ticket.crm_id, error=str(e))
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
                SyncError(entity_type="work_order", crm_id=wo.crm_id, error=str(e))
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


# ============ List Endpoints (for ERP UI dropdowns) ============


@router.get("/projects", response_model=list[CRMProjectRead])
def list_crm_projects(
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(_get_db),
    search: Optional[str] = None,
    status: Optional[str] = None,
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
    search: Optional[str] = None,
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
    search: Optional[str] = None,
    employee_id: Optional[UUID] = None,
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
    Get expense totals for CRM entities.

    Called by DotMac CRM to display expense summaries on project/ticket detail pages.

    Request body should contain lists of CRM IDs to query:
    - project_crm_ids: List of CRM project UUIDs
    - ticket_crm_ids: List of CRM ticket UUIDs
    - work_order_crm_ids: List of CRM work order UUIDs

    Returns totals keyed by CRM ID with amounts by status (draft, submitted, approved, paid).
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    result: dict[str, ExpenseTotals] = {}

    # Get project totals
    for crm_id in payload.project_crm_ids:
        totals = service.get_expense_totals_for_project(org_id, crm_id)
        if totals:
            result[crm_id] = totals

    # Get ticket totals
    for crm_id in payload.ticket_crm_ids:
        totals = service.get_expense_totals_for_ticket(org_id, crm_id)
        if totals:
            result[crm_id] = totals

    # Get work order totals
    for crm_id in payload.work_order_crm_ids:
        totals = service.get_expense_totals_for_work_order(org_id, crm_id)
        if totals:
            result[crm_id] = totals

    db.commit()

    return ExpenseTotalsResponse(totals=result)


# ============ Inventory Sync Endpoints (ERP → CRM) ============


@router.get("/inventory", response_model=InventoryListResponse)
def list_inventory(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
    search: Optional[str] = None,
    category_code: Optional[str] = None,
    warehouse_id: Optional[UUID] = None,
    include_zero_stock: bool = False,
    only_below_reorder: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> InventoryListResponse:
    """
    List inventory items with current stock levels for CRM.

    Called by DotMac CRM to retrieve available inventory for installation assignments.
    Returns items with stock quantities (on-hand, reserved, available).

    Query parameters:
    - search: Search term for item code, name, or barcode
    - category_code: Filter by item category code
    - warehouse_id: Filter by specific warehouse
    - include_zero_stock: Include items with zero available stock (default: false)
    - only_below_reorder: Only return items below reorder point (default: false)
    - limit: Max items per page (default: 100, max: 500)
    - offset: Pagination offset

    Returns:
    - items: List of InventoryItemStock objects
    - total_count: Total matching items (before pagination)
    - has_more: Whether there are more items beyond this page
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)

    result = service.list_inventory_items(
        org_id,
        search=search,
        category_code=category_code,
        warehouse_id=warehouse_id,
        include_zero_stock=include_zero_stock,
        only_below_reorder=only_below_reorder,
        limit=min(limit, 500),
        offset=offset,
    )
    db.commit()
    return result


@router.get("/inventory/{item_id}", response_model=InventoryItemDetail)
def get_inventory_item(
    item_id: UUID,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(_get_db),
) -> InventoryItemDetail:
    """
    Get detailed inventory item with warehouse-level stock breakdown.

    Called by DotMac CRM to get full item details including stock per warehouse.

    Returns:
    - Item details (code, name, description, category, UOM)
    - Total stock levels (on-hand, reserved, available)
    - Per-warehouse stock breakdown
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)

    item_detail = service.get_inventory_item_detail(org_id, item_id)
    if not item_detail:
        raise HTTPException(status_code=404, detail="Item not found")

    db.commit()

    return item_detail


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
    result = service.get_categories(org_id)
    db.commit()
    return result


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
    result = service.get_warehouses(org_id)
    db.commit()
    return result
