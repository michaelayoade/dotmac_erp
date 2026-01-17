"""
AR API Router.

Accounts Receivable API endpoints for customers, invoices, and receipts.
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.schemas.ifrs.ar import (
    CustomerCreate,
    CustomerUpdate,
    CustomerRead,
    ARInvoiceCreate,
    ARInvoiceRead,
    ARReceiptCreate,
    ARReceiptRead,
    ARAgingReportRead,
    CreditNoteCreate,
    CreditNoteRead,
)
from app.schemas.ifrs.common import ListResponse, PostingResultSchema
from app.models.ifrs.ar.customer import CustomerType
from app.services.ifrs.ar import (
    customer_service,
    ar_invoice_service,
    customer_payment_service,
    ar_posting_adapter,
    CustomerInput,
    ARInvoiceInput,
    ARInvoiceLineInput,
    CustomerPaymentInput,
    PaymentAllocationInput,
)


router = APIRouter(prefix="/ar", tags=["accounts-receivable"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Customers
# =============================================================================

@router.post("/customers", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
def create_customer(
    payload: CustomerCreate,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new customer."""
    # Convert string customer_type to enum
    customer_type = CustomerType(payload.customer_type.upper())

    input_data = CustomerInput(
        customer_code=payload.customer_code,
        customer_type=customer_type,
        legal_name=payload.legal_name,
        trading_name=payload.trading_name,
        tax_identification_number=payload.tax_identification_number,
        credit_terms_days=payload.credit_terms_days,
        credit_limit=payload.credit_limit,
        currency_code=payload.currency_code,
        default_revenue_account_id=payload.default_revenue_account_id,
        ar_control_account_id=payload.ar_control_account_id,
    )
    return customer_service.create_customer(db, organization_id, input_data)


@router.get("/customers/{customer_id}", response_model=CustomerRead)
def get_customer(
    customer_id: UUID,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Get a customer by ID."""
    return customer_service.get(db, organization_id, str(customer_id))


@router.get("/customers", response_model=ListResponse[CustomerRead])
def list_customers(
    organization_id: UUID = Query(...),
    is_active: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
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
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Update a customer (partial update)."""
    # Fetch existing customer
    existing = customer_service.get(db, organization_id, str(customer_id))

    # Build input with existing values, overriding with provided values
    input_data = CustomerInput(
        customer_code=existing.customer_code,
        customer_type=existing.customer_type,
        legal_name=payload.legal_name if payload.legal_name is not None else existing.legal_name,
        trading_name=payload.trading_name if payload.trading_name is not None else existing.trading_name,
        tax_identification_number=existing.tax_identification_number,
        credit_terms_days=payload.credit_terms_days if payload.credit_terms_days is not None else existing.credit_terms_days,
        credit_limit=payload.credit_limit if payload.credit_limit is not None else existing.credit_limit,
        currency_code=existing.currency_code,
        default_revenue_account_id=existing.default_revenue_account_id,
        ar_control_account_id=existing.ar_control_account_id,
    )

    result = customer_service.update_customer(
        db=db,
        organization_id=organization_id,
        customer_id=customer_id,
        input=input_data,
    )

    # Handle is_active separately if provided
    if payload.is_active is not None and payload.is_active != result.is_active:
        if payload.is_active:
            result = customer_service.activate_customer(db, organization_id, customer_id)
        else:
            result = customer_service.deactivate_customer(db, organization_id, customer_id)

    return result


# =============================================================================
# AR Invoices
# =============================================================================

@router.post("/invoices", response_model=ARInvoiceRead, status_code=status.HTTP_201_CREATED)
def create_ar_invoice(
    payload: ARInvoiceCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new AR invoice."""
    lines = [
        ARInvoiceLineInput(
            revenue_account_id=line.revenue_account_id,
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
        invoice_date=payload.invoice_date,
        due_date=payload.due_date,
        currency_code=payload.currency_code,
        description=payload.description,
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
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Get an AR invoice by ID."""
    return ar_invoice_service.get(db, organization_id, str(invoice_id))


@router.get("/invoices", response_model=ListResponse[ARInvoiceRead])
def list_ar_invoices(
    organization_id: UUID = Query(...),
    customer_id: Optional[UUID] = None,
    status: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List AR invoices with filters."""
    invoices = ar_invoice_service.list(
        db=db,
        organization_id=str(organization_id),
        customer_id=str(customer_id) if customer_id else None,
        status=status,
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
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
    fiscal_period_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Post an AR invoice to the GL."""
    result = ar_posting_adapter.post_invoice(
        db=db,
        organization_id=organization_id,
        invoice_id=invoice_id,
        fiscal_period_id=fiscal_period_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=result.entry_number,
        message=result.message,
    )


# =============================================================================
# AR Receipts
# =============================================================================

@router.post("/receipts", response_model=ARReceiptRead, status_code=status.HTTP_201_CREATED)
def create_ar_receipt(
    payload: ARReceiptCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
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

    input_data = CustomerPaymentInput(
        customer_id=payload.customer_id,
        receipt_date=payload.receipt_date,
        payment_method=payload.payment_method,
        bank_account_id=payload.bank_account_id,
        currency_code=payload.currency_code,
        reference_number=payload.reference_number,
        allocations=allocations,
    )

    return customer_payment_service.create_receipt(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/receipts/{receipt_id}", response_model=ARReceiptRead)
def get_ar_receipt(
    receipt_id: UUID,
    db: Session = Depends(get_db),
):
    """Get an AR receipt by ID."""
    return customer_payment_service.get(db, str(receipt_id))


@router.get("/receipts", response_model=ListResponse[ARReceiptRead])
def list_ar_receipts(
    organization_id: UUID = Query(...),
    customer_id: Optional[UUID] = None,
    status: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List AR receipts with filters."""
    receipts = customer_payment_service.list(
        db=db,
        organization_id=str(organization_id),
        customer_id=str(customer_id) if customer_id else None,
        status=status,
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
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
    fiscal_period_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Post an AR receipt to the GL."""
    result = ar_posting_adapter.post_receipt(
        db=db,
        organization_id=organization_id,
        receipt_id=receipt_id,
        fiscal_period_id=fiscal_period_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=result.entry_number,
        message=result.message,
    )


# =============================================================================
# AR Aging
# =============================================================================

@router.get("/aging", response_model=ARAgingReportRead)
def get_ar_aging(
    organization_id: UUID = Query(...),
    as_of_date: date = Query(...),
    customer_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    """Get AR aging report."""
    return ar_invoice_service.get_aging_report(
        db=db,
        organization_id=str(organization_id),
        as_of_date=as_of_date,
        customer_id=str(customer_id) if customer_id else None,
    )


# =============================================================================
# Credit Memos
# =============================================================================

@router.post("/credit-notes", response_model=CreditNoteRead, status_code=status.HTTP_201_CREATED)
def create_credit_note(
    payload: CreditNoteCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
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

    return ar_invoice_service.create_credit_note(
        db=db,
        organization_id=organization_id,
        customer_id=payload.customer_id,
        original_invoice_id=payload.original_invoice_id,
        credit_date=payload.credit_date,
        reason=payload.reason,
        lines=lines,
        created_by_user_id=created_by_user_id,
    )


# =============================================================================
# IFRS 15 Contracts
# =============================================================================

from pydantic import BaseModel, ConfigDict, Field
from decimal import Decimal
from app.services.ifrs.ar import (
    contract_service,
    ContractInput,
    PerformanceObligationInput,
    ProgressUpdateInput,
)


class PerformanceObligationCreate(BaseModel):
    """Performance obligation input."""
    description: str
    standalone_price: Decimal
    recognition_method: str = "OVER_TIME"  # OVER_TIME or POINT_IN_TIME
    measure_type: Optional[str] = "OUTPUT"  # INPUT or OUTPUT
    total_units: Optional[Decimal] = None


class ContractCreate(BaseModel):
    """Create IFRS 15 contract request."""
    customer_id: UUID
    contract_number: str = Field(max_length=50)
    contract_date: date
    start_date: date
    end_date: date
    total_transaction_price: Decimal
    currency_code: str = Field(max_length=3)
    description: Optional[str] = None
    performance_obligations: list[PerformanceObligationCreate] = []


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
    units_delivered: Optional[Decimal] = None
    percentage_complete: Optional[Decimal] = None


class RevenueEventRead(BaseModel):
    """Revenue recognition event response."""
    model_config = ConfigDict(from_attributes=True)
    event_id: UUID
    obligation_id: UUID
    event_date: date
    revenue_amount: Decimal
    event_type: str


@router.post("/contracts", response_model=ContractRead, status_code=status.HTTP_201_CREATED)
def create_contract(
    payload: ContractCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create an IFRS 15 revenue contract."""
    input_data = ContractInput(
        customer_id=payload.customer_id,
        contract_number=payload.contract_number,
        contract_date=payload.contract_date,
        start_date=payload.start_date,
        end_date=payload.end_date,
        total_transaction_price=payload.total_transaction_price,
        currency_code=payload.currency_code,
        description=payload.description,
    )
    return contract_service.create_contract(db, organization_id, input_data, created_by_user_id)


@router.get("/contracts/{contract_id}", response_model=ContractRead)
def get_contract(contract_id: UUID, db: Session = Depends(get_db)):
    """Get a contract by ID."""
    return contract_service.get(db, str(contract_id))


@router.get("/contracts", response_model=ListResponse[ContractRead])
def list_contracts(
    organization_id: UUID = Query(...),
    customer_id: Optional[UUID] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List IFRS 15 contracts with filters."""
    contracts = contract_service.list(
        db=db,
        organization_id=str(organization_id),
        customer_id=str(customer_id) if customer_id else None,
        status=status,
        limit=limit,
        offset=offset,
    )
    return ListResponse(items=contracts, count=len(contracts), limit=limit, offset=offset)


@router.post("/contracts/{contract_id}/activate", response_model=ContractRead)
def activate_contract(
    contract_id: UUID,
    organization_id: UUID = Query(...),
    approved_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Activate an IFRS 15 contract."""
    return contract_service.activate_contract(db, organization_id, contract_id, approved_by_user_id)


@router.post("/contracts/{contract_id}/obligations", response_model=ContractRead)
def add_performance_obligation(
    contract_id: UUID,
    payload: PerformanceObligationCreate,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Add a performance obligation to a contract."""
    input_data = PerformanceObligationInput(
        description=payload.description,
        standalone_price=payload.standalone_price,
        recognition_method=payload.recognition_method,
        measure_type=payload.measure_type,
        total_units=payload.total_units,
    )
    contract_service.add_performance_obligation(db, organization_id, contract_id, input_data)
    return contract_service.get(db, str(contract_id))


@router.post("/contracts/update-progress", response_model=RevenueEventRead)
def update_contract_progress(
    payload: ProgressUpdateCreate,
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Update progress and recognize revenue."""
    input_data = ProgressUpdateInput(
        obligation_id=payload.obligation_id,
        update_date=payload.update_date,
        fiscal_period_id=payload.fiscal_period_id,
        measure_type=payload.measure_type,
        units_delivered=payload.units_delivered,
        percentage_complete=payload.percentage_complete,
    )
    return contract_service.update_progress(db, organization_id, input_data, posted_by_user_id)


# =============================================================================
# IFRS 9 Expected Credit Loss (ECL)
# =============================================================================

from app.services.ifrs.ar import ecl_service, ECLCalculationInput, GeneralApproachInput


class SimplifiedECLCreate(BaseModel):
    """Simplified ECL calculation input."""
    as_of_date: date
    fiscal_period_id: UUID
    customer_id: Optional[UUID] = None


class GeneralECLCreate(BaseModel):
    """General ECL calculation input."""
    customer_id: UUID
    fiscal_period_id: UUID
    as_of_date: date
    principal_outstanding: Decimal
    current_stage: int = 1
    days_past_due: int = 0
    probability_of_default: Optional[Decimal] = None
    loss_given_default: Optional[Decimal] = None
    exposure_at_default: Optional[Decimal] = None


class ECLResultRead(BaseModel):
    """ECL calculation result."""
    model_config = ConfigDict(from_attributes=True)
    customer_id: Optional[UUID]
    gross_carrying_amount: Decimal
    expected_credit_loss: Decimal
    net_carrying_amount: Decimal
    ecl_rate: Decimal


class ECLSummaryRead(BaseModel):
    """ECL provision summary."""
    as_of_date: date
    total_receivables: str
    total_provision: str
    coverage_ratio: str
    customer_count: int


@router.post("/ecl/calculate-simplified", response_model=ECLResultRead)
def calculate_simplified_ecl(
    payload: SimplifiedECLCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Calculate simplified ECL using provision matrix."""
    input_data = ECLCalculationInput(
        as_of_date=payload.as_of_date,
        fiscal_period_id=payload.fiscal_period_id,
        customer_id=payload.customer_id,
    )
    return ecl_service.calculate_simplified(db, organization_id, input_data, created_by_user_id)


@router.post("/ecl/calculate-general")
def calculate_general_ecl(
    payload: GeneralECLCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Calculate general ECL using 3-stage model."""
    input_data = GeneralApproachInput(
        customer_id=payload.customer_id,
        fiscal_period_id=payload.fiscal_period_id,
        as_of_date=payload.as_of_date,
        principal_outstanding=payload.principal_outstanding,
        current_stage=payload.current_stage,
        days_past_due=payload.days_past_due,
        probability_of_default=payload.probability_of_default,
        loss_given_default=payload.loss_given_default,
        exposure_at_default=payload.exposure_at_default,
    )
    return ecl_service.calculate_general(db, organization_id, input_data, created_by_user_id)


@router.get("/ecl/summary", response_model=ECLSummaryRead)
def get_ecl_summary(
    organization_id: UUID = Query(...),
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """Get ECL provision summary."""
    return ecl_service.get_provision_summary(db, organization_id, as_of_date)
