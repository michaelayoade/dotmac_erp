"""Versioned Dotmac Sub domain adapters.

The routes authenticate, validate, delegate and serialize. They import no
connector runtime and hold no external credentials, checkpoints or retries.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.service_principal import get_db_with_service_org, require_service_auth
from app.models.expense.expense_claim import ExpenseClaim
from app.schemas.sync.dotmac_sub import SubPurchaseInvoiceStatusResponse
from app.schemas.sync.sub_operational import (
    BulkSyncRequest,
    BulkSyncResponse,
    InventoryItemDetail,
    InventoryListResponse,
    SubAvailableSerialListResponse,
    SubExpenseCategoriesResponse,
    SubExpenseClaimPayload,
    SubExpenseClaimResponse,
    SubExpenseClaimStatusResponse,
    SubMaterialRequestPayload,
    SubMaterialRequestResponse,
    SubMaterialRequestStatusRead,
    SubNccFinancialsResponse,
    SubNccStaffHeadcountResponse,
    SubPurchaseInvoiceAttachmentPayload,
    SubPurchaseInvoiceAttachmentResponse,
    SubPurchaseInvoicePayload,
    SubPurchaseInvoiceResponse,
    SubPurchaseOrderPayload,
    SubPurchaseOrderResponse,
    SyncError,
)
from app.services.inventory.material_support import MaterialSupportService
from app.services.finance.rpt.ncc_financials import ncc_financials_context
from app.services.people.hr.ncc_staff_report import NccStaffReportService
from app.services.sync.dotmac_sub_sync_service import DotMacSubSyncService
from app.services.sync.sub_purchase_invoice_status import (
    PurchaseInvoiceStatusNotFoundError,
    get_purchase_invoice_status,
)

router = APIRouter(prefix="/sync/sub", tags=["sub-sync"])
logger = logging.getLogger(__name__)
_MAX_ERROR_LEN = 200


def _sanitize_error(exc: Exception) -> str:
    return str(exc)[:_MAX_ERROR_LEN]


def _require_sub_flow_scope(auth: dict, *accepted: str) -> dict:
    scopes = set(auth.get("scopes") or [])
    if not scopes.intersection(accepted):
        raise HTTPException(
            status_code=403,
            detail=f"API key missing required scope: {accepted[0]}",
        )
    return auth


def require_sub_ap_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_sub_flow_scope(auth, "sub:ap:write")


def require_sub_ap_read_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_sub_flow_scope(auth, "sub:ap:read", "sub:ap:write")


def require_sub_domain_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_sub_flow_scope(auth, "sub:domain:write")


def require_sub_material_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_sub_flow_scope(auth, "sub:material:write")


def require_sub_material_read_scope(
    auth: dict = Depends(require_service_auth),
) -> dict:
    return _require_sub_flow_scope(auth, "sub:material:read")


def require_sub_inventory_read_scope(
    auth: dict = Depends(require_service_auth),
) -> dict:
    return _require_sub_flow_scope(auth, "sub:inventory:read")


def require_sub_expense_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_sub_flow_scope(auth, "sub:expense:write")


def require_sub_po_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_sub_flow_scope(auth, "sub:po:write")


def require_sub_ncc_read_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_sub_flow_scope(auth, "sub:ncc:read")


@router.post(
    "/bulk",
    response_model=BulkSyncResponse,
    dependencies=[Depends(require_sub_domain_scope)],
)
def sync_sub_operational_domains(
    payload: BulkSyncRequest,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> BulkSyncResponse:
    """Project rebuildable Sub operational context into ERP."""
    organization_id = UUID(str(auth["organization_id"]))
    service = DotMacSubSyncService(db)
    errors: list[SyncError] = []
    counts = {
        "projects_synced": 0,
        "project_tasks_synced": 0,
        "tickets_synced": 0,
        "work_orders_synced": 0,
    }

    for entity_type, rows, operation, identity in (
        ("project", payload.projects, service.sync_project, "source_reference"),
        ("ticket", payload.tickets, service.sync_ticket, "source_reference"),
        (
            "project_task",
            payload.project_tasks,
            service.sync_project_task,
            "source_id",
        ),
        (
            "work_order",
            payload.work_orders,
            service.sync_work_order,
            "source_reference",
        ),
    ):
        for row in rows:
            savepoint = db.begin_nested()
            try:
                if entity_type == "ticket":
                    item_errors: list[str] = []
                    operation(organization_id, row, item_errors=item_errors)
                    errors.extend(
                        SyncError(
                            entity_type="ticket_item",
                            source_reference=getattr(row, identity),
                            error=error,
                        )
                        for error in item_errors
                    )
                else:
                    operation(organization_id, row)
                savepoint.commit()
                count_key = (
                    "project_tasks_synced"
                    if entity_type == "project_task"
                    else f"{entity_type}s_synced"
                )
                counts[count_key] += 1
            except Exception as exc:
                savepoint.rollback()
                source_reference = str(getattr(row, identity))
                logger.exception(
                    "Failed to accept Sub %s %s", entity_type, source_reference
                )
                errors.append(
                    SyncError(
                        entity_type=entity_type,
                        source_reference=source_reference,
                        error=_sanitize_error(exc),
                    )
                )

    return BulkSyncResponse(**counts, errors=errors)


@router.post(
    "/purchase-invoices",
    response_model=SubPurchaseInvoiceResponse,
    status_code=201,
    dependencies=[Depends(require_sub_ap_scope)],
)
def create_sub_purchase_invoice(
    payload: SubPurchaseInvoicePayload,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubPurchaseInvoiceResponse:
    try:
        return DotMacSubSyncService(db).create_purchase_invoice(
            UUID(str(auth["organization_id"])),
            payload,
            UUID(str(auth["person_id"])),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/purchase-invoices/{source_invoice_id}",
    response_model=SubPurchaseInvoiceStatusResponse,
    dependencies=[Depends(require_sub_ap_read_scope)],
)
def get_sub_purchase_invoice_status(
    source_invoice_id: UUID,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubPurchaseInvoiceStatusResponse:
    try:
        observation = get_purchase_invoice_status(
            db,
            organization_id=UUID(str(auth["organization_id"])),
            source_invoice_id=source_invoice_id,
        )
    except PurchaseInvoiceStatusNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SubPurchaseInvoiceStatusResponse.model_validate(
        observation, from_attributes=True
    )


@router.post(
    "/purchase-invoices/{purchase_invoice_id}/attachments",
    response_model=SubPurchaseInvoiceAttachmentResponse,
    status_code=201,
    dependencies=[Depends(require_sub_ap_scope)],
)
def upload_sub_purchase_invoice_attachment(
    purchase_invoice_id: UUID,
    payload: SubPurchaseInvoiceAttachmentPayload,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubPurchaseInvoiceAttachmentResponse:
    """Attach a source document; the checksum makes retries idempotent."""
    from app.models.finance.ap.supplier_invoice import SupplierInvoice
    from app.models.finance.common.attachment import Attachment, AttachmentCategory
    from app.services.finance.common.attachment import (
        AttachmentInput,
        attachment_service,
    )

    organization_id = UUID(str(auth["organization_id"]))
    invoice = db.get(SupplierInvoice, purchase_invoice_id)
    if invoice is None or invoice.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Purchase invoice not found")
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid base64 attachment") from exc
    if not content:
        raise HTTPException(status_code=422, detail="Attachment is empty")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Attachment exceeds 10 MB")

    checksum = hashlib.sha256(content).hexdigest()
    existing = db.scalar(
        select(Attachment).where(
            Attachment.organization_id == organization_id,
            Attachment.entity_type == "SUPPLIER_INVOICE",
            Attachment.entity_id == invoice.invoice_id,
            Attachment.checksum == checksum,
        )
    )
    if existing:
        return SubPurchaseInvoiceAttachmentResponse(
            attachment_id=existing.attachment_id,
            purchase_invoice_id=invoice.invoice_id,
            file_name=existing.file_name,
            created=False,
        )

    attachment = attachment_service.save_file(
        db,
        organization_id,
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
    return SubPurchaseInvoiceAttachmentResponse(
        attachment_id=attachment.attachment_id,
        purchase_invoice_id=invoice.invoice_id,
        file_name=attachment.file_name,
        created=True,
    )


@router.post(
    "/material-requests",
    response_model=SubMaterialRequestResponse,
    status_code=201,
    dependencies=[Depends(require_sub_material_scope)],
)
def create_sub_material_request(
    payload: SubMaterialRequestPayload,
    response: Response,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubMaterialRequestResponse:
    try:
        acceptance = MaterialSupportService(db).accept_sub_request(
            organization_id=UUID(str(auth["organization_id"])),
            payload=payload,
            actor_person_id=UUID(str(auth["person_id"])),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response.status_code = 200 if acceptance.replayed else 201
    return acceptance.outcome


@router.get(
    "/material-requests/{source_request_id}",
    response_model=SubMaterialRequestStatusRead,
    dependencies=[Depends(require_sub_material_read_scope)],
)
def get_sub_material_request_status(
    source_request_id: str,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubMaterialRequestStatusRead:
    result = MaterialSupportService(db).get_sub_outcome(
        organization_id=UUID(str(auth["organization_id"])),
        source_request_id=source_request_id,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Material support request not found: {source_request_id}",
        )
    return result


@router.post(
    "/expense-claims",
    response_model=SubExpenseClaimResponse,
    status_code=201,
    dependencies=[Depends(require_sub_expense_scope)],
)
def create_sub_expense_claim(
    payload: SubExpenseClaimPayload,
    response: Response,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubExpenseClaimResponse:
    organization_id = UUID(str(auth["organization_id"]))
    existed_before = bool(
        db.scalar(
            select(ExpenseClaim.claim_id).where(
                ExpenseClaim.organization_id == organization_id,
                ExpenseClaim.source_system == "sub",
                ExpenseClaim.source_reference == payload.source_claim_id,
            )
        )
    )
    try:
        result = DotMacSubSyncService(db).create_expense_claim(
            organization_id,
            payload,
            UUID(str(auth["person_id"])),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response.status_code = 200 if existed_before else 201
    return result


@router.get(
    "/expense-claims/{source_claim_id}",
    response_model=SubExpenseClaimStatusResponse,
    dependencies=[Depends(require_sub_expense_scope)],
)
def get_sub_expense_claim_status(
    source_claim_id: str,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubExpenseClaimStatusResponse:
    result = DotMacSubSyncService(db).get_expense_claim_by_source_reference(
        UUID(str(auth["organization_id"])), source_claim_id
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Expense claim not found: {source_claim_id}",
        )
    return result


@router.get(
    "/expense-categories",
    response_model=SubExpenseCategoriesResponse,
    dependencies=[Depends(require_sub_expense_scope)],
)
def list_sub_expense_categories(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubExpenseCategoriesResponse:
    return DotMacSubSyncService(db).list_expense_categories(
        UUID(str(auth["organization_id"]))
    )


@router.post(
    "/purchase-orders",
    response_model=SubPurchaseOrderResponse,
    status_code=201,
    dependencies=[Depends(require_sub_po_scope)],
)
def create_sub_purchase_order(
    payload: SubPurchaseOrderPayload,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubPurchaseOrderResponse:
    try:
        return DotMacSubSyncService(db).create_purchase_order(
            UUID(str(auth["organization_id"])),
            payload,
            UUID(str(auth["person_id"])),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/inventory",
    response_model=InventoryListResponse,
    dependencies=[Depends(require_sub_inventory_read_scope)],
)
def list_sub_inventory(
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
    return DotMacSubSyncService(db).list_inventory_items(
        UUID(str(auth["organization_id"])),
        search=search,
        category_code=category_code,
        warehouse_id=warehouse_id,
        include_zero_stock=include_zero_stock,
        only_below_reorder=only_below_reorder,
        only_with_available_serials=only_with_available_serials,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/inventory/meta/categories",
    dependencies=[Depends(require_sub_inventory_read_scope)],
)
def list_sub_inventory_categories(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> list[dict]:
    return DotMacSubSyncService(db).get_categories(UUID(str(auth["organization_id"])))


@router.get(
    "/inventory/meta/warehouses",
    dependencies=[Depends(require_sub_inventory_read_scope)],
)
def list_sub_inventory_warehouses(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> list[dict]:
    return DotMacSubSyncService(db).get_warehouses(UUID(str(auth["organization_id"])))


@router.get(
    "/inventory/serials/available",
    response_model=SubAvailableSerialListResponse,
    dependencies=[Depends(require_sub_inventory_read_scope)],
)
def list_sub_available_inventory_serials(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
    item_code: str = Query(..., min_length=1, max_length=50),
    warehouse_code: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> SubAvailableSerialListResponse:
    try:
        return DotMacSubSyncService(db).list_available_serials_for_sub(
            UUID(str(auth["organization_id"])),
            item_code=item_code,
            warehouse_code=warehouse_code,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/inventory/{item_id}",
    response_model=InventoryItemDetail,
    dependencies=[Depends(require_sub_inventory_read_scope)],
)
def get_sub_inventory_item(
    item_id: UUID,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> InventoryItemDetail:
    detail = DotMacSubSyncService(db).get_inventory_item_detail(
        UUID(str(auth["organization_id"])), item_id
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return detail


@router.get(
    "/ncc/financials",
    response_model=SubNccFinancialsResponse,
    dependencies=[Depends(require_sub_ncc_read_scope)],
)
def get_sub_ncc_financials(
    year: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    as_of_date: str | None = None,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubNccFinancialsResponse:
    data = ncc_financials_context(
        db,
        UUID(str(auth["organization_id"])),
        year=year,
        start_date=start_date,
        end_date=end_date,
        as_of_date=as_of_date,
    )
    return SubNccFinancialsResponse(**data)


@router.get(
    "/ncc/staff-headcount",
    response_model=SubNccStaffHeadcountResponse,
    dependencies=[Depends(require_sub_ncc_read_scope)],
)
def get_sub_ncc_staff_headcount(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubNccStaffHeadcountResponse:
    report = NccStaffReportService(db).build(UUID(str(auth["organization_id"])))
    return SubNccStaffHeadcountResponse(**report)
