"""Canonical Dotmac Sub operational adapter."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.orm import Session

from app.api.service_principal import get_db_with_service_org, require_service_auth
from app.schemas.sync.dotmac_sub import (
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
    SubPurchaseInvoiceAttachmentPayload,
    SubPurchaseInvoiceAttachmentResponse,
    SubPurchaseInvoicePayload,
    SubPurchaseInvoiceResponse,
    SubPurchaseInvoiceStatusResponse,
    SubPurchaseOrderPayload,
    SubPurchaseOrderResponse,
    SubPurchaseOrderVariationPayload,
)
from app.services.inventory.material_support import MaterialSupportService
from app.services.sync.dotmac_sub_sync_service import DotmacSubSyncService
from app.services.sync.sub.errors import (
    SubNotFoundError,
    SubPayloadTooLargeError,
    SubReplayConflictError,
    SubValidationError,
)
from app.services.sync.sub_purchase_invoice_status import (
    PurchaseInvoiceStatusNotFoundError,
    get_purchase_invoice_status,
)

router = APIRouter(prefix="/sync/sub", tags=["sub-sync"])


def _require_scope(auth: dict, *accepted: str) -> dict:
    scopes = set(auth.get("scopes") or [])
    if not scopes.intersection(accepted):
        raise HTTPException(
            status_code=403,
            detail=f"API key missing required scope: {accepted[0]}",
        )
    return auth


def require_sub_domain_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_scope(auth, "sub:domain:write")


def require_sub_material_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_scope(auth, "sub:material:write")


def require_sub_material_read_scope(
    auth: dict = Depends(require_service_auth),
) -> dict:
    return _require_scope(auth, "sub:material:read", "sub:material:write")


def require_sub_inventory_read_scope(
    auth: dict = Depends(require_service_auth),
) -> dict:
    return _require_scope(auth, "sub:inventory:read")


def require_sub_expense_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_scope(auth, "sub:expense:write")


def require_sub_expense_read_scope(
    auth: dict = Depends(require_service_auth),
) -> dict:
    return _require_scope(auth, "sub:expense:read", "sub:expense:write")


def require_sub_po_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_scope(auth, "sub:po:write")


def require_sub_ap_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_scope(auth, "sub:ap:write")


def require_sub_ap_read_scope(
    auth: dict = Depends(require_service_auth),
) -> dict:
    return _require_scope(auth, "sub:ap:read", "sub:ap:write")


def _organization_id(auth: dict) -> UUID:
    value = auth["organization_id"]
    return value if isinstance(value, UUID) else UUID(str(value))


def _person_id(auth: dict) -> UUID:
    value = auth["person_id"]
    return value if isinstance(value, UUID) else UUID(str(value))


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
    return DotmacSubSyncService(db).bulk_sync(_organization_id(auth), payload)


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
    try:
        acceptance = DotmacSubSyncService(db).accept_expense_claim(
            _organization_id(auth), payload, _person_id(auth)
        )
    except SubReplayConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SubValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.status_code = 200 if acceptance.replayed else 201
    return acceptance.outcome


@router.get(
    "/expense-claims/{source_request_id}",
    response_model=SubExpenseClaimStatusResponse,
    dependencies=[Depends(require_sub_expense_read_scope)],
)
def get_sub_expense_claim_status(
    source_request_id: str,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubExpenseClaimStatusResponse:
    result = DotmacSubSyncService(db).get_expense_claim_by_source_id(
        _organization_id(auth), source_request_id
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Expense claim not found: {source_request_id}",
        )
    return result


@router.get(
    "/expense-categories",
    response_model=SubExpenseCategoriesResponse,
    dependencies=[Depends(require_sub_expense_read_scope)],
)
def list_sub_expense_categories(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubExpenseCategoriesResponse:
    return DotmacSubSyncService(db).list_expense_categories(_organization_id(auth))


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
            organization_id=_organization_id(auth),
            payload=payload,
            actor_person_id=_person_id(auth),
        )
    except SubReplayConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SubNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (SubValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        organization_id=_organization_id(auth),
        source_request_id=source_request_id,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Material support request not found: {source_request_id}",
        )
    return result


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
        return DotmacSubSyncService(db).create_purchase_order(
            _organization_id(auth), payload, _person_id(auth)
        )
    except SubReplayConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/purchase-orders/variations",
    response_model=SubPurchaseOrderResponse,
    status_code=201,
    dependencies=[Depends(require_sub_po_scope)],
)
def create_sub_purchase_order_variation(
    payload: SubPurchaseOrderVariationPayload,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubPurchaseOrderResponse:
    try:
        return DotmacSubSyncService(db).create_purchase_order_variation(
            _organization_id(auth), payload, _person_id(auth)
        )
    except SubReplayConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        return DotmacSubSyncService(db).create_purchase_invoice(
            _organization_id(auth), payload, _person_id(auth)
        )
    except SubReplayConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/purchase-invoices/{source_invoice_id}",
    response_model=SubPurchaseInvoiceStatusResponse,
    dependencies=[Depends(require_sub_ap_read_scope)],
)
def get_sub_purchase_invoice_status(
    source_invoice_id: str = Path(..., min_length=1, max_length=120),
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> SubPurchaseInvoiceStatusResponse:
    try:
        observation = get_purchase_invoice_status(
            db,
            organization_id=_organization_id(auth),
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
    try:
        return DotmacSubSyncService(db).upload_purchase_invoice_attachment(
            _organization_id(auth), purchase_invoice_id, payload, _person_id(auth)
        )
    except SubNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SubValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SubPayloadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc


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
    return DotmacSubSyncService(db).list_inventory_items(
        _organization_id(auth),
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
    return DotmacSubSyncService(db).get_categories(_organization_id(auth))


@router.get(
    "/inventory/meta/warehouses",
    dependencies=[Depends(require_sub_inventory_read_scope)],
)
def list_sub_warehouses(
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> list[dict]:
    return DotmacSubSyncService(db).get_warehouses(_organization_id(auth))


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
        return DotmacSubSyncService(db).list_available_serials_for_sub(
            _organization_id(auth),
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
    result = DotmacSubSyncService(db).get_inventory_item_detail(
        _organization_id(auth), item_id
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return result
