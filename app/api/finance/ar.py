"""
AR API Router.

Accounts Receivable API endpoints for customers, invoices, and receipts.
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.config import settings
from app.db import SessionLocal
from app.models.finance.ar.contract import ContractStatus, ContractType
from app.models.finance.ar.customer import CustomerType
from app.models.finance.ar.customer_payment import PaymentMethod, PaymentStatus
from app.models.finance.ar.invoice import InvoiceStatus, InvoiceType
from app.models.finance.ar.performance_obligation import SatisfactionPattern
from app.schemas.finance.ar import (
    ARAgingReportRead,
    ARInvoiceCreate,
    ARInvoiceRead,
    ARReceiptCreate,
    ARReceiptRead,
    CreditNoteCreate,
    CreditNoteRead,
    CustomerCreate,
    CustomerRead,
    CustomerUpdate,
)
from app.schemas.finance.common import ListResponse, PostingResultSchema
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ar import (
    ARInvoiceInput,
    ARInvoiceLineInput,
    CustomerInput,
    CustomerPaymentInput,
    PaymentAllocationInput,
    ar_aging_service,
    ar_invoice_service,
    ar_posting_adapter,
    customer_payment_service,
    customer_service,
)

router = APIRouter(
    prefix="/ar",
    tags=["accounts-receivable"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Customers
# =============================================================================


@router.post(
    "/customers", response_model=CustomerRead, status_code=status.HTTP_201_CREATED
)
def create_customer(
    payload: CustomerCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ar:customers:create")),
    db: Session = Depends(get_db),
):
    """Create a new customer."""
    # Thin wrapper: pass template-friendly names directly to service
    # Service handles field mapping internally
    input_data = CustomerInput(
        customer_code=payload.customer_code,
        customer_type=CustomerType(payload.customer_type.upper()),
        customer_name=payload.customer_name,  # Template-friendly name
        trading_name=payload.trading_name,
        tax_id=payload.tax_id,  # Template-friendly name
        vat_category=payload.vat_category,
        payment_terms_days=payload.payment_terms_days,  # Template-friendly name
        credit_limit=payload.credit_limit,
        currency_code=settings.default_functional_currency_code,
        default_revenue_account_id=payload.default_revenue_account_id,
        default_receivable_account_id=payload.default_receivable_account_id,  # Template-friendly name
        default_tax_code_id=payload.default_tax_code_id,
    )
    return customer_service.create_customer(db, organization_id, input_data)


@router.get("/customers/{customer_id}", response_model=CustomerRead)
def get_customer(
    customer_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ar:customers:read")),
    db: Session = Depends(get_db),
):
    """Get a customer by ID."""
    return customer_service.get(db, organization_id, str(customer_id))


@router.get("/customers", response_model=ListResponse[CustomerRead])
def list_customers(
    organization_id: UUID = Depends(require_organization_id),
    is_active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ar:customers:read")),
    db: Session = Depends(get_db),
):
    """List customers with filters."""
    customers = customer_service.list(
        db=db,
        organization_id=str(organization_id),
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=customers,
        count=len(customers),
        limit=limit,
        offset=offset,
    )


@router.patch("/customers/{customer_id}", response_model=CustomerRead)
def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ar:customers:update")),
    db: Session = Depends(get_db),
):
    """Update a customer (partial update)."""
    # Convert payload to dict, excluding unset fields
    update_data = payload.model_dump(exclude_unset=True)
    return customer_service.partial_update_customer(
        db=db,
        organization_id=organization_id,
        customer_id=customer_id,
        update_data=update_data,
    )


# =============================================================================
# AR Invoices
# =============================================================================


@router.post(
    "/invoices", response_model=ARInvoiceRead, status_code=status.HTTP_201_CREATED
)
def create_ar_invoice(
    payload: ARInvoiceCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ar:invoices:create")),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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


# =============================================================================
# AR Receipts
# =============================================================================


@router.post(
    "/receipts", response_model=ARReceiptRead, status_code=status.HTTP_201_CREATED
)
def create_ar_receipt(
    payload: ARReceiptCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ar:receipts:create")),
    db: Session = Depends(get_db),
):
    """Create a new AR receipt."""
    allocations = [
        PaymentAllocationInput(
            invoice_id=alloc.invoice_id,
            amount=alloc.amount,
        )
        for alloc in payload.allocations
    ]
    total_amount = sum((alloc.amount for alloc in allocations), Decimal("0"))
    try:
        payment_method = PaymentMethod(payload.payment_method)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid payment method") from exc

    input_data = CustomerPaymentInput(
        customer_id=payload.customer_id,
        payment_date=payload.receipt_date,
        payment_method=payment_method,
        bank_account_id=payload.bank_account_id,
        currency_code=payload.currency_code,
        amount=total_amount,
        reference=payload.reference_number,
        allocations=allocations,
    )

    return customer_payment_service.create_payment(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/receipts/{receipt_id}", response_model=ARReceiptRead)
def get_ar_receipt(
    receipt_id: UUID,
    auth: dict = Depends(require_tenant_permission("ar:receipts:read")),
    db: Session = Depends(get_db),
):
    """Get an AR receipt by ID."""
    return customer_payment_service.get(db, str(receipt_id))


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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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


# =============================================================================
# AR Aging
# =============================================================================


@router.get("/aging", response_model=ARAgingReportRead)
def get_ar_aging(
    organization_id: UUID = Depends(require_organization_id),
    as_of_date: date = Query(...),
    customer_id: UUID | None = None,
    auth: dict = Depends(require_tenant_permission("ar:aging:read")),
    db: Session = Depends(get_db),
):
    """Get AR aging report."""
    org_summary = ar_aging_service.calculate_organization_aging(
        db=db,
        organization_id=organization_id,
        as_of_date=as_of_date,
    )

    if customer_id:
        customer_summaries = [
            ar_aging_service.calculate_customer_aging(
                db=db,
                organization_id=organization_id,
                customer_id=customer_id,
                as_of_date=as_of_date,
            )
        ]
    else:
        customer_summaries = ar_aging_service.get_aging_by_customer(
            db=db,
            organization_id=organization_id,
            as_of_date=as_of_date,
        )

    buckets = [
        {
            "customer_id": summary.customer_id,
            "customer_code": summary.customer_code,
            "customer_name": summary.customer_name,
            "current": summary.current,
            "days_1_30": summary.current,
            "days_31_60": summary.days_31_60,
            "days_61_90": summary.days_61_90,
            "over_90": summary.over_90,
            "total": summary.total_outstanding,
        }
        for summary in customer_summaries
    ]

    totals = {
        "customer_id": UUID(int=0),
        "customer_code": "TOTAL",
        "customer_name": "Total",
        "current": org_summary.current,
        "days_1_30": org_summary.current,
        "days_31_60": org_summary.days_31_60,
        "days_61_90": org_summary.days_61_90,
        "over_90": org_summary.over_90,
        "total": org_summary.total_outstanding,
    }

    return {
        "as_of_date": org_summary.as_of_date,
        "currency_code": org_summary.currency_code,
        "buckets": buckets,
        "totals": totals,
    }


# =============================================================================
# Credit Memos
# =============================================================================


@router.post(
    "/credit-notes", response_model=CreditNoteRead, status_code=status.HTTP_201_CREATED
)
def create_credit_note(
    payload: CreditNoteCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ar:credit_notes:create")),
    db: Session = Depends(get_db),
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


# =============================================================================
# IFRS 15 Contracts
# =============================================================================

from decimal import Decimal  # noqa: E402

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from app.services.finance.ar import (  # noqa: E402
    ContractInput,
    PerformanceObligationInput,
    ProgressUpdateInput,
    contract_service,
)


class PerformanceObligationCreate(BaseModel):
    """Performance obligation input."""

    description: str
    standalone_price: Decimal
    recognition_method: str = "OVER_TIME"  # OVER_TIME or POINT_IN_TIME
    measure_type: str | None = "OUTPUT"  # INPUT or OUTPUT
    total_units: Decimal | None = None
    revenue_account_id: UUID
    ssp_determination_method: str = "STANDALONE"
    is_distinct: bool = True
    over_time_method: str | None = None
    progress_measure: str | None = None
    expected_completion_date: date | None = None
    contract_asset_account_id: UUID | None = None
    contract_liability_account_id: UUID | None = None


class ContractCreate(BaseModel):
    """Create IFRS 15 contract request."""

    customer_id: UUID
    contract_number: str = Field(max_length=50)
    contract_date: date
    start_date: date
    end_date: date
    total_transaction_price: Decimal
    currency_code: str = Field(max_length=3)
    description: str | None = None
    performance_obligations: list[PerformanceObligationCreate] = []
    contract_type: str = "STANDARD"


class ContractRead(BaseModel):
    """IFRS 15 contract response."""

    model_config = ConfigDict(from_attributes=True)
    contract_id: UUID
    organization_id: UUID
    customer_id: UUID
    contract_number: str
    contract_date: date
    status: str
    total_transaction_price: Decimal
    recognized_revenue: Decimal
    deferred_revenue: Decimal


class ProgressUpdateCreate(BaseModel):
    """Progress update input."""

    obligation_id: UUID
    update_date: date
    fiscal_period_id: UUID
    measure_type: str = "OUTPUT"
    units_delivered: Decimal | None = None
    percentage_complete: Decimal | None = None


class RevenueEventRead(BaseModel):
    """Revenue recognition event response."""

    model_config = ConfigDict(from_attributes=True)
    event_id: UUID
    obligation_id: UUID
    event_date: date
    revenue_amount: Decimal
    event_type: str


@router.post(
    "/contracts", response_model=ContractRead, status_code=status.HTTP_201_CREATED
)
def create_contract(
    payload: ContractCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ar:contracts:create")),
    db: Session = Depends(get_db),
):
    """Create an IFRS 15 revenue contract."""
    obligations = []
    for obligation in payload.performance_obligations:
        try:
            pattern = SatisfactionPattern(obligation.recognition_method)
        except ValueError:
            pattern = SatisfactionPattern.OVER_TIME
        obligations.append(
            PerformanceObligationInput(
                description=obligation.description,
                satisfaction_pattern=pattern,
                standalone_selling_price=obligation.standalone_price,
                ssp_determination_method=obligation.ssp_determination_method,
                revenue_account_id=obligation.revenue_account_id,
                is_distinct=obligation.is_distinct,
                over_time_method=obligation.over_time_method,
                progress_measure=obligation.progress_measure or obligation.measure_type,
                expected_completion_date=obligation.expected_completion_date,
                contract_asset_account_id=obligation.contract_asset_account_id,
                contract_liability_account_id=obligation.contract_liability_account_id,
            )
        )
    input_data = ContractInput(
        customer_id=payload.customer_id,
        contract_name=payload.contract_number,
        contract_type=ContractType(payload.contract_type),
        start_date=payload.start_date,
        end_date=payload.end_date,
        currency_code=payload.currency_code,
        total_contract_value=payload.total_transaction_price,
        obligations=obligations,
    )
    return contract_service.create_contract(
        db, organization_id, input_data, created_by_user_id
    )


@router.get("/contracts/{contract_id}", response_model=ContractRead)
def get_contract(
    contract_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ar:contracts:read")),
    db: Session = Depends(get_db),
):
    """Get a contract by ID."""
    return contract_service.get(db, str(contract_id), organization_id)


@router.get("/contracts", response_model=ListResponse[ContractRead])
def list_contracts(
    organization_id: UUID = Depends(require_organization_id),
    customer_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ar:contracts:read")),
    db: Session = Depends(get_db),
):
    """List IFRS 15 contracts with filters."""
    status_value = None
    if status:
        try:
            status_value = ContractStatus(status)
        except ValueError:
            status_value = None
    contracts = contract_service.list(
        db=db,
        organization_id=str(organization_id),
        customer_id=str(customer_id) if customer_id else None,
        status=status_value,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=contracts, count=len(contracts), limit=limit, offset=offset
    )


@router.post("/contracts/{contract_id}/activate", response_model=ContractRead)
def activate_contract(
    contract_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    approved_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ar:contracts:approve")),
    db: Session = Depends(get_db),
):
    """Activate an IFRS 15 contract."""
    return contract_service.activate_contract(
        db, organization_id, contract_id, approved_by_user_id
    )


@router.post("/contracts/{contract_id}/obligations", response_model=ContractRead)
def add_performance_obligation(
    contract_id: UUID,
    payload: PerformanceObligationCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ar:contracts:update")),
    db: Session = Depends(get_db),
):
    """Add a performance obligation to a contract."""
    try:
        pattern = SatisfactionPattern(payload.recognition_method)
    except ValueError:
        pattern = SatisfactionPattern.OVER_TIME
    input_data = PerformanceObligationInput(
        description=payload.description,
        satisfaction_pattern=pattern,
        standalone_selling_price=payload.standalone_price,
        ssp_determination_method=payload.ssp_determination_method,
        revenue_account_id=payload.revenue_account_id,
        is_distinct=payload.is_distinct,
        over_time_method=payload.over_time_method,
        progress_measure=payload.progress_measure or payload.measure_type,
        expected_completion_date=payload.expected_completion_date,
        contract_asset_account_id=payload.contract_asset_account_id,
        contract_liability_account_id=payload.contract_liability_account_id,
    )
    contract_service.add_performance_obligation(
        db, organization_id, contract_id, input_data
    )
    return contract_service.get(db, str(contract_id), organization_id)


@router.post("/contracts/update-progress", response_model=RevenueEventRead)
def update_contract_progress(
    payload: ProgressUpdateCreate,
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ar:contracts:post")),
    db: Session = Depends(get_db),
):
    """Update progress and recognize revenue."""
    input_data = ProgressUpdateInput(
        obligation_id=payload.obligation_id,
        event_date=payload.update_date,
        progress_percentage=payload.percentage_complete or Decimal("0"),
        measurement_details={
            "measure_type": payload.measure_type,
            "units_delivered": str(payload.units_delivered)
            if payload.units_delivered
            else None,
            "fiscal_period_id": str(payload.fiscal_period_id),
        },
    )
    return contract_service.update_progress(
        db, organization_id, input_data, posted_by_user_id
    )
