"""
TAX API Router.

Tax Management API endpoints per IAS 12.
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.schemas.ifrs.common import ListResponse, PostingResultSchema
from app.services.ifrs.tax import (
    tax_code_service,
    tax_jurisdiction_service,
    tax_transaction_service,
    deferred_tax_service,
    tax_reconciliation_service,
    tax_posting_adapter,
    TaxCodeInput,
    TaxJurisdictionInput,
    TaxTransactionInput,
    DeferredTaxBasisInput,
    TaxReconciliationInput,
    ReconciliationLine,
)


router = APIRouter(prefix="/tax", tags=["tax"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Schemas
# =============================================================================

class TaxJurisdictionCreate(BaseModel):
    """Create tax jurisdiction request."""

    jurisdiction_code: str = Field(max_length=20)
    jurisdiction_name: str = Field(max_length=100)
    country_code: str = Field(max_length=3)
    tax_authority_name: Optional[str] = None
    currency_code: str = Field(max_length=3)
    corporate_tax_rate: Decimal = Decimal("0")
    effective_date: date


class TaxJurisdictionRead(BaseModel):
    """Tax jurisdiction response."""

    model_config = ConfigDict(from_attributes=True)

    jurisdiction_id: UUID
    organization_id: UUID
    jurisdiction_code: str
    jurisdiction_name: str
    country_code: str
    tax_authority_name: Optional[str]
    corporate_tax_rate: Decimal
    is_active: bool


class TaxCodeCreate(BaseModel):
    """Create tax code request."""

    tax_code: str = Field(max_length=20)
    tax_name: str = Field(max_length=100)
    tax_type: str = Field(max_length=30)  # VAT, GST, WHT, INCOME
    jurisdiction_id: UUID
    rate: Decimal
    effective_date: date
    end_date: Optional[date] = None
    tax_account_id: Optional[UUID] = None
    is_recoverable: bool = True


class TaxCodeRead(BaseModel):
    """Tax code response."""

    model_config = ConfigDict(from_attributes=True)

    tax_code_id: UUID
    organization_id: UUID
    tax_code: str
    tax_name: str
    tax_type: str
    jurisdiction_id: UUID
    rate: Decimal
    effective_date: date
    end_date: Optional[date]
    is_recoverable: bool
    is_active: bool


class TaxCalculationRead(BaseModel):
    """Tax calculation result."""

    tax_code_id: UUID
    tax_code: str
    tax_name: str
    rate: Decimal
    base_amount: Decimal
    tax_amount: Decimal
    is_recoverable: bool


class TaxTransactionCreate(BaseModel):
    """Create tax transaction request."""

    tax_code_id: UUID
    transaction_date: date
    base_amount: Decimal
    tax_amount: Decimal
    source_document_type: str = Field(max_length=30)
    source_document_id: UUID
    is_input_tax: bool = True
    description: Optional[str] = None


class TaxTransactionRead(BaseModel):
    """Tax transaction response."""

    model_config = ConfigDict(from_attributes=True)

    transaction_id: UUID
    organization_id: UUID
    tax_code_id: UUID
    transaction_date: date
    base_amount: Decimal
    tax_amount: Decimal
    source_document_type: str
    is_input_tax: bool
    is_posted: bool


class TaxReturnSummaryRead(BaseModel):
    """Tax return summary."""

    jurisdiction_id: UUID
    period_start: date
    period_end: date
    total_output_tax: Decimal
    total_input_tax: Decimal
    net_tax_payable: Decimal
    transaction_count: int


class DeferredTaxBasisCreate(BaseModel):
    """Create deferred tax basis request."""

    asset_liability_type: str = Field(max_length=30)
    item_description: str = Field(max_length=200)
    reference_id: Optional[UUID] = None
    reference_type: Optional[str] = None
    accounting_basis: Decimal
    tax_basis: Decimal
    applicable_tax_rate: Decimal


class DeferredTaxBasisRead(BaseModel):
    """Deferred tax basis response."""

    model_config = ConfigDict(from_attributes=True)

    basis_id: UUID
    organization_id: UUID
    asset_liability_type: str
    item_description: str
    accounting_basis: Decimal
    tax_basis: Decimal
    temporary_difference: Decimal
    deferred_tax_asset: Decimal
    deferred_tax_liability: Decimal


class DeferredTaxSummaryRead(BaseModel):
    """Deferred tax summary."""

    as_of_date: date
    total_dta: Decimal
    total_dtl: Decimal
    net_position: Decimal
    item_count: int


class TaxReconciliationCreate(BaseModel):
    """Create tax reconciliation request."""

    fiscal_year_id: UUID
    accounting_profit: Decimal
    lines: list["ReconciliationLineCreate"]


class ReconciliationLineCreate(BaseModel):
    """Reconciliation line input."""

    line_type: str = Field(max_length=30)
    description: str = Field(max_length=200)
    amount: Decimal
    is_permanent: bool = False


class TaxReconciliationRead(BaseModel):
    """Tax reconciliation response."""

    model_config = ConfigDict(from_attributes=True)

    reconciliation_id: UUID
    organization_id: UUID
    fiscal_year_id: UUID
    accounting_profit: Decimal
    taxable_income: Decimal
    current_tax_expense: Decimal
    effective_tax_rate: Decimal


# =============================================================================
# Tax Jurisdictions
# =============================================================================

@router.post("/jurisdictions", response_model=TaxJurisdictionRead, status_code=status.HTTP_201_CREATED)
def create_jurisdiction(
    payload: TaxJurisdictionCreate,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new tax jurisdiction."""
    input_data = TaxJurisdictionInput(
        jurisdiction_code=payload.jurisdiction_code,
        jurisdiction_name=payload.jurisdiction_name,
        country_code=payload.country_code,
        tax_authority_name=payload.tax_authority_name,
        currency_code=payload.currency_code,
        corporate_tax_rate=payload.corporate_tax_rate,
        effective_date=payload.effective_date,
    )
    return tax_jurisdiction_service.create_jurisdiction(db, organization_id, input_data)


@router.get("/jurisdictions/{jurisdiction_id}", response_model=TaxJurisdictionRead)
def get_jurisdiction(
    jurisdiction_id: UUID,
    db: Session = Depends(get_db),
):
    """Get a tax jurisdiction by ID."""
    return tax_jurisdiction_service.get(db, str(jurisdiction_id))


@router.get("/jurisdictions", response_model=ListResponse[TaxJurisdictionRead])
def list_jurisdictions(
    organization_id: UUID = Query(...),
    country_code: Optional[str] = None,
    is_active: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List tax jurisdictions with filters."""
    jurisdictions = tax_jurisdiction_service.list(
        db=db,
        organization_id=str(organization_id),
        country_code=country_code,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=jurisdictions,
        count=len(jurisdictions),
        limit=limit,
        offset=offset,
    )


# =============================================================================
# Tax Codes
# =============================================================================

@router.post("/codes", response_model=TaxCodeRead, status_code=status.HTTP_201_CREATED)
def create_tax_code(
    payload: TaxCodeCreate,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new tax code."""
    input_data = TaxCodeInput(
        tax_code=payload.tax_code,
        tax_name=payload.tax_name,
        tax_type=payload.tax_type,
        jurisdiction_id=payload.jurisdiction_id,
        rate=payload.rate,
        effective_date=payload.effective_date,
        end_date=payload.end_date,
        tax_account_id=payload.tax_account_id,
        is_recoverable=payload.is_recoverable,
    )
    return tax_code_service.create_tax_code(db, organization_id, input_data)


@router.get("/codes/{tax_code_id}", response_model=TaxCodeRead)
def get_tax_code(
    tax_code_id: UUID,
    db: Session = Depends(get_db),
):
    """Get a tax code by ID."""
    return tax_code_service.get(db, str(tax_code_id))


@router.get("/codes", response_model=ListResponse[TaxCodeRead])
def list_tax_codes(
    organization_id: UUID = Query(...),
    tax_type: Optional[str] = None,
    jurisdiction_id: Optional[UUID] = None,
    is_active: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List tax codes with filters."""
    codes = tax_code_service.list(
        db=db,
        organization_id=str(organization_id),
        tax_type=tax_type,
        jurisdiction_id=str(jurisdiction_id) if jurisdiction_id else None,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=codes,
        count=len(codes),
        limit=limit,
        offset=offset,
    )


@router.post("/codes/{tax_code_id}/calculate", response_model=TaxCalculationRead)
def calculate_tax(
    tax_code_id: UUID,
    base_amount: Decimal = Query(...),
    transaction_date: date = Query(...),
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Calculate tax for a given amount."""
    return tax_code_service.calculate_tax(
        db=db,
        organization_id=organization_id,
        tax_code_id=tax_code_id,
        base_amount=base_amount,
        transaction_date=transaction_date,
    )


# =============================================================================
# Tax Transactions
# =============================================================================

@router.post("/transactions", response_model=TaxTransactionRead, status_code=status.HTTP_201_CREATED)
def create_tax_transaction(
    payload: TaxTransactionCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a tax transaction."""
    input_data = TaxTransactionInput(
        tax_code_id=payload.tax_code_id,
        transaction_date=payload.transaction_date,
        base_amount=payload.base_amount,
        tax_amount=payload.tax_amount,
        source_document_type=payload.source_document_type,
        source_document_id=payload.source_document_id,
        is_input_tax=payload.is_input_tax,
        description=payload.description,
    )
    return tax_transaction_service.create_transaction(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/transactions", response_model=ListResponse[TaxTransactionRead])
def list_tax_transactions(
    organization_id: UUID = Query(...),
    tax_code_id: Optional[UUID] = None,
    tax_type: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    is_input_tax: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List tax transactions with filters."""
    transactions = tax_transaction_service.list(
        db=db,
        organization_id=str(organization_id),
        tax_code_id=str(tax_code_id) if tax_code_id else None,
        tax_type=tax_type,
        start_date=start_date,
        end_date=end_date,
        is_input_tax=is_input_tax,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=transactions,
        count=len(transactions),
        limit=limit,
        offset=offset,
    )


@router.get("/return-summary", response_model=TaxReturnSummaryRead)
def get_tax_return_summary(
    organization_id: UUID = Query(...),
    jurisdiction_id: UUID = Query(...),
    period_start: date = Query(...),
    period_end: date = Query(...),
    db: Session = Depends(get_db),
):
    """Get tax return summary for a period."""
    return tax_transaction_service.get_return_summary(
        db=db,
        organization_id=str(organization_id),
        jurisdiction_id=str(jurisdiction_id),
        period_start=period_start,
        period_end=period_end,
    )


# =============================================================================
# Deferred Taxes (IAS 12)
# =============================================================================

@router.post("/deferred/basis", response_model=DeferredTaxBasisRead, status_code=status.HTTP_201_CREATED)
def create_deferred_tax_basis(
    payload: DeferredTaxBasisCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a deferred tax basis record."""
    input_data = DeferredTaxBasisInput(
        asset_liability_type=payload.asset_liability_type,
        item_description=payload.item_description,
        reference_id=payload.reference_id,
        reference_type=payload.reference_type,
        accounting_basis=payload.accounting_basis,
        tax_basis=payload.tax_basis,
        applicable_tax_rate=payload.applicable_tax_rate,
    )
    return deferred_tax_service.create_basis(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/deferred/basis", response_model=ListResponse[DeferredTaxBasisRead])
def list_deferred_tax_basis(
    organization_id: UUID = Query(...),
    asset_liability_type: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List deferred tax basis records."""
    items = deferred_tax_service.list(
        db=db,
        organization_id=str(organization_id),
        asset_liability_type=asset_liability_type,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=items,
        count=len(items),
        limit=limit,
        offset=offset,
    )


@router.post("/deferred/calculate", response_model=DeferredTaxSummaryRead)
def calculate_deferred_taxes(
    organization_id: UUID = Query(...),
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """Calculate deferred tax assets and liabilities."""
    return deferred_tax_service.calculate_deferred_taxes(
        db=db,
        organization_id=str(organization_id),
        as_of_date=as_of_date,
    )


@router.get("/deferred/summary", response_model=DeferredTaxSummaryRead)
def get_deferred_tax_summary(
    organization_id: UUID = Query(...),
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """Get deferred tax summary."""
    return deferred_tax_service.get_summary(
        db=db,
        organization_id=str(organization_id),
        as_of_date=as_of_date,
    )


# =============================================================================
# Tax Reconciliation
# =============================================================================

@router.post("/reconciliation", response_model=TaxReconciliationRead, status_code=status.HTTP_201_CREATED)
def create_tax_reconciliation(
    payload: TaxReconciliationCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a tax reconciliation."""
    lines = [
        ReconciliationLine(
            line_type=line.line_type,
            description=line.description,
            amount=line.amount,
            is_permanent=line.is_permanent,
        )
        for line in payload.lines
    ]
    input_data = TaxReconciliationInput(
        fiscal_year_id=payload.fiscal_year_id,
        accounting_profit=payload.accounting_profit,
        lines=lines,
    )
    return tax_reconciliation_service.create_reconciliation(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/reconciliation/{fiscal_year_id}", response_model=TaxReconciliationRead)
def get_tax_reconciliation(
    fiscal_year_id: UUID,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Get tax reconciliation for a fiscal year."""
    return tax_reconciliation_service.get_by_fiscal_year(
        db=db,
        organization_id=str(organization_id),
        fiscal_year_id=str(fiscal_year_id),
    )


# =============================================================================
# Tax Postings
# =============================================================================

@router.post("/transactions/{transaction_id}/post", response_model=PostingResultSchema)
def post_tax_transaction(
    transaction_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Post tax transaction to GL."""
    result = tax_posting_adapter.post_tax_transaction(
        db=db,
        organization_id=organization_id,
        transaction_id=transaction_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=result.entry_number,
        message=result.message,
    )


@router.post("/deferred/post", response_model=PostingResultSchema)
def post_deferred_tax_movement(
    organization_id: UUID = Query(...),
    fiscal_period_id: UUID = Query(...),
    posting_date: date = Query(...),
    posted_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Post deferred tax movement to GL."""
    result = tax_posting_adapter.post_deferred_tax_movement(
        db=db,
        organization_id=organization_id,
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
# Tax Periods
# =============================================================================

from app.services.ifrs.tax import tax_period_service, TaxPeriodInput


class TaxPeriodCreate(BaseModel):
    """Create tax period request."""
    jurisdiction_id: UUID
    tax_type: str = Field(max_length=30)
    period_start: date
    period_end: date
    due_date: date
    description: Optional[str] = None


class TaxPeriodRead(BaseModel):
    """Tax period response."""
    model_config = ConfigDict(from_attributes=True)
    period_id: UUID
    organization_id: UUID
    jurisdiction_id: UUID
    tax_type: str
    period_start: date
    period_end: date
    due_date: date
    status: str
    is_extended: bool


@router.post("/periods", response_model=TaxPeriodRead, status_code=status.HTTP_201_CREATED)
def create_tax_period(
    payload: TaxPeriodCreate,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new tax period."""
    input_data = TaxPeriodInput(
        jurisdiction_id=payload.jurisdiction_id,
        tax_type=payload.tax_type,
        period_start=payload.period_start,
        period_end=payload.period_end,
        due_date=payload.due_date,
        description=payload.description,
    )
    return tax_period_service.create_period(db, organization_id, input_data)


@router.get("/periods/{period_id}", response_model=TaxPeriodRead)
def get_tax_period(period_id: UUID, db: Session = Depends(get_db)):
    """Get a tax period by ID."""
    return tax_period_service.get(db, str(period_id))


@router.get("/periods", response_model=ListResponse[TaxPeriodRead])
def list_tax_periods(
    organization_id: UUID = Query(...),
    jurisdiction_id: Optional[UUID] = None,
    tax_type: Optional[str] = None,
    status: Optional[str] = None,
    year: Optional[int] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List tax periods with filters."""
    periods = tax_period_service.list(
        db=db,
        organization_id=str(organization_id),
        jurisdiction_id=str(jurisdiction_id) if jurisdiction_id else None,
        tax_type=tax_type,
        status=status,
        year=year,
        limit=limit,
        offset=offset,
    )
    return ListResponse(items=periods, count=len(periods), limit=limit, offset=offset)


@router.post("/periods/generate", response_model=ListResponse[TaxPeriodRead])
def generate_tax_periods(
    organization_id: UUID = Query(...),
    jurisdiction_id: UUID = Query(...),
    year: int = Query(...),
    frequency: str = Query(default="MONTHLY"),
    due_date_offset_days: int = Query(default=30),
    db: Session = Depends(get_db),
):
    """Auto-generate tax periods for a year."""
    periods = tax_period_service.generate_periods(
        db, organization_id, jurisdiction_id, year, frequency, due_date_offset_days
    )
    return ListResponse(items=periods, count=len(periods), limit=len(periods), offset=0)


@router.post("/periods/{period_id}/extend", response_model=TaxPeriodRead)
def extend_tax_period(
    period_id: UUID,
    extended_due_date: date = Query(...),
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """File an extension for a tax period."""
    return tax_period_service.file_extension(db, organization_id, period_id, extended_due_date)


@router.get("/periods/overdue", response_model=ListResponse[TaxPeriodRead])
def get_overdue_tax_periods(
    organization_id: UUID = Query(...),
    as_of_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Get overdue tax periods."""
    periods = tax_period_service.get_overdue_periods(db, organization_id, as_of_date)
    return ListResponse(items=periods, count=len(periods), limit=len(periods), offset=0)


# =============================================================================
# Tax Returns
# =============================================================================

from app.services.ifrs.tax import tax_return_service, TaxReturnInput


class TaxReturnCreate(BaseModel):
    """Create tax return request."""
    period_id: UUID
    return_type: str = Field(max_length=30)
    gross_revenue: Decimal = Decimal("0")
    taxable_income: Decimal = Decimal("0")
    tax_credits: Decimal = Decimal("0")
    withholding_paid: Decimal = Decimal("0")
    notes: Optional[str] = None


class TaxReturnRead(BaseModel):
    """Tax return response."""
    model_config = ConfigDict(from_attributes=True)
    return_id: UUID
    organization_id: UUID
    period_id: UUID
    return_type: str
    status: str
    gross_revenue: Decimal
    taxable_income: Decimal
    tax_liability: Decimal
    tax_due: Decimal
    is_amended: bool


@router.post("/returns", response_model=TaxReturnRead, status_code=status.HTTP_201_CREATED)
def prepare_tax_return(
    payload: TaxReturnCreate,
    organization_id: UUID = Query(...),
    prepared_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Prepare a new tax return."""
    input_data = TaxReturnInput(
        period_id=payload.period_id,
        return_type=payload.return_type,
        gross_revenue=payload.gross_revenue,
        taxable_income=payload.taxable_income,
        tax_credits=payload.tax_credits,
        withholding_paid=payload.withholding_paid,
        notes=payload.notes,
    )
    return tax_return_service.prepare_return(db, organization_id, input_data, prepared_by_user_id)


@router.get("/returns/{return_id}", response_model=TaxReturnRead)
def get_tax_return(return_id: UUID, db: Session = Depends(get_db)):
    """Get a tax return by ID."""
    return tax_return_service.get(db, str(return_id))


@router.get("/returns", response_model=ListResponse[TaxReturnRead])
def list_tax_returns(
    organization_id: UUID = Query(...),
    period_id: Optional[UUID] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List tax returns with filters."""
    returns = tax_return_service.list(
        db=db,
        organization_id=str(organization_id),
        period_id=str(period_id) if period_id else None,
        status=status,
        limit=limit,
        offset=offset,
    )
    return ListResponse(items=returns, count=len(returns), limit=limit, offset=offset)


@router.post("/returns/{return_id}/review", response_model=TaxReturnRead)
def review_tax_return(
    return_id: UUID,
    organization_id: UUID = Query(...),
    reviewed_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Review and approve a tax return."""
    return tax_return_service.review_return(db, organization_id, return_id, reviewed_by_user_id)


@router.post("/returns/{return_id}/file", response_model=TaxReturnRead)
def file_tax_return(
    return_id: UUID,
    filing_reference: Optional[str] = None,
    organization_id: UUID = Query(...),
    filed_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """File a tax return with the authority."""
    return tax_return_service.file_return(db, organization_id, return_id, filed_by_user_id, filing_reference)


@router.post("/returns/{return_id}/record-payment", response_model=TaxReturnRead)
def record_return_payment(
    return_id: UUID,
    payment_date: date = Query(...),
    payment_amount: Decimal = Query(...),
    payment_reference: Optional[str] = None,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Record a tax payment for a return."""
    return tax_return_service.record_payment(
        db, organization_id, return_id, payment_date, payment_amount, payment_reference
    )


@router.post("/returns/{return_id}/amend", response_model=TaxReturnRead)
def amend_tax_return(
    return_id: UUID,
    amendment_reason: str = Query(...),
    taxable_income: Optional[Decimal] = None,
    tax_credits: Optional[Decimal] = None,
    organization_id: UUID = Query(...),
    prepared_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create an amended tax return."""
    return tax_return_service.create_amendment(
        db, organization_id, return_id, amendment_reason,
        taxable_income, tax_credits, prepared_by_user_id
    )
