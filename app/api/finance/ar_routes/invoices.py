"""Invoice routes for the AR API."""

from datetime import date
from uuid import UUID

from fastapi import Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.api.finance.ar_routes.base import router
from app.models.finance.ar.invoice import InvoiceStatus, InvoiceType
from app.schemas.finance.ar import ARInvoiceCreate, ARInvoiceRead
from app.schemas.finance.common import ListResponse, PostingResultSchema
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ar import (
    ARInvoiceInput,
    ARInvoiceLineInput,
    ar_invoice_service,
    ar_posting_adapter,
)


@router.post(
    "/invoices", response_model=ARInvoiceRead, status_code=status.HTTP_201_CREATED
)
def create_ar_invoice(
    payload: ARInvoiceCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ar:invoices:create")),
    db: Session = Depends(get_db_with_org),
):
    """Create a new AR invoice."""
    lines = [
        ARInvoiceLineInput(
            revenue_account_id=line.revenue_account_id,
            item_id=line.item_id,
            description=line.description,
            quantity=line.quantity,
            unit_price=line.unit_price,
            tax_code_id=line.tax_code_id,
            cost_center_id=line.cost_center_id,
            project_id=line.project_id,
        )
        for line in payload.lines
    ]
    input_data = ARInvoiceInput(
        customer_id=payload.customer_id,
        invoice_type=InvoiceType.STANDARD,
        invoice_date=payload.invoice_date,
        due_date=payload.due_date,
        currency_code=payload.currency_code,
        purpose=payload.purpose,
        notes=payload.description,
        lines=lines,
    )
    return ar_invoice_service.create_invoice(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/invoices/{invoice_id}", response_model=ARInvoiceRead)
def get_ar_invoice(
    invoice_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ar:invoices:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get an AR invoice by ID."""
    return ar_invoice_service.get(db, organization_id, str(invoice_id))


@router.get("/invoices", response_model=ListResponse[ARInvoiceRead])
def list_ar_invoices(
    organization_id: UUID = Depends(require_organization_id),
    customer_id: UUID | None = None,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ar:invoices:read")),
    db: Session = Depends(get_db_with_org),
):
    """List AR invoices with filters."""
    status_value = None
    if status:
        try:
            status_value = InvoiceStatus(status)
        except ValueError:
            status_value = None
    invoices = ar_invoice_service.list(
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
        items=invoices,
        count=len(invoices),
        limit=limit,
        offset=offset,
    )


@router.post("/invoices/{invoice_id}/post", response_model=PostingResultSchema)
def post_ar_invoice(
    invoice_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ar:invoices:post")),
    db: Session = Depends(get_db_with_org),
):
    """Post an AR invoice to the GL."""
    result = ar_posting_adapter.post_invoice(
        db=db,
        organization_id=organization_id,
        invoice_id=invoice_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=None,
        message=result.message,
    )
