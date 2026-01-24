"""
FIN_INST API Router.

Financial Instruments API endpoints per IFRS 9.
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.services.auth_dependencies import require_tenant_permission
from app.db import SessionLocal
from app.schemas.finance.common import ListResponse, PostingResultSchema
from app.services.finance.fin_inst import (
    financial_instrument_service,
    interest_accrual_service,
    instrument_valuation_service,
    hedge_accounting_service,
    fin_inst_posting_adapter,
    InstrumentInput,
    ValuationInput,
    HedgeDesignationInput,
)


router = APIRouter(
    prefix="/fin-inst",
    tags=["financial-instruments"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Schemas
# =============================================================================

class InstrumentCreate(BaseModel):
    """Create financial instrument request."""

    instrument_code: str = Field(max_length=30)
    instrument_name: str = Field(max_length=200)
    instrument_type: str = Field(max_length=30)
    classification: str = Field(max_length=30)  # FVPL, FVOCI, AMORTIZED_COST
    acquisition_date: date
    maturity_date: Optional[date] = None
    currency_code: str = Field(max_length=3)
    face_value: Decimal
    acquisition_cost: Decimal
    stated_interest_rate: Optional[Decimal] = None
    effective_interest_rate: Optional[Decimal] = None
    counterparty_name: Optional[str] = None
    counterparty_id: Optional[UUID] = None


class InstrumentRead(BaseModel):
    """Financial instrument response."""

    model_config = ConfigDict(from_attributes=True)

    instrument_id: UUID
    organization_id: UUID
    instrument_code: str
    instrument_name: str
    instrument_type: str
    classification: str
    acquisition_date: date
    maturity_date: Optional[date]
    face_value: Decimal
    amortized_cost: Decimal
    fair_value: Decimal
    ecl_stage: int
    ecl_allowance: Decimal
    status: str


class ValuationCreate(BaseModel):
    """Create valuation request."""

    valuation_date: date
    fair_value: Decimal
    valuation_source: str = Field(max_length=30)
    fair_value_hierarchy: int = Field(ge=1, le=3)


class ValuationRead(BaseModel):
    """Valuation response."""

    model_config = ConfigDict(from_attributes=True)

    valuation_id: UUID
    instrument_id: UUID
    valuation_date: date
    fair_value: Decimal
    fair_value_change: Decimal
    fair_value_hierarchy: int
    valuation_source: str


class InterestAccrualRead(BaseModel):
    """Interest accrual response."""

    model_config = ConfigDict(from_attributes=True)

    accrual_id: UUID
    instrument_id: UUID
    accrual_date: date
    accrual_days: int
    stated_interest: Decimal
    effective_interest: Decimal
    amortization_amount: Decimal


class HedgeCreate(BaseModel):
    """Create hedge relationship request."""

    hedge_code: str = Field(max_length=30)
    hedge_type: str = Field(max_length=30)  # FAIR_VALUE, CASH_FLOW, NET_INVESTMENT
    hedged_item_id: UUID
    hedging_instrument_id: UUID
    designation_date: date
    hedged_risk: str = Field(max_length=100)
    hedge_ratio: Decimal = Decimal("1.0")


class HedgeRead(BaseModel):
    """Hedge relationship response."""

    model_config = ConfigDict(from_attributes=True)

    hedge_id: UUID
    organization_id: UUID
    hedge_code: str
    hedge_type: str
    hedged_item_id: UUID
    hedging_instrument_id: UUID
    designation_date: date
    hedged_risk: str
    hedge_ratio: Decimal
    is_effective: bool
    status: str


class EffectivenessTestRead(BaseModel):
    """Hedge effectiveness test result."""

    model_config = ConfigDict(from_attributes=True)

    test_id: UUID
    hedge_id: UUID
    test_date: date
    hedged_item_change: Decimal
    hedging_instrument_change: Decimal
    effectiveness_ratio: Decimal
    is_effective: bool
    ineffectiveness_amount: Decimal


# =============================================================================
# Financial Instruments
# =============================================================================

@router.post("/instruments", response_model=InstrumentRead, status_code=status.HTTP_201_CREATED)
def create_instrument(
    payload: InstrumentCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("fin_inst:instruments:create")),
    db: Session = Depends(get_db),
):
    """Create a new financial instrument."""
    input_data = InstrumentInput(
        instrument_code=payload.instrument_code,
        instrument_name=payload.instrument_name,
        instrument_type=payload.instrument_type,
        classification=payload.classification,
        acquisition_date=payload.acquisition_date,
        maturity_date=payload.maturity_date,
        currency_code=payload.currency_code,
        face_value=payload.face_value,
        acquisition_cost=payload.acquisition_cost,
        stated_interest_rate=payload.stated_interest_rate,
        effective_interest_rate=payload.effective_interest_rate,
        counterparty_name=payload.counterparty_name,
        counterparty_id=payload.counterparty_id,
    )
    return financial_instrument_service.create_instrument(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/instruments/{instrument_id}", response_model=InstrumentRead)
def get_instrument(
    instrument_id: UUID,
    auth: dict = Depends(require_tenant_permission("fin_inst:instruments:read")),
    db: Session = Depends(get_db),
):
    """Get a financial instrument by ID."""
    return financial_instrument_service.get(db, str(instrument_id))


@router.get("/instruments", response_model=ListResponse[InstrumentRead])
def list_instruments(
    organization_id: UUID = Depends(require_organization_id),
    instrument_type: Optional[str] = None,
    classification: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("fin_inst:instruments:read")),
    db: Session = Depends(get_db),
):
    """List financial instruments with filters."""
    instruments = financial_instrument_service.list(
        db=db,
        organization_id=str(organization_id),
        instrument_type=instrument_type,
        classification=classification,
        status=status,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=instruments,
        count=len(instruments),
        limit=limit,
        offset=offset,
    )


@router.post("/instruments/{instrument_id}/assess-ecl", response_model=InstrumentRead)
def assess_ecl_staging(
    instrument_id: UUID,
    assessment_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("fin_inst:instruments:assess")),
    db: Session = Depends(get_db),
):
    """Assess ECL staging for an instrument."""
    return financial_instrument_service.assess_ecl_staging(
        db=db,
        organization_id=organization_id,
        instrument_id=instrument_id,
        assessment_date=assessment_date,
    )


# =============================================================================
# Valuations
# =============================================================================

@router.post("/instruments/{instrument_id}/valuations", response_model=ValuationRead, status_code=status.HTTP_201_CREATED)
def create_valuation(
    instrument_id: UUID,
    payload: ValuationCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("fin_inst:valuations:create")),
    db: Session = Depends(get_db),
):
    """Create a valuation for an instrument."""
    input_data = ValuationInput(
        instrument_id=instrument_id,
        valuation_date=payload.valuation_date,
        fair_value=payload.fair_value,
        valuation_source=payload.valuation_source,
        fair_value_hierarchy=payload.fair_value_hierarchy,
    )
    return instrument_valuation_service.create_period_valuation(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/instruments/{instrument_id}/valuations", response_model=ListResponse[ValuationRead])
def list_valuations(
    instrument_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("fin_inst:valuations:read")),
    db: Session = Depends(get_db),
):
    """List valuations for an instrument."""
    valuations = instrument_valuation_service.list(
        db=db,
        organization_id=str(organization_id),
        instrument_id=str(instrument_id),
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=valuations,
        count=len(valuations),
        limit=limit,
        offset=offset,
    )


# =============================================================================
# Interest Accruals
# =============================================================================

@router.post("/instruments/{instrument_id}/accrue-interest", response_model=InterestAccrualRead)
def accrue_interest(
    instrument_id: UUID,
    accrual_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("fin_inst:accruals:create")),
    db: Session = Depends(get_db),
):
    """Calculate and record interest accrual."""
    return interest_accrual_service.create_accrual(
        db=db,
        organization_id=organization_id,
        instrument_id=instrument_id,
        accrual_date=accrual_date,
        created_by_user_id=created_by_user_id,
    )


@router.post("/instruments/{instrument_id}/post-interest", response_model=PostingResultSchema)
def post_interest_accrual(
    instrument_id: UUID,
    accrual_id: UUID = Query(...),
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("fin_inst:accruals:post")),
    db: Session = Depends(get_db),
):
    """Post interest accrual to GL."""
    result = fin_inst_posting_adapter.post_interest_accrual(
        db=db,
        organization_id=organization_id,
        instrument_id=instrument_id,
        accrual_id=accrual_id,
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
# Hedge Accounting
# =============================================================================

@router.post("/hedges", response_model=HedgeRead, status_code=status.HTTP_201_CREATED)
def designate_hedge(
    payload: HedgeCreate,
    organization_id: UUID = Depends(require_organization_id),
    designated_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("fin_inst:hedges:create")),
    db: Session = Depends(get_db),
):
    """Designate a hedge relationship."""
    input_data = HedgeDesignationInput(
        hedge_code=payload.hedge_code,
        hedge_type=payload.hedge_type,
        hedged_item_id=payload.hedged_item_id,
        hedging_instrument_id=payload.hedging_instrument_id,
        designation_date=payload.designation_date,
        hedged_risk=payload.hedged_risk,
        hedge_ratio=payload.hedge_ratio,
    )
    return hedge_accounting_service.designate_hedge(
        db=db,
        organization_id=organization_id,
        input=input_data,
        designated_by_user_id=designated_by_user_id,
    )


@router.get("/hedges/{hedge_id}", response_model=HedgeRead)
def get_hedge(
    hedge_id: UUID,
    auth: dict = Depends(require_tenant_permission("fin_inst:hedges:read")),
    db: Session = Depends(get_db),
):
    """Get a hedge relationship by ID."""
    return hedge_accounting_service.get(db, str(hedge_id))


@router.get("/hedges", response_model=ListResponse[HedgeRead])
def list_hedges(
    organization_id: UUID = Depends(require_organization_id),
    hedge_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("fin_inst:hedges:read")),
    db: Session = Depends(get_db),
):
    """List hedge relationships with filters."""
    hedges = hedge_accounting_service.list(
        db=db,
        organization_id=str(organization_id),
        hedge_type=hedge_type,
        status=status,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=hedges,
        count=len(hedges),
        limit=limit,
        offset=offset,
    )


@router.post("/hedges/{hedge_id}/test-effectiveness", response_model=EffectivenessTestRead)
def test_hedge_effectiveness(
    hedge_id: UUID,
    test_date: date = Query(...),
    hedged_item_change: Decimal = Query(...),
    hedging_instrument_change: Decimal = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    tested_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("fin_inst:hedges:test")),
    db: Session = Depends(get_db),
):
    """Perform hedge effectiveness test."""
    return hedge_accounting_service.perform_effectiveness_test(
        db=db,
        organization_id=organization_id,
        hedge_id=hedge_id,
        test_date=test_date,
        hedged_item_change=hedged_item_change,
        hedging_instrument_change=hedging_instrument_change,
        tested_by_user_id=tested_by_user_id,
    )


@router.post("/hedges/{hedge_id}/discontinue", response_model=HedgeRead)
def discontinue_hedge(
    hedge_id: UUID,
    discontinuation_date: date = Query(...),
    reason: str = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    discontinued_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("fin_inst:hedges:discontinue")),
    db: Session = Depends(get_db),
):
    """Discontinue a hedge relationship."""
    return hedge_accounting_service.discontinue_hedge(
        db=db,
        organization_id=organization_id,
        hedge_id=hedge_id,
        discontinuation_date=discontinuation_date,
        reason=reason,
        discontinued_by_user_id=discontinued_by_user_id,
    )
