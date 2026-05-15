"""Purchase order routes for the AP API."""

from uuid import UUID

from fastapi import Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.api.finance.ap_routes.base import router
from app.models.finance.ap.purchase_order import POStatus, PurchaseOrder
from app.schemas.finance.ap import POCreate, PORead
from app.schemas.finance.common import ListResponse
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ap import (
    POLineInput,
    PurchaseOrderInput,
    purchase_order_service,
)


@router.post(
    "/purchase-orders", response_model=PORead, status_code=status.HTTP_201_CREATED
)
def create_purchase_order(
    payload: POCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:purchase_orders:create")),
    db: Session = Depends(get_db_with_org),
):
    """Create a new purchase order."""
    created_by_user_id = UUID(auth["person_id"])
    lines = [
        POLineInput(
            item_id=line.item_id,
            description=line.description,
            quantity_ordered=line.quantity,
            unit_price=line.unit_price,
            expense_account_id=line.expense_account_id,
        )
        for line in payload.lines
    ]
    input_data = PurchaseOrderInput(
        supplier_id=payload.supplier_id,
        po_date=payload.po_date,
        expected_delivery_date=payload.expected_delivery_date,
        currency_code=payload.currency_code,
        terms_and_conditions=payload.description,
        lines=lines,
    )
    return purchase_order_service.create_po(
        db, organization_id, input_data, created_by_user_id
    )


@router.get("/purchase-orders/{po_id}", response_model=PORead)
def get_purchase_order(
    po_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:purchase_orders:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get a purchase order by ID."""
    return purchase_order_service.get(db, str(po_id), organization_id)


@router.get("/purchase-orders", response_model=ListResponse[PORead])
def list_purchase_orders(
    organization_id: UUID = Depends(require_organization_id),
    supplier_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ap:purchase_orders:read")),
    db: Session = Depends(get_db_with_org),
):
    """List purchase orders with filters."""
    status_value = None
    if status:
        try:
            status_value = POStatus(status)
        except ValueError:
            status_value = None
    purchase_orders = purchase_order_service.list(
        db=db,
        organization_id=str(organization_id),
        supplier_id=str(supplier_id) if supplier_id else None,
        status=status_value,
        limit=limit,
        offset=offset,
    )
    count_stmt = select(func.count(PurchaseOrder.po_id)).where(
        PurchaseOrder.organization_id == organization_id
    )
    if supplier_id:
        count_stmt = count_stmt.where(PurchaseOrder.supplier_id == supplier_id)
    if status_value:
        count_stmt = count_stmt.where(PurchaseOrder.status == status_value)
    total = db.scalar(count_stmt) or 0
    return ListResponse(
        items=purchase_orders,
        count=total,
        limit=limit,
        offset=offset,
    )


@router.post("/purchase-orders/{po_id}/submit", response_model=PORead)
def submit_po_for_approval(
    po_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:purchase_orders:submit")),
    db: Session = Depends(get_db_with_org),
):
    """Submit PO for approval."""
    submitted_by_user_id = UUID(auth["person_id"])
    return purchase_order_service.submit_for_approval(
        db, organization_id, po_id, submitted_by_user_id
    )


@router.post("/purchase-orders/{po_id}/approve", response_model=PORead)
def approve_purchase_order(
    po_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:purchase_orders:approve")),
    db: Session = Depends(get_db_with_org),
):
    """Approve a purchase order."""
    approved_by_user_id = UUID(auth["person_id"])
    return purchase_order_service.approve_po(
        db, organization_id, po_id, approved_by_user_id
    )


@router.post("/purchase-orders/{po_id}/cancel", response_model=PORead)
def cancel_purchase_order(
    po_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:purchase_orders:void")),
    db: Session = Depends(get_db_with_org),
):
    """Cancel a purchase order."""
    return purchase_order_service.cancel_po(db, organization_id, po_id)
