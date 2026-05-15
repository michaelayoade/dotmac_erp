"""Payment routes for the AP API."""

from datetime import date
from uuid import UUID

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.api.finance.ap_routes.base import router
from app.models.finance.ap.supplier_payment import APPaymentStatus, SupplierPayment
from app.schemas.finance.ap import APPaymentCreate, APPaymentRead
from app.schemas.finance.common import ListResponse, PostingResultSchema
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ap import ap_posting_adapter, supplier_payment_service


@router.post(
    "/payments", response_model=APPaymentRead, status_code=status.HTTP_201_CREATED
)
def create_ap_payment(
    payload: APPaymentCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:payments:create")),
    db: Session = Depends(get_db_with_org),
):
    """Create a new AP payment."""
    created_by_user_id = UUID(auth["person_id"])
    try:
        input_data = supplier_payment_service.build_payment_input(
            supplier_id=payload.supplier_id,
            payment_date=payload.payment_date,
            payment_method_str=payload.payment_method,
            bank_account_id=payload.bank_account_id,
            currency_code=payload.currency_code,
            allocations_raw=[
                {"invoice_id": allocation.invoice_id, "amount": allocation.amount}
                for allocation in payload.allocations
            ],
            reference=payload.reference_number,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return supplier_payment_service.create_payment(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/payments/{payment_id}", response_model=APPaymentRead)
def get_ap_payment(
    payment_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:payments:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get an AP payment by ID."""
    return supplier_payment_service.get(db, str(payment_id), organization_id)


@router.get("/payments", response_model=ListResponse[APPaymentRead])
def list_ap_payments(
    organization_id: UUID = Depends(require_organization_id),
    supplier_id: UUID | None = None,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ap:payments:read")),
    db: Session = Depends(get_db_with_org),
):
    """List AP payments with filters."""
    status_value = None
    if status:
        try:
            status_value = APPaymentStatus(status)
        except ValueError:
            status_value = None
    payments = supplier_payment_service.list(
        db=db,
        organization_id=str(organization_id),
        supplier_id=str(supplier_id) if supplier_id else None,
        status=status_value,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )
    count_stmt = select(func.count(SupplierPayment.payment_id)).where(
        SupplierPayment.organization_id == organization_id
    )
    if supplier_id:
        count_stmt = count_stmt.where(SupplierPayment.supplier_id == supplier_id)
    if status_value:
        count_stmt = count_stmt.where(SupplierPayment.status == status_value)
    if from_date:
        count_stmt = count_stmt.where(SupplierPayment.payment_date >= from_date)
    if to_date:
        count_stmt = count_stmt.where(SupplierPayment.payment_date <= to_date)
    total = db.scalar(count_stmt) or 0
    return ListResponse(
        items=payments,
        count=total,
        limit=limit,
        offset=offset,
    )


@router.post("/payments/{payment_id}/post", response_model=PostingResultSchema)
def post_ap_payment(
    payment_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:payments:post")),
    db: Session = Depends(get_db_with_org),
):
    """Post an AP payment to the GL."""
    posted_by_user_id = UUID(auth["person_id"])
    result = ap_posting_adapter.post_payment(
        db=db,
        organization_id=organization_id,
        payment_id=payment_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=None,
        message=result.message,
    )
