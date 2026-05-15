"""Receipt routes for the AR API."""

from datetime import date
from uuid import UUID

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.api.finance.ar_routes.base import router
from app.models.finance.ar.customer_payment import PaymentStatus
from app.schemas.finance.ar import ARReceiptCreate, ARReceiptRead
from app.schemas.finance.common import ListResponse, PostingResultSchema
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ar import ar_posting_adapter, customer_payment_service


@router.post(
    "/receipts", response_model=ARReceiptRead, status_code=status.HTTP_201_CREATED
)
def create_ar_receipt(
    payload: ARReceiptCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ar:receipts:create")),
    db: Session = Depends(get_db_with_org),
):
    """Create a new AR receipt."""
    try:
        input_data = customer_payment_service.build_receipt_input(
            customer_id=payload.customer_id,
            receipt_date=payload.receipt_date,
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

    return customer_payment_service.create_payment(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/receipts/{receipt_id}", response_model=ARReceiptRead)
def get_ar_receipt(
    receipt_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ar:receipts:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get an AR receipt by ID."""
    return customer_payment_service.get(db, str(receipt_id), organization_id)


@router.get("/receipts", response_model=ListResponse[ARReceiptRead])
def list_ar_receipts(
    organization_id: UUID = Depends(require_organization_id),
    customer_id: UUID | None = None,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ar:receipts:read")),
    db: Session = Depends(get_db_with_org),
):
    """List AR receipts with filters."""
    status_value = None
    if status:
        try:
            status_value = PaymentStatus(status)
        except ValueError:
            status_value = None
    receipts = customer_payment_service.list(
        db=db,
        organization_id=str(organization_id),
        customer_id=str(customer_id) if customer_id else None,
        status=status_value,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=receipts,
        count=len(receipts),
        limit=limit,
        offset=offset,
    )


@router.post("/receipts/{receipt_id}/post", response_model=PostingResultSchema)
def post_ar_receipt(
    receipt_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ar:receipts:post")),
    db: Session = Depends(get_db_with_org),
):
    """Post an AR receipt to the GL."""
    result = ar_posting_adapter.post_payment(
        db=db,
        organization_id=organization_id,
        payment_id=receipt_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=None,
        message=result.message,
    )
