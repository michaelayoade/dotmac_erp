"""
DotMac CRM Sync API - Endpoints for CRM entity synchronization.

Handles:
- Bulk sync from CRM (projects, tickets, work orders)
- Webhook for real-time entity updates
- Entity lookup for expense claim dropdowns
- Expense totals for CRM entities
- Inventory data for CRM field service
"""

import base64
import binascii
import hashlib
import io
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org
from app.api.service_principal import (
    _LAST_USED_THROTTLE as _LAST_USED_THROTTLE,
    _last_used_is_stale as _last_used_is_stale,
    get_db_with_service_org,
    require_any_service_scope,
    require_service_auth,
    require_service_scope,
)
from app.rls import set_current_organization_sync
from app.schemas.sync.dotmac_crm import (
    BulkSyncRequest,
    BulkSyncResponse,
    CompanyListResponse,
    CRMAvailableSerialListResponse,
    CRMExpenseCategoriesResponse,
    CRMExpenseClaimPayload,
    CRMExpenseClaimResponse,
    CRMExpenseClaimStatusResponse,
    CRMInventoryItemPayload,
    CRMInventoryItemResponse,
    CRMMaterialRequestPayload,
    CRMMaterialRequestResponse,
    CRMMaterialRequestStatusRead,
    CRMProjectPayload,
    CRMProjectRead,
    CRMPurchaseOrderPayload,
    CRMPurchaseOrderResponse,
    CRMPurchaseOrderVariationPayload,
    CRMPurchaseInvoiceAttachmentPayload,
    CRMPurchaseInvoiceAttachmentResponse,
    CRMPurchaseInvoicePayload,
    CRMPurchaseInvoiceResponse,
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
    ReconcileOrphansRequest,
    ReconcileOrphansResponse,
    SyncError,
    WorkforceEmployeeListResponse,
)
from app.services.auth_dependencies import require_tenant_auth
from app.services.finance.rpt.ncc_financials import ncc_financials_context
from app.services.people.hr.ncc_staff_report import NccStaffReportService
from app.services.sync.dotmac_crm_sync_service import DotMacCRMSyncService

logger = logging.getLogger(__name__)


def require_crm_material_sync_retired() -> None:
    """Fail closed after material/item authority moves from CRM to Sub."""
    raise HTTPException(
        status_code=410,
        detail="CRM item and material-request sync is retired; use /sync/sub",
    )


router = APIRouter(prefix="/sync/crm", tags=["crm-sync"])

# Maximum error detail length to avoid leaking internals
_MAX_ERROR_LEN = 200


def _sanitize_error(e: Exception) -> str:
    """Truncate error message to avoid leaking internal details."""
    msg = str(e)
    if len(msg) > _MAX_ERROR_LEN:
        return msg[:_MAX_ERROR_LEN] + "..."
    return msg


# ============ Sync Endpoints (CRM → ERP) ============


@router.post("/bulk", response_model=BulkSyncResponse, status_code=200)
def bulk_sync(
    payload: BulkSyncRequest,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> BulkSyncResponse:
    """
    Bulk sync projects, tickets, and work orders from DotMac CRM.

    Idempotent - safe to retry. Uses CRM entity IDs for deduplication.
    Processes in order: projects -> tickets -> project tasks -> work orders.
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
            ticket_item_errors: list[str] = []
            service.sync_ticket(org_id, ticket, item_errors=ticket_item_errors)
            savepoint.commit()
            tickets_synced += 1
            for item_error in ticket_item_errors:
                errors.append(
                    SyncError(
                        entity_type="ticket_item",
                        crm_id=ticket.crm_id,
                        error=item_error,
                    )
                )
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

    # Sync project tasks after their projects and optional tickets exist.
    project_tasks_synced = 0
    for project_task in payload.project_tasks:
        savepoint = db.begin_nested()
        try:
            service.sync_project_task(org_id, project_task)
            savepoint.commit()
            project_tasks_synced += 1
        except Exception as e:
            savepoint.rollback()
            logger.exception("Failed to sync project task %s", project_task.source_id)
            errors.append(
                SyncError(
                    entity_type="project_task",
                    crm_id=project_task.source_id,
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

    logger.info(
        "CRM bulk sync complete: %d projects, %d project_tasks, %d tickets, "
        "%d work_orders, %d errors",
        projects_synced,
        project_tasks_synced,
        tickets_synced,
        work_orders_synced,
        len(errors),
    )

    db.commit()
    return BulkSyncResponse(
        projects_synced=projects_synced,
        project_tasks_synced=project_tasks_synced,
        tickets_synced=tickets_synced,
        work_orders_synced=work_orders_synced,
        errors=errors,
    )


@router.post(
    "/reconcile-orphans",
    response_model=ReconcileOrphansResponse,
    status_code=200,
    dependencies=[Depends(require_any_service_scope("crm:sync:write", "crm:write"))],
)
def reconcile_orphans(
    payload: ReconcileOrphansRequest,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> ReconcileOrphansResponse:
    """
    Reconcile orphaned CRM entities after a full CRM sync run.

    CRM sends the complete set of ids a clean ``sync_all_active`` run saw for
    one entity type; ACTIVE mappings not in that set are soft-closed (with
    min-fetch / max-terminate safety rails — a suspicious set is skipped, not
    applied). Idempotent: already-closed mappings are never re-examined.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    try:
        result = service.reconcile_orphans(
            org_id,
            entity_type=payload.entity_type,
            seen_crm_ids=payload.seen_crm_ids,
            active_count=payload.active_count,
        )
        db.commit()
        return ReconcileOrphansResponse(**result)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception(
            "Failed to reconcile CRM orphans entity_type=%s", payload.entity_type
        )
        raise HTTPException(status_code=500, detail=_sanitize_error(e)) from e


# ============ Webhook Endpoint (CRM → ERP real-time) ============


@router.post("/webhook/{entity_type}", status_code=200)
def handle_webhook(
    entity_type: str,
    payload: (
        CRMProjectPayload
        | CRMTicketPayload
        | CRMWorkOrderPayload
        | CRMInventoryItemPayload
    ),
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> dict:
    """
    Handle real-time entity updates from CRM webhook.

    Accepts a single entity payload and syncs it immediately.
    Supported entity_type values: project, ticket, work_order, item, inventory_item.
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
        elif entity_type in {"item", "inventory_item"}:
            raise HTTPException(
                status_code=410,
                detail="CRM inventory-item writes are retired; ERP owns item facts",
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown entity type: {entity_type}",
            )
        db.commit()
        return {"status": "ok", "entity_type": entity_type, "crm_id": payload.crm_id}
    except HTTPException:
        db.rollback()
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
    db: Session = Depends(get_db_with_org),
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
    db: Session = Depends(get_db_with_org),
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
    db: Session = Depends(get_db_with_org),
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


@router.post(
    "/inventory-items",
    response_model=CRMInventoryItemResponse,
    status_code=201,
    dependencies=[Depends(require_crm_material_sync_retired)],
)
def upsert_inventory_item(
    payload: CRMInventoryItemPayload,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> CRMInventoryItemResponse:
    """Create or update an ERP inventory item from CRM."""
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    try:
        result = service.upsert_inventory_item(org_id, payload)
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        db.rollback()
        logger.exception(
            "Failed to upsert CRM inventory item crm_id=%s", payload.crm_id
        )
        raise HTTPException(status_code=500, detail=_sanitize_error(e)) from e


# ============ Expense Totals Endpoint (ERP → CRM) ============


@router.post("/expense-totals", response_model=ExpenseTotalsResponse)
def get_expense_totals(
    payload: ExpenseTotalsRequest,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> ExpenseTotalsResponse:
    """
    Get expense totals for CRM entities (batch).

    Called by DotMac CRM to display expense summaries on project/ticket detail pages.
    Uses batched queries instead of per-entity lookups for efficiency.
    List sizes capped at 200 per entity type.
    """
    org_id = auth["organization_id"]
    set_current_organization_sync(db, org_id)
    service = DotMacCRMSyncService(db)

    result = service.get_batch_expense_totals(
        org_id,
        project_crm_ids=payload.project_crm_ids,
        ticket_crm_ids=payload.ticket_crm_ids,
        work_order_crm_ids=payload.work_order_crm_ids,
    )

    return ExpenseTotalsResponse(totals=result)


# ============ Inventory Sync Endpoints (ERP → CRM) ============


@router.get(
    "/inventory/meta/categories",
    dependencies=[Depends(require_crm_material_sync_retired)],
)
def list_inventory_categories(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> list[dict]:
    """
    Get list of item categories for filtering inventory.

    Returns list of {code, name} objects.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    return service.get_categories(org_id)


@router.get(
    "/inventory/meta/warehouses",
    dependencies=[Depends(require_crm_material_sync_retired)],
)
def list_warehouses(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> list[dict]:
    """
    Get list of warehouses for filtering inventory.

    Returns list of {warehouse_id, code, name} objects.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    return service.get_warehouses(org_id)


@router.get(
    "/inventory/serials/available",
    response_model=CRMAvailableSerialListResponse,
    dependencies=[Depends(require_crm_material_sync_retired)],
)
def list_available_inventory_serials(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
    item_code: str = Query(..., min_length=1, max_length=50),
    warehouse_code: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> CRMAvailableSerialListResponse:
    """
    List available serial numbers for CRM material request selection.

    CRM should call this before submitting a serial-tracked ISSUE material
    request, then send the selected serial_numbers on the matching request line.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    try:
        return service.list_available_serials_for_crm(
            org_id,
            item_code=item_code,
            warehouse_code=warehouse_code,
            limit=limit,
            offset=offset,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    "/inventory/{item_id}",
    response_model=InventoryItemDetail,
    dependencies=[Depends(require_crm_material_sync_retired)],
)
def get_inventory_item(
    item_id: UUID,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
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


@router.get(
    "/inventory",
    response_model=InventoryListResponse,
    dependencies=[Depends(require_crm_material_sync_retired)],
)
def list_inventory(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
    search: str | None = None,
    category_code: str | None = None,
    warehouse_id: UUID | None = None,
    include_zero_stock: bool = False,
    only_below_reorder: bool = False,
    only_with_available_serials: bool = False,
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
        only_with_available_serials=only_with_available_serials,
        limit=limit,
        offset=offset,
    )


# ============ Workforce / Department Endpoints (ERP → CRM) ============


@router.get(
    "/workforce/employees",
    response_model=WorkforceEmployeeListResponse,
    dependencies=[Depends(require_service_scope("crm:workforce:read"))],
)
def list_workforce_employees(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
    include_inactive: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> WorkforceEmployeeListResponse:
    """
    List employees for CRM staff/author mapping.

    Returns employees with stable employee_id and email.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    return service.list_workforce_employees(
        org_id,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )


@router.get("/workforce/departments", response_model=DepartmentListResponse)
def list_departments(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
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
    db: Session = Depends(get_db_with_service_org),
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
    db: Session = Depends(get_db_with_service_org),
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
    dependencies=[Depends(require_crm_material_sync_retired)],
)
def create_material_request(
    payload: CRMMaterialRequestPayload,
    response: Response,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> CRMMaterialRequestResponse:
    """
    Create a material request from CRM.

    Immutable idempotent create-and-issue endpoint for CRM material issues.
    """
    from app.models.inventory.material_request import MaterialRequest

    org_id = auth["organization_id"]
    person_id = auth["person_id"]
    service = DotMacCRMSyncService(db)
    existed_before = bool(
        db.scalar(
            select(MaterialRequest.request_id).where(
                MaterialRequest.organization_id == org_id,
                MaterialRequest.crm_id == payload.omni_id,
            )
        )
    )

    try:
        result = service.create_material_request(org_id, payload, person_id)
        if existed_before:
            response.status_code = 200
        else:
            response.status_code = 201
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception(
            "Failed to create material request omni_id=%s", payload.omni_id
        )
        raise HTTPException(status_code=500, detail=_sanitize_error(e)) from e


@router.get(
    "/material-requests/{omni_id}",
    response_model=CRMMaterialRequestStatusRead,
    dependencies=[Depends(require_crm_material_sync_retired)],
)
def get_material_request_status(
    omni_id: str,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
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


# ============ Expense Claim Endpoints (CRM → ERP) ============


@router.post(
    "/expense-claims",
    response_model=CRMExpenseClaimResponse,
    status_code=201,
    dependencies=[Depends(require_service_scope("crm:expense:write"))],
)
def create_expense_claim(
    payload: CRMExpenseClaimPayload,
    response: Response,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> CRMExpenseClaimResponse:
    """
    Create an expense claim from a CRM field-technician expense request.

    Immutable idempotent create-and-submit endpoint: an identical resend of
    the same omni_id returns the existing claim (200); a changed resend is
    rejected (409).
    """
    from app.models.expense.expense_claim import ExpenseClaim

    org_id = auth["organization_id"]
    person_id = auth["person_id"]
    service = DotMacCRMSyncService(db)
    existed_before = bool(
        db.scalar(
            select(ExpenseClaim.claim_id).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.crm_id == payload.omni_id,
            )
        )
    )

    try:
        result = service.create_expense_claim(org_id, payload, person_id)
        if existed_before:
            response.status_code = 200
        else:
            response.status_code = 201
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to create expense claim omni_id=%s", payload.omni_id)
        raise HTTPException(status_code=500, detail=_sanitize_error(e)) from e


@router.get(
    "/expense-claims/{omni_id}",
    response_model=CRMExpenseClaimStatusResponse,
)
def get_expense_claim_status(
    omni_id: str,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> CRMExpenseClaimStatusResponse:
    """
    Get expense claim status by CRM omni_id.

    Used by CRM to poll claim status (approval / rejection / payment)
    after creation.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)

    result = service.get_expense_claim_by_crm_id(org_id, omni_id)
    if not result:
        raise HTTPException(
            status_code=404, detail=f"Expense claim not found: {omni_id}"
        )
    return result


@router.get(
    "/expense-categories",
    response_model=CRMExpenseCategoriesResponse,
)
def list_expense_categories(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> CRMExpenseCategoriesResponse:
    """
    List active expense categories for the CRM expense-request form.

    CRM uses category_code when submitting expense claims; requires_receipt
    and max_amount_per_claim let the CRM validate before sending.
    """
    org_id = auth["organization_id"]
    service = DotMacCRMSyncService(db)
    return service.list_expense_categories(org_id)


# ============ Purchase Order Endpoints (CRM → ERP) ============


@router.post(
    "/purchase-orders",
    response_model=CRMPurchaseOrderResponse,
    status_code=201,
    dependencies=[Depends(require_service_scope("crm:po:write"))],
)
def create_purchase_order(
    payload: CRMPurchaseOrderPayload,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> CRMPurchaseOrderResponse:
    """
    Create a DRAFT purchase order from a CRM vendor quote approval.

    Idempotent: if a PO for the same omni_work_order_id already exists, returns
    the existing PO.
    """
    org_id = auth["organization_id"]
    person_id = auth["person_id"]
    service = DotMacCRMSyncService(db)

    try:
        result = service.create_purchase_order(org_id, payload, person_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Failed to create purchase order omni_work_order_id=%s",
            payload.omni_work_order_id,
        )
        raise HTTPException(status_code=500, detail=_sanitize_error(e)) from e


@router.post(
    "/purchase-invoices",
    response_model=CRMPurchaseInvoiceResponse,
    status_code=201,
    dependencies=[Depends(require_service_scope("crm:ap:write"))],
)
def create_purchase_invoice(
    payload: CRMPurchaseInvoicePayload,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> CRMPurchaseInvoiceResponse:
    """Create an idempotent DRAFT AP invoice matched to an existing PO."""
    service = DotMacCRMSyncService(db)
    try:
        return service.create_purchase_invoice(
            auth["organization_id"], payload, auth["person_id"]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to create purchase invoice source_id=%s", payload.crm_invoice_id
        )
        raise HTTPException(status_code=500, detail=_sanitize_error(exc)) from exc


@router.post(
    "/purchase-invoices/{purchase_invoice_id}/attachments",
    response_model=CRMPurchaseInvoiceAttachmentResponse,
    status_code=201,
    dependencies=[Depends(require_service_scope("crm:ap:write"))],
)
def upload_purchase_invoice_attachment(
    purchase_invoice_id: UUID,
    payload: CRMPurchaseInvoiceAttachmentPayload,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> CRMPurchaseInvoiceAttachmentResponse:
    """Attach one source document; content checksum makes retries idempotent."""
    from app.models.finance.ap.supplier_invoice import SupplierInvoice
    from app.models.finance.common.attachment import Attachment, AttachmentCategory
    from app.services.finance.common.attachment import (
        AttachmentInput,
        attachment_service,
    )

    org_id = UUID(str(auth["organization_id"]))
    invoice = db.get(SupplierInvoice, purchase_invoice_id)
    if invoice is None or invoice.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Purchase invoice not found")
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail="Invalid base64 attachment"
        ) from exc
    if not content:
        raise HTTPException(status_code=422, detail="Attachment is empty")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Attachment exceeds 10 MB")

    checksum = hashlib.sha256(content).hexdigest()
    existing = db.scalar(
        select(Attachment).where(
            Attachment.organization_id == org_id,
            Attachment.entity_type == "SUPPLIER_INVOICE",
            Attachment.entity_id == invoice.invoice_id,
            Attachment.checksum == checksum,
        )
    )
    if existing:
        return CRMPurchaseInvoiceAttachmentResponse(
            attachment_id=existing.attachment_id,
            purchase_invoice_id=invoice.invoice_id,
            file_name=existing.file_name,
            created=False,
        )

    attachment = attachment_service.save_file(
        db,
        org_id,
        AttachmentInput(
            entity_type="SUPPLIER_INVOICE",
            entity_id=str(invoice.invoice_id),
            file_name=payload.file_name,
            content_type=payload.mime_type,
            category=AttachmentCategory.INVOICE,
            description="Uploaded by Sub vendor purchase-invoice sync",
        ),
        io.BytesIO(content),
        UUID(str(auth["person_id"])),
    )
    return CRMPurchaseInvoiceAttachmentResponse(
        attachment_id=attachment.attachment_id,
        purchase_invoice_id=invoice.invoice_id,
        file_name=attachment.file_name,
        created=True,
    )


@router.post(
    "/purchase-orders/variations",
    response_model=CRMPurchaseOrderResponse,
    status_code=201,
)
def create_purchase_order_variation(
    payload: CRMPurchaseOrderVariationPayload,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> CRMPurchaseOrderResponse:
    """
    Create a PO amendment from a CRM variation approval.

    Idempotent: if a PO amendment with the same variation_id already exists,
    returns the existing amended PO.  The baseline PO is marked SUPERSEDED.
    Approval workflow integrity is preserved — the amendment starts in DRAFT.
    """
    org_id = auth["organization_id"]
    person_id = auth["person_id"]
    service = DotMacCRMSyncService(db)

    try:
        result = service.create_purchase_order_variation(org_id, payload, person_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Failed to create PO variation variation_id=%s, wo=%s",
            payload.variation_id,
            payload.omni_work_order_id,
        )
        raise HTTPException(status_code=500, detail=_sanitize_error(e)) from e


# ============ NCC Regulatory Endpoints (ERP → CRM) ============
#
# Service-authenticated variants of the NCC read endpoints in
# app/api/finance/ncc.py and app/api/people/ncc.py. Those use JWT-based
# require_tenant_auth (for the ERP UI); these mirror them behind the CRM's
# X-API-Key service auth so the CRM regulatory-pack aggregator can pull the
# year-end return's Section F (financials) and Section G (staff head-count).


class NccFinancialsResponse(BaseModel):
    period: dict
    summary: dict
    detail: dict
    note: str


class NccStaffHeadcountResponse(BaseModel):
    """NCC Section G matrix: category -> nationality -> gender -> count."""

    total_active: int
    by_category: dict[str, dict[str, dict[str, int]]]


@router.get(
    "/ncc/financials",
    response_model=NccFinancialsResponse,
    dependencies=[Depends(require_service_scope("crm:ncc:read"))],
)
def ncc_financials(
    year: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    as_of_date: str | None = None,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> NccFinancialsResponse:
    """NCC Section F financials for the CRM aggregator.

    Composed from the income-statement / balance-sheet / expense-summary
    services for the given year (or explicit date range / as-at date).
    """
    data = ncc_financials_context(
        db,
        auth["organization_id"],
        year=year,
        start_date=start_date,
        end_date=end_date,
        as_of_date=as_of_date,
    )
    return NccFinancialsResponse(**data)


@router.get(
    "/ncc/staff-headcount",
    response_model=NccStaffHeadcountResponse,
    dependencies=[Depends(require_service_scope("crm:ncc:read"))],
)
def ncc_staff_headcount(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> NccStaffHeadcountResponse:
    """NCC Section G active-staff head-count for the CRM aggregator.

    Matrix of NCC category x Nigerian/Expatriate x Male/Female.
    """
    org_id = auth["organization_id"]
    if not isinstance(org_id, UUID):
        org_id = UUID(str(org_id))
    report = NccStaffReportService(db).build(org_id)
    return NccStaffHeadcountResponse(**report)
