"""Credit note routes for the AR API."""

from uuid import UUID

from fastapi import Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.api.finance.ar_routes.base import router
from app.config import settings
from app.models.finance.ar.invoice import InvoiceType
from app.schemas.finance.ar import CreditNoteCreate, CreditNoteRead
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ar import (
    ARInvoiceInput,
    ARInvoiceLineInput,
    ar_invoice_service,
)


@router.post(
    "/credit-notes", response_model=CreditNoteRead, status_code=status.HTTP_201_CREATED
)
def create_credit_note(
    payload: CreditNoteCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ar:credit_notes:create")),
    db: Session = Depends(get_db_with_org),
):
    """Create a credit note."""
    lines = [
        ARInvoiceLineInput(
            revenue_account_id=line.revenue_account_id,
            description=line.description,
            quantity=line.quantity,
            unit_price=line.unit_price,
            tax_code_id=line.tax_code_id,
        )
        for line in payload.lines
    ]
    input_data = ARInvoiceInput(
        customer_id=payload.customer_id,
        invoice_type=InvoiceType.CREDIT_NOTE,
        invoice_date=payload.credit_date,
        due_date=payload.credit_date,
        currency_code=settings.default_functional_currency_code,
        lines=lines,
        notes=payload.reason,
        correlation_id=str(payload.original_invoice_id)
        if payload.original_invoice_id
        else None,
    )
    return ar_invoice_service.create_invoice(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )
