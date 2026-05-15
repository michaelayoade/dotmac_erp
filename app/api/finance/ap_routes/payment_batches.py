"""Payment batch routes for the AP API."""

from uuid import UUID

from fastapi import Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.api.finance.ap_routes.base import router
from app.models.finance.ap.payment_batch import APBatchStatus, APPaymentBatch
from app.schemas.finance.ap import (
    BankFileResultRead,
    PaymentBatchCreate,
    PaymentBatchRead,
)
from app.schemas.finance.common import ListResponse
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ap import payment_batch_service


@router.post(
    "/payment-batches",
    response_model=PaymentBatchRead,
    status_code=status.HTTP_201_CREATED,
)
def create_payment_batch(
    payload: PaymentBatchCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:create")),
    db: Session = Depends(get_db_with_org),
):
    """Create a new payment batch."""
    return payment_batch_service.create_batch_from_invoice_ids(
        db=db,
        organization_id=organization_id,
        batch_date=payload.batch_date,
        payment_method=payload.payment_method,
        bank_account_id=payload.bank_account_id,
        invoice_ids=payload.invoice_ids,
        created_by_user_id=UUID(auth["person_id"]),
        currency_code=payload.currency_code,
    )


@router.get("/payment-batches/{batch_id}", response_model=PaymentBatchRead)
def get_payment_batch(
    batch_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get a payment batch by ID."""
    return payment_batch_service.get(db, str(batch_id), organization_id)


@router.get("/payment-batches", response_model=ListResponse[PaymentBatchRead])
def list_payment_batches(
    organization_id: UUID = Depends(require_organization_id),
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:read")),
    db: Session = Depends(get_db_with_org),
):
    """List payment batches with filters."""
    status_value = None
    if status:
        try:
            status_value = APBatchStatus(status)
        except ValueError:
            status_value = None
    batches = payment_batch_service.list(
        db=db,
        organization_id=str(organization_id),
        status=status_value,
        limit=limit,
        offset=offset,
    )
    count_stmt = select(func.count(APPaymentBatch.batch_id)).where(
        APPaymentBatch.organization_id == organization_id
    )
    if status_value:
        count_stmt = count_stmt.where(APPaymentBatch.status == status_value)
    total = db.scalar(count_stmt) or 0
    return ListResponse(
        items=batches,
        count=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/payment-batches/{batch_id}/add-payment/{payment_id}",
    response_model=PaymentBatchRead,
)
def add_payment_to_batch(
    batch_id: UUID,
    payment_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:update")),
    db: Session = Depends(get_db_with_org),
):
    """Add a payment to a batch."""
    return payment_batch_service.add_payment_to_batch(
        db, organization_id, batch_id, payment_id
    )


@router.post("/payment-batches/{batch_id}/approve", response_model=PaymentBatchRead)
def approve_payment_batch(
    batch_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:approve")),
    db: Session = Depends(get_db_with_org),
):
    """Approve a payment batch."""
    approved_by_user_id = UUID(auth["person_id"])
    return payment_batch_service.approve_batch(
        db, organization_id, batch_id, approved_by_user_id
    )


@router.post("/payment-batches/{batch_id}/process", response_model=PaymentBatchRead)
def process_payment_batch(
    batch_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:process")),
    db: Session = Depends(get_db_with_org),
):
    """Process an approved payment batch."""
    processed_by_user_id = UUID(auth["person_id"])
    return payment_batch_service.process_batch(
        db, organization_id, batch_id, processed_by_user_id
    )


@router.post(
    "/payment-batches/{batch_id}/generate-bank-file", response_model=BankFileResultRead
)
def generate_bank_file(
    batch_id: UUID,
    file_format: str = Query(default="NACHA"),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:export")),
    db: Session = Depends(get_db_with_org),
):
    """Generate bank file for a payment batch."""
    result = payment_batch_service.generate_bank_file(
        db, organization_id, batch_id, file_format
    )
    return BankFileResultRead(
        success=True,
        file_format=result["file_format"],
        file_content=result["content"],
        payment_count=result["payment_count"],
        total_amount=result["total_amount"],
    )
