"""Goods receipt routes for the AP API."""

from uuid import UUID

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.api.finance.ap_routes.base import router
from app.models.finance.ap.goods_receipt import GoodsReceipt, ReceiptStatus
from app.schemas.finance.ap import GRCreate, GRRead
from app.schemas.finance.common import ListResponse
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ap import goods_receipt_service


@router.post(
    "/goods-receipts", response_model=GRRead, status_code=status.HTTP_201_CREATED
)
def create_goods_receipt(
    payload: GRCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:goods_receipts:create")),
    db: Session = Depends(get_db_with_org),
):
    """Create a goods receipt against a PO."""
    received_by_user_id = UUID(auth["person_id"])
    try:
        input_data = goods_receipt_service.build_receipt_input(
            po_id=payload.po_id,
            receipt_date=payload.receipt_date,
            lines_raw=[
                {
                    "po_line_id": line.po_line_id,
                    "quantity_received": line.quantity_received,
                    "warehouse_id": line.warehouse_id,
                }
                for line in payload.lines
            ],
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return goods_receipt_service.create_receipt(
        db, organization_id, input_data, received_by_user_id
    )


@router.get("/goods-receipts/{receipt_id}", response_model=GRRead)
def get_goods_receipt(
    receipt_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:goods_receipts:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get a goods receipt by ID."""
    return goods_receipt_service.get(db, str(receipt_id), organization_id)


@router.get("/goods-receipts", response_model=ListResponse[GRRead])
def list_goods_receipts(
    organization_id: UUID = Depends(require_organization_id),
    po_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ap:goods_receipts:read")),
    db: Session = Depends(get_db_with_org),
):
    """List goods receipts with filters."""
    status_value = None
    if status:
        try:
            status_value = ReceiptStatus(status)
        except ValueError:
            status_value = None
    receipts = goods_receipt_service.list(
        db=db,
        organization_id=str(organization_id),
        po_id=str(po_id) if po_id else None,
        status=status_value,
        limit=limit,
        offset=offset,
    )
    count_stmt = select(func.count(GoodsReceipt.receipt_id)).where(
        GoodsReceipt.organization_id == organization_id
    )
    if po_id:
        count_stmt = count_stmt.where(GoodsReceipt.po_id == po_id)
    if status_value:
        count_stmt = count_stmt.where(GoodsReceipt.status == status_value)
    total = db.scalar(count_stmt) or 0
    return ListResponse(
        items=receipts,
        count=total,
        limit=limit,
        offset=offset,
    )


@router.post("/goods-receipts/{receipt_id}/inspect", response_model=GRRead)
def start_gr_inspection(
    receipt_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:goods_receipts:update")),
    db: Session = Depends(get_db_with_org),
):
    """Start inspection for a goods receipt."""
    return goods_receipt_service.start_inspection(db, organization_id, receipt_id)


@router.post("/goods-receipts/{receipt_id}/accept", response_model=GRRead)
def accept_goods_receipt(
    receipt_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:goods_receipts:approve")),
    db: Session = Depends(get_db_with_org),
):
    """Accept all items in a goods receipt."""
    return goods_receipt_service.accept_all(db, organization_id, receipt_id)
