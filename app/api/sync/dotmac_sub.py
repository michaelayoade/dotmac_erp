"""Neutral Sub -> ERP sync routes.

The CRM namespace remains available during transition, but new Sub clients use
this route so no new operational dependency is named or anchored on CRM.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from uuid import UUID

from app.api.sync.dotmac_crm import (
    bulk_sync,
    create_expense_claim,
    create_material_request,
    create_purchase_order,
    create_purchase_order_variation,
    create_purchase_invoice,
    get_expense_claim_status,
    get_inventory_item,
    get_material_request_status,
    get_db_with_service_org,
    list_available_inventory_serials,
    list_expense_categories,
    list_inventory,
    list_inventory_categories,
    list_warehouses,
    require_service_auth,
    upload_purchase_invoice_attachment,
)
from app.schemas.sync.dotmac_crm import (
    BulkSyncRequest,
    BulkSyncResponse,
    CRMAvailableSerialListResponse,
    CRMExpenseCategoriesResponse,
    CRMExpenseClaimResponse,
    CRMExpenseClaimStatusResponse,
    CRMMaterialRequestResponse,
    CRMMaterialRequestStatusRead,
    CRMPurchaseOrderResponse,
    CRMPurchaseInvoiceAttachmentPayload,
    CRMPurchaseInvoiceAttachmentResponse,
    CRMPurchaseInvoicePayload,
    CRMPurchaseInvoiceResponse,
    InventoryItemDetail,
    InventoryListResponse,
)

router = APIRouter(prefix="/sync/sub", tags=["sub-sync"])


def require_sub_ap_scope(auth: dict = Depends(require_service_auth)) -> dict:
    scopes = auth.get("scopes") or []
    if scopes and not {"sub:ap:write", "crm:ap:write"}.intersection(scopes):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=403, detail="API key missing required scope: sub:ap:write"
        )
    return auth


def require_sub_domain_scope(auth: dict = Depends(require_service_auth)) -> dict:
    scopes = auth.get("scopes") or []
    if scopes and not {"sub:domain:write", "crm:sync:write"}.intersection(scopes):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=403,
            detail="API key missing required scope: sub:domain:write",
        )
    return auth


def _require_sub_flow_scope(auth: dict, *accepted: str) -> dict:
    scopes = set(auth.get("scopes") or [])
    if scopes and not scopes.intersection(accepted):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=403,
            detail=f"API key missing required scope: {accepted[0]}",
        )
    return auth


def require_sub_material_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_sub_flow_scope(auth, "sub:material:write", "crm:material:write")


def require_sub_expense_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_sub_flow_scope(auth, "sub:expense:write", "crm:expense:write")


def require_sub_po_scope(auth: dict = Depends(require_service_auth)) -> dict:
    return _require_sub_flow_scope(auth, "sub:po:write", "crm:po:write")


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
    return bulk_sync(payload, auth, db)


@router.post(
    "/purchase-invoices",
    response_model=CRMPurchaseInvoiceResponse,
    status_code=201,
    dependencies=[Depends(require_sub_ap_scope)],
)
def create_sub_purchase_invoice(
    payload: CRMPurchaseInvoicePayload,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> CRMPurchaseInvoiceResponse:
    return create_purchase_invoice(payload, auth, db)


@router.post(
    "/purchase-invoices/{purchase_invoice_id}/attachments",
    response_model=CRMPurchaseInvoiceAttachmentResponse,
    status_code=201,
    dependencies=[Depends(require_sub_ap_scope)],
)
def upload_sub_purchase_invoice_attachment(
    purchase_invoice_id: UUID,
    payload: CRMPurchaseInvoiceAttachmentPayload,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> CRMPurchaseInvoiceAttachmentResponse:
    return upload_purchase_invoice_attachment(purchase_invoice_id, payload, auth, db)


# These are aliases over ERP's established idempotent services. The legacy
# /sync/crm routes remain available only for the old client during migration.
router.add_api_route(
    "/material-requests",
    create_material_request,
    methods=["POST"],
    response_model=CRMMaterialRequestResponse,
    status_code=201,
    dependencies=[Depends(require_sub_material_scope)],
    name="create_sub_material_request",
)
router.add_api_route(
    "/material-requests/{omni_id}",
    get_material_request_status,
    methods=["GET"],
    response_model=CRMMaterialRequestStatusRead,
    name="get_sub_material_request_status",
)
router.add_api_route(
    "/expense-claims",
    create_expense_claim,
    methods=["POST"],
    response_model=CRMExpenseClaimResponse,
    status_code=201,
    dependencies=[Depends(require_sub_expense_scope)],
    name="create_sub_expense_claim",
)
router.add_api_route(
    "/expense-claims/{omni_id}",
    get_expense_claim_status,
    methods=["GET"],
    response_model=CRMExpenseClaimStatusResponse,
    name="get_sub_expense_claim_status",
)
router.add_api_route(
    "/expense-categories",
    list_expense_categories,
    methods=["GET"],
    response_model=CRMExpenseCategoriesResponse,
    name="list_sub_expense_categories",
)
router.add_api_route(
    "/purchase-orders",
    create_purchase_order,
    methods=["POST"],
    response_model=CRMPurchaseOrderResponse,
    status_code=201,
    dependencies=[Depends(require_sub_po_scope)],
    name="create_sub_purchase_order",
)
router.add_api_route(
    "/purchase-orders/variations",
    create_purchase_order_variation,
    methods=["POST"],
    response_model=CRMPurchaseOrderResponse,
    status_code=201,
    dependencies=[Depends(require_sub_po_scope)],
    name="create_sub_purchase_order_variation",
)
router.add_api_route(
    "/inventory",
    list_inventory,
    methods=["GET"],
    response_model=InventoryListResponse,
    name="list_sub_inventory",
)
router.add_api_route(
    "/inventory/meta/categories",
    list_inventory_categories,
    methods=["GET"],
    name="list_sub_inventory_categories",
)
router.add_api_route(
    "/inventory/meta/warehouses",
    list_warehouses,
    methods=["GET"],
    name="list_sub_inventory_warehouses",
)
router.add_api_route(
    "/inventory/serials/available",
    list_available_inventory_serials,
    methods=["GET"],
    response_model=CRMAvailableSerialListResponse,
    name="list_sub_available_inventory_serials",
)
router.add_api_route(
    "/inventory/{item_id}",
    get_inventory_item,
    methods=["GET"],
    response_model=InventoryItemDetail,
    name="get_sub_inventory_item",
)
