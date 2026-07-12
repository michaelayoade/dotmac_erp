"""Neutral Sub -> ERP sync routes.

The CRM namespace remains available during transition, but new Sub clients use
this route so no new operational dependency is named or anchored on CRM.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from uuid import UUID

from app.api.sync.dotmac_crm import (
    bulk_sync,
    create_purchase_invoice,
    get_db_with_service_org,
    require_service_auth,
    upload_purchase_invoice_attachment,
)
from app.schemas.sync.dotmac_crm import (
    BulkSyncRequest,
    BulkSyncResponse,
    CRMPurchaseInvoiceAttachmentPayload,
    CRMPurchaseInvoiceAttachmentResponse,
    CRMPurchaseInvoicePayload,
    CRMPurchaseInvoiceResponse,
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
