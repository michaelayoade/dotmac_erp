"""Invoice routes for the AP API."""

from datetime import date
from uuid import UUID

from fastapi import Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.api.finance.ap_routes.base import router
from app.api.finance.utils import parse_enum
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.schemas.finance.ap import APInvoiceCreate, APInvoiceRead
from app.schemas.finance.common import ListResponse, PostingResultSchema
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ap import (
    InvoiceLineInput,
    SupplierInvoiceInput,
    ap_posting_adapter,
    supplier_invoice_service,
)


@router.post(
    "/invoices", response_model=APInvoiceRead, status_code=status.HTTP_201_CREATED
)
def create_ap_invoice(
    payload: APInvoiceCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:invoices:create")),
    db: Session = Depends(get_db_with_org),
):
    """Create a new AP invoice."""
    created_by_user_id = UUID(auth["person_id"])
    lines = [
        InvoiceLineInput(
            expense_account_id=line.expense_account_id,
            description=line.description,
            quantity=line.quantity,
            unit_price=line.unit_price,
            tax_code_id=line.tax_code_id,
            cost_center_id=line.cost_center_id,
            project_id=line.project_id,
        )
        for line in payload.lines
    ]
    input_data = SupplierInvoiceInput(
        supplier_id=payload.supplier_id,
        invoice_type=parse_enum(SupplierInvoiceType, payload.invoice_type)
        or SupplierInvoiceType.STANDARD,
        supplier_invoice_number=payload.invoice_number,
        invoice_date=payload.invoice_date,
        received_date=payload.received_date or payload.invoice_date,
        due_date=payload.due_date,
        currency_code=payload.currency_code,
        purpose=payload.purpose,
        lines=lines,
    )
    return supplier_invoice_service.create_invoice(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/invoices/{invoice_id}", response_model=APInvoiceRead)
def get_ap_invoice(
    invoice_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:invoices:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get an AP invoice by ID."""
    return supplier_invoice_service.get(db, str(invoice_id), organization_id)


@router.get("/invoices", response_model=ListResponse[APInvoiceRead])
def list_ap_invoices(
    organization_id: UUID = Depends(require_organization_id),
    supplier_id: UUID | None = None,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ap:invoices:read")),
    db: Session = Depends(get_db_with_org),
):
    """List AP invoices with filters."""
    status_value = None
    if status:
        try:
            status_value = SupplierInvoiceStatus(status)
        except ValueError:
            status_value = None
    invoices = supplier_invoice_service.list(
        db=db,
        organization_id=str(organization_id),
        supplier_id=str(supplier_id) if supplier_id else None,
        status=status_value,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )
    count_stmt = select(func.count(SupplierInvoice.invoice_id)).where(
        SupplierInvoice.organization_id == organization_id
    )
    if supplier_id:
        count_stmt = count_stmt.where(SupplierInvoice.supplier_id == supplier_id)
    if status_value:
        count_stmt = count_stmt.where(SupplierInvoice.status == status_value)
    if from_date:
        count_stmt = count_stmt.where(SupplierInvoice.invoice_date >= from_date)
    if to_date:
        count_stmt = count_stmt.where(SupplierInvoice.invoice_date <= to_date)
    total = db.scalar(count_stmt) or 0
    return ListResponse(
        items=invoices,
        count=total,
        limit=limit,
        offset=offset,
    )


@router.post("/invoices/{invoice_id}/submit", response_model=APInvoiceRead)
def submit_ap_invoice(
    invoice_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:invoices:submit")),
    db: Session = Depends(get_db_with_org),
):
    """Submit an AP invoice for approval."""
    submitted_by_user_id = UUID(auth["person_id"])
    return supplier_invoice_service.submit_invoice(
        db=db,
        organization_id=organization_id,
        invoice_id=invoice_id,
        submitted_by_user_id=submitted_by_user_id,
    )


@router.post("/invoices/{invoice_id}/approve", response_model=APInvoiceRead)
def approve_ap_invoice(
    invoice_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:invoices:approve")),
    db: Session = Depends(get_db_with_org),
):
    """Approve an AP invoice."""
    approved_by_user_id = UUID(auth["person_id"])
    return supplier_invoice_service.approve_invoice(
        db=db,
        organization_id=organization_id,
        invoice_id=invoice_id,
        approved_by_user_id=approved_by_user_id,
    )


@router.post("/invoices/{invoice_id}/post", response_model=PostingResultSchema)
def post_ap_invoice(
    invoice_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:invoices:post")),
    db: Session = Depends(get_db_with_org),
):
    """Post an AP invoice to the GL."""
    posted_by_user_id = UUID(auth["person_id"])
    result = ap_posting_adapter.post_invoice(
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
