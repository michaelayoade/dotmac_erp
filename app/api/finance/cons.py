"""
CONS API Router.

Consolidation API endpoints per IFRS 10.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.services.auth_dependencies import require_tenant_permission
from app.db import SessionLocal
from app.schemas.finance.common import ListResponse, PostingResultSchema
from app.models.finance.cons.consolidation_run import ConsolidationStatus
from app.models.finance.cons.elimination_entry import EliminationEntry, EliminationType
from app.models.finance.cons.legal_entity import ConsolidationMethod, EntityType
from app.services.finance.cons import (
    legal_entity_service,
    ownership_service,
    intercompany_service,
    consolidation_service,
    cons_posting_adapter,
    LegalEntityInput,
    OwnershipInput,
    IntercompanyBalanceInput,
    ConsolidationRunInput,
    EliminationInput,
)


router = APIRouter(
    prefix="/cons",
    tags=["consolidation"],
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


class LegalEntityCreate(BaseModel):
    """Create legal entity request."""

    entity_code: str = Field(max_length=20)
    entity_name: str = Field(max_length=200)
    legal_name: str = Field(max_length=300)
    entity_type: EntityType
    consolidation_method: ConsolidationMethod = ConsolidationMethod.FULL
    country_code: str = Field(max_length=3)
    functional_currency_code: str = Field(max_length=3)
    reporting_currency_code: str = Field(max_length=3)
    parent_entity_id: Optional[UUID] = None
    organization_id: Optional[UUID] = None
    is_consolidating_entity: bool = False
    description: Optional[str] = None
    incorporation_date: Optional[date] = None
    registration_number: Optional[str] = None
    tax_id: Optional[str] = None
    fiscal_year_end_month: int = 12
    fiscal_year_end_day: int = 31
    acquisition_date: Optional[date] = None
    acquisition_cost: Optional[Decimal] = None
    goodwill_at_acquisition: Optional[Decimal] = None
    address: Optional[dict] = None


class LegalEntityRead(BaseModel):
    """Legal entity response."""

    model_config = ConfigDict(from_attributes=True)

    entity_id: UUID
    group_id: UUID
    organization_id: Optional[UUID] = None
    entity_code: str
    entity_name: str
    legal_name: str
    description: Optional[str] = None
    entity_type: EntityType
    consolidation_method: ConsolidationMethod
    parent_entity_id: Optional[UUID] = None
    is_consolidating_entity: bool
    country_code: str
    functional_currency_code: str
    reporting_currency_code: str
    fiscal_year_end_month: int
    fiscal_year_end_day: int
    acquisition_date: Optional[date] = None
    disposal_date: Optional[date] = None
    acquisition_cost: Optional[Decimal] = None
    goodwill_at_acquisition: Optional[Decimal] = None
    accumulated_goodwill_impairment: Decimal
    is_active: bool


class GroupStructureRead(BaseModel):
    """Group structure response."""

    model_config = ConfigDict(from_attributes=True)

    entity: LegalEntityRead
    children: list["GroupStructureRead"] = Field(default_factory=list)
    level: int


class OwnershipCreate(BaseModel):
    """Create ownership interest request."""

    investor_entity_id: UUID
    investee_entity_id: UUID
    ownership_percentage: Decimal
    voting_rights_percentage: Decimal
    effective_from: date
    shares_held: Optional[Decimal] = None
    total_shares_outstanding: Optional[Decimal] = None
    investment_cost: Optional[Decimal] = None
    nci_at_acquisition: Optional[Decimal] = None
    nci_measurement_basis: Optional[str] = None


class OwnershipRead(BaseModel):
    """Ownership interest response."""

    model_config = ConfigDict(from_attributes=True)

    interest_id: UUID
    investor_entity_id: UUID
    investee_entity_id: UUID
    ownership_percentage: Decimal
    voting_rights_percentage: Decimal
    effective_ownership_percentage: Decimal
    effective_from: date
    effective_to: Optional[date] = None
    shares_held: Optional[Decimal] = None
    total_shares_outstanding: Optional[Decimal] = None
    investment_cost: Optional[Decimal] = None
    nci_percentage: Decimal
    nci_at_acquisition: Optional[Decimal] = None
    nci_measurement_basis: Optional[str] = None
    has_control: bool
    has_significant_influence: bool
    has_joint_control: bool
    is_current: bool


class NCISummaryRead(BaseModel):
    """NCI summary response."""

    model_config = ConfigDict(from_attributes=True)

    entity_id: UUID
    entity_name: str
    nci_percentage: Decimal
    nci_at_acquisition: Optional[Decimal] = None
    has_control: bool


class IntercompanyBalanceCreate(BaseModel):
    """Create intercompany balance request."""

    fiscal_period_id: UUID
    balance_date: date
    from_entity_id: UUID
    to_entity_id: UUID
    balance_type: str = Field(max_length=50)
    from_entity_gl_account_id: UUID
    from_entity_currency: str = Field(max_length=3)
    from_entity_amount: Decimal
    from_entity_functional_amount: Decimal
    to_entity_gl_account_id: UUID
    to_entity_currency: str = Field(max_length=3)
    to_entity_amount: Decimal
    to_entity_functional_amount: Decimal
    reporting_currency_code: str = Field(max_length=3)
    reporting_currency_amount: Decimal
    balance_description: Optional[str] = None


class IntercompanyBalanceRead(BaseModel):
    """Intercompany balance response."""

    model_config = ConfigDict(from_attributes=True)

    balance_id: UUID
    fiscal_period_id: UUID
    balance_date: date
    from_entity_id: UUID
    to_entity_id: UUID
    balance_type: str
    balance_description: Optional[str] = None
    from_entity_gl_account_id: UUID
    from_entity_currency: str
    from_entity_amount: Decimal
    from_entity_functional_amount: Decimal
    to_entity_gl_account_id: UUID
    to_entity_currency: str
    to_entity_amount: Decimal
    to_entity_functional_amount: Decimal
    reporting_currency_code: str
    reporting_currency_amount: Decimal
    is_matched: bool
    difference_amount: Decimal
    difference_reason: Optional[str] = None
    is_eliminated: bool
    elimination_entry_id: Optional[UUID] = None


class MatchingResultRead(BaseModel):
    """Intercompany matching result."""

    model_config = ConfigDict(from_attributes=True)

    balance_id: UUID
    from_entity_code: str
    to_entity_code: str
    balance_type: str
    from_amount: Decimal
    to_amount: Decimal
    difference: Decimal
    is_matched: bool
    difference_reason: Optional[str] = None


class ConsolidationRunCreate(BaseModel):
    """Create consolidation run request."""

    fiscal_period_id: UUID
    reporting_currency_code: str = Field(max_length=3)
    run_description: Optional[str] = None


class ConsolidationRunRead(BaseModel):
    """Consolidation run response."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    group_id: UUID
    fiscal_period_id: UUID
    run_number: int
    run_description: Optional[str] = None
    reporting_currency_code: str
    status: ConsolidationStatus
    entities_count: int
    subsidiaries_count: int
    associates_count: int
    elimination_entries_count: int
    total_eliminations_amount: Decimal
    intercompany_differences: Decimal
    total_translation_adjustment: Decimal
    total_nci: Decimal
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    created_by_user_id: UUID
    approved_by_user_id: Optional[UUID] = None


class EliminationEntryCreate(BaseModel):
    """Create elimination entry request."""

    elimination_type: EliminationType
    description: str = Field(max_length=200)
    currency_code: str = Field(max_length=3)
    debit_account_id: UUID
    debit_amount: Decimal
    credit_account_id: UUID
    credit_amount: Decimal
    entity_1_id: Optional[UUID] = None
    entity_2_id: Optional[UUID] = None
    source_balance_id: Optional[UUID] = None
    nci_debit_account_id: Optional[UUID] = None
    nci_debit_amount: Decimal = Decimal("0")
    nci_credit_account_id: Optional[UUID] = None
    nci_credit_amount: Decimal = Decimal("0")
    is_automatic: bool = True


class EliminationEntryRead(BaseModel):
    """Elimination entry response."""

    model_config = ConfigDict(from_attributes=True)

    entry_id: UUID
    consolidation_run_id: UUID
    elimination_type: EliminationType
    description: str
    currency_code: str
    debit_account_id: UUID
    debit_amount: Decimal
    credit_account_id: UUID
    credit_amount: Decimal
    entity_1_id: Optional[UUID] = None
    entity_2_id: Optional[UUID] = None
    source_balance_id: Optional[UUID] = None
    nci_debit_account_id: Optional[UUID] = None
    nci_debit_amount: Decimal
    nci_credit_account_id: Optional[UUID] = None
    nci_credit_amount: Decimal
    is_automatic: bool


class ConsolidationSummaryRead(BaseModel):
    """Consolidation summary."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    status: ConsolidationStatus
    entities_count: int
    elimination_count: int
    total_eliminations: Decimal
    total_translation_adjustment: Decimal
    total_nci: Decimal
    intercompany_differences: Decimal


# =============================================================================
# Legal Entities
# =============================================================================


@router.post(
    "/entities", response_model=LegalEntityRead, status_code=status.HTTP_201_CREATED
)
def create_legal_entity(
    payload: LegalEntityCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("cons:entities:create")),
    db: Session = Depends(get_db),
):
    """Create a new legal entity."""
    input_data = LegalEntityInput(
        entity_code=payload.entity_code,
        entity_name=payload.entity_name,
        legal_name=payload.legal_name,
        entity_type=payload.entity_type,
        consolidation_method=payload.consolidation_method,
        country_code=payload.country_code,
        functional_currency_code=payload.functional_currency_code,
        reporting_currency_code=payload.reporting_currency_code,
        parent_entity_id=payload.parent_entity_id,
        organization_id=payload.organization_id or organization_id,
        is_consolidating_entity=payload.is_consolidating_entity,
        description=payload.description,
        incorporation_date=payload.incorporation_date,
        registration_number=payload.registration_number,
        tax_id=payload.tax_id,
        fiscal_year_end_month=payload.fiscal_year_end_month,
        fiscal_year_end_day=payload.fiscal_year_end_day,
        acquisition_date=payload.acquisition_date,
        acquisition_cost=payload.acquisition_cost,
        goodwill_at_acquisition=payload.goodwill_at_acquisition,
        address=payload.address,
    )
    return legal_entity_service.create_entity(
        db=db,
        group_id=organization_id,
        input=input_data,
    )


@router.get("/entities/{entity_id}", response_model=LegalEntityRead)
def get_legal_entity(
    entity_id: UUID,
    auth: dict = Depends(require_tenant_permission("cons:entities:read")),
    db: Session = Depends(get_db),
):
    """Get a legal entity by ID."""
    return legal_entity_service.get(db, str(entity_id))


@router.get("/entities", response_model=ListResponse[LegalEntityRead])
def list_legal_entities(
    organization_id: UUID = Depends(require_organization_id),
    entity_type: Optional[EntityType] = None,
    consolidation_method: Optional[ConsolidationMethod] = None,
    is_active: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("cons:entities:read")),
    db: Session = Depends(get_db),
):
    """List legal entities with filters."""
    entities = legal_entity_service.list(
        db=db,
        group_id=str(organization_id),
        entity_type=entity_type,
        consolidation_method=consolidation_method,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=entities,
        count=len(entities),
        limit=limit,
        offset=offset,
    )


@router.get("/structure", response_model=list[GroupStructureRead])
def get_group_structure(
    organization_id: UUID = Depends(require_organization_id),
    as_of_date: Optional[date] = None,
    auth: dict = Depends(require_tenant_permission("cons:entities:read")),
    db: Session = Depends(get_db),
):
    """Get group structure hierarchy."""
    return legal_entity_service.get_group_structure(
        db=db,
        group_id=organization_id,
        as_of_date=as_of_date,
    )


@router.post(
    "/entities/{entity_id}/update-consolidation-method", response_model=LegalEntityRead
)
def update_consolidation_method(
    entity_id: UUID,
    consolidation_method: ConsolidationMethod = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("cons:entities:update")),
    db: Session = Depends(get_db),
):
    """Update entity consolidation method."""
    return legal_entity_service.update_consolidation_method(
        db=db,
        group_id=organization_id,
        entity_id=entity_id,
        new_method=consolidation_method,
    )


@router.post(
    "/entities/{entity_id}/record-goodwill-impairment", response_model=LegalEntityRead
)
def record_goodwill_impairment(
    entity_id: UUID,
    impairment_amount: Decimal = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("cons:entities:update")),
    db: Session = Depends(get_db),
):
    """Record goodwill impairment for entity."""
    return legal_entity_service.record_goodwill_impairment(
        db=db,
        group_id=organization_id,
        entity_id=entity_id,
        impairment_amount=impairment_amount,
    )


# =============================================================================
# Ownership Interests
# =============================================================================


@router.post(
    "/ownership", response_model=OwnershipRead, status_code=status.HTTP_201_CREATED
)
def create_ownership(
    payload: OwnershipCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("cons:ownership:create")),
    db: Session = Depends(get_db),
):
    """Create a new ownership interest."""
    input_data = OwnershipInput(
        investor_entity_id=payload.investor_entity_id,
        investee_entity_id=payload.investee_entity_id,
        ownership_percentage=payload.ownership_percentage,
        voting_rights_percentage=payload.voting_rights_percentage,
        effective_from=payload.effective_from,
        shares_held=payload.shares_held,
        total_shares_outstanding=payload.total_shares_outstanding,
        investment_cost=payload.investment_cost,
        nci_at_acquisition=payload.nci_at_acquisition,
        nci_measurement_basis=payload.nci_measurement_basis,
    )
    return ownership_service.create_ownership(
        db=db,
        group_id=organization_id,
        input=input_data,
    )


@router.get("/ownership", response_model=ListResponse[OwnershipRead])
def list_ownership_interests(
    organization_id: UUID = Depends(require_organization_id),
    investor_entity_id: Optional[UUID] = None,
    investee_entity_id: Optional[UUID] = None,
    is_current: Optional[bool] = None,
    has_control: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("cons:ownership:read")),
    db: Session = Depends(get_db),
):
    """List ownership interests with filters."""
    interests = ownership_service.list(
        db=db,
        investor_entity_id=str(investor_entity_id) if investor_entity_id else None,
        investee_entity_id=str(investee_entity_id) if investee_entity_id else None,
        is_current=is_current,
        has_control=has_control,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=interests,
        count=len(interests),
        limit=limit,
        offset=offset,
    )


@router.get("/ownership/effective/{entity_id}")
def get_effective_ownership(
    entity_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    as_of_date: date = Query(...),
    auth: dict = Depends(require_tenant_permission("cons:ownership:read")),
    db: Session = Depends(get_db),
):
    """Calculate effective ownership for an entity."""
    results = ownership_service.calculate_effective_ownership(
        db=db,
        group_id=organization_id,
        as_of_date=as_of_date,
    )
    for result in results:
        if result.entity_id == entity_id:
            return result
    raise HTTPException(status_code=404, detail="Entity not found")


@router.get("/nci-summary", response_model=list[NCISummaryRead])
def get_nci_summary(
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("cons:ownership:read")),
    db: Session = Depends(get_db),
):
    """Get NCI summary for all subsidiaries."""
    return ownership_service.get_nci_summary(
        db=db,
        group_id=organization_id,
    )


# =============================================================================
# Intercompany Balances
# =============================================================================


@router.post(
    "/intercompany",
    response_model=IntercompanyBalanceRead,
    status_code=status.HTTP_201_CREATED,
)
def create_intercompany_balance(
    payload: IntercompanyBalanceCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("cons:intercompany:create")),
    db: Session = Depends(get_db),
):
    """Record an intercompany balance."""
    input_data = IntercompanyBalanceInput(
        fiscal_period_id=payload.fiscal_period_id,
        balance_date=payload.balance_date,
        from_entity_id=payload.from_entity_id,
        to_entity_id=payload.to_entity_id,
        balance_type=payload.balance_type,
        from_entity_gl_account_id=payload.from_entity_gl_account_id,
        from_entity_currency=payload.from_entity_currency,
        from_entity_amount=payload.from_entity_amount,
        from_entity_functional_amount=payload.from_entity_functional_amount,
        to_entity_gl_account_id=payload.to_entity_gl_account_id,
        to_entity_currency=payload.to_entity_currency,
        to_entity_amount=payload.to_entity_amount,
        to_entity_functional_amount=payload.to_entity_functional_amount,
        reporting_currency_code=payload.reporting_currency_code,
        reporting_currency_amount=payload.reporting_currency_amount,
        balance_description=payload.balance_description,
    )
    return intercompany_service.record_balance(
        db=db,
        group_id=organization_id,
        input=input_data,
    )


@router.get("/intercompany", response_model=ListResponse[IntercompanyBalanceRead])
def list_intercompany_balances(
    organization_id: UUID = Depends(require_organization_id),
    fiscal_period_id: Optional[UUID] = None,
    from_entity_id: Optional[UUID] = None,
    to_entity_id: Optional[UUID] = None,
    balance_type: Optional[str] = None,
    is_matched: Optional[bool] = None,
    is_eliminated: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("cons:intercompany:read")),
    db: Session = Depends(get_db),
):
    """List intercompany balances with filters."""
    balances = intercompany_service.list(
        db=db,
        fiscal_period_id=str(fiscal_period_id) if fiscal_period_id else None,
        from_entity_id=str(from_entity_id) if from_entity_id else None,
        to_entity_id=str(to_entity_id) if to_entity_id else None,
        balance_type=balance_type,
        is_matched=is_matched,
        is_eliminated=is_eliminated,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=balances,
        count=len(balances),
        limit=limit,
        offset=offset,
    )


@router.post("/intercompany/match", response_model=list[MatchingResultRead])
def perform_intercompany_matching(
    organization_id: UUID = Depends(require_organization_id),
    fiscal_period_id: UUID = Query(...),
    tolerance: Decimal = Query(default=Decimal("0.01")),
    auth: dict = Depends(require_tenant_permission("cons:intercompany:match")),
    db: Session = Depends(get_db),
):
    """Perform intercompany balance matching."""
    return intercompany_service.perform_matching(
        db=db,
        group_id=organization_id,
        fiscal_period_id=fiscal_period_id,
        tolerance=tolerance,
    )


# =============================================================================
# Consolidation Runs
# =============================================================================


@router.post(
    "/runs", response_model=ConsolidationRunRead, status_code=status.HTTP_201_CREATED
)
def create_consolidation_run(
    payload: ConsolidationRunCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("cons:runs:create")),
    db: Session = Depends(get_db),
):
    """Create a new consolidation run."""
    input_data = ConsolidationRunInput(
        fiscal_period_id=payload.fiscal_period_id,
        reporting_currency_code=payload.reporting_currency_code,
        run_description=payload.run_description,
    )
    return consolidation_service.create_run(
        db=db,
        group_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/runs/{run_id}", response_model=ConsolidationRunRead)
def get_consolidation_run(
    run_id: UUID,
    auth: dict = Depends(require_tenant_permission("cons:runs:read")),
    db: Session = Depends(get_db),
):
    """Get a consolidation run by ID."""
    return consolidation_service.get(db, str(run_id))


@router.get("/runs", response_model=ListResponse[ConsolidationRunRead])
def list_consolidation_runs(
    organization_id: UUID = Depends(require_organization_id),
    fiscal_period_id: Optional[UUID] = None,
    status: Optional[ConsolidationStatus] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("cons:runs:read")),
    db: Session = Depends(get_db),
):
    """List consolidation runs with filters."""
    runs = consolidation_service.list(
        db=db,
        group_id=str(organization_id),
        fiscal_period_id=str(fiscal_period_id) if fiscal_period_id else None,
        status=status,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=runs,
        count=len(runs),
        limit=limit,
        offset=offset,
    )


@router.post("/runs/{run_id}/start", response_model=ConsolidationRunRead)
def start_consolidation_run(
    run_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("cons:runs:manage")),
    db: Session = Depends(get_db),
):
    """Start a consolidation run."""
    return consolidation_service.start_run(
        db=db,
        group_id=organization_id,
        run_id=run_id,
    )


@router.post("/runs/{run_id}/complete", response_model=ConsolidationRunRead)
def complete_consolidation_run(
    run_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("cons:runs:manage")),
    db: Session = Depends(get_db),
):
    """Complete a consolidation run."""
    return consolidation_service.complete_run(
        db=db,
        group_id=organization_id,
        run_id=run_id,
    )


@router.get("/runs/{run_id}/summary", response_model=ConsolidationSummaryRead)
def get_consolidation_summary(
    run_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("cons:runs:read")),
    db: Session = Depends(get_db),
):
    """Get consolidation run summary."""
    return consolidation_service.get_summary(
        db=db,
        group_id=organization_id,
        run_id=run_id,
    )


# =============================================================================
# Elimination Entries
# =============================================================================


@router.post(
    "/runs/{run_id}/eliminations",
    response_model=EliminationEntryRead,
    status_code=status.HTTP_201_CREATED,
)
def create_elimination_entry(
    run_id: UUID,
    payload: EliminationEntryCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("cons:eliminations:create")),
    db: Session = Depends(get_db),
):
    """Create an elimination entry."""
    input_data = EliminationInput(
        elimination_type=payload.elimination_type,
        description=payload.description,
        currency_code=payload.currency_code,
        debit_account_id=payload.debit_account_id,
        debit_amount=payload.debit_amount,
        credit_account_id=payload.credit_account_id,
        credit_amount=payload.credit_amount,
        entity_1_id=payload.entity_1_id,
        entity_2_id=payload.entity_2_id,
        source_balance_id=payload.source_balance_id,
        nci_debit_account_id=payload.nci_debit_account_id,
        nci_debit_amount=payload.nci_debit_amount,
        nci_credit_account_id=payload.nci_credit_account_id,
        nci_credit_amount=payload.nci_credit_amount,
        is_automatic=payload.is_automatic,
    )
    return consolidation_service.create_elimination_entry(
        db=db,
        group_id=organization_id,
        run_id=run_id,
        input=input_data,
    )


@router.get(
    "/runs/{run_id}/eliminations", response_model=ListResponse[EliminationEntryRead]
)
def list_elimination_entries(
    run_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    elimination_type: Optional[EliminationType] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("cons:eliminations:read")),
    db: Session = Depends(get_db),
):
    """List elimination entries for a run."""
    entries = consolidation_service.get_elimination_entries(
        db=db,
        run_id=run_id,
        elimination_type=elimination_type,
    )
    return ListResponse(
        items=entries[offset : offset + limit],
        count=len(entries),
        limit=limit,
        offset=offset,
    )


@router.post(
    "/runs/{run_id}/generate-intercompany-eliminations",
    response_model=list[EliminationEntryRead],
)
def generate_intercompany_eliminations(
    run_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    intercompany_elimination_account_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("cons:eliminations:generate")),
    db: Session = Depends(get_db),
):
    """Auto-generate intercompany elimination entries."""
    return consolidation_service.generate_intercompany_eliminations(
        db=db,
        group_id=organization_id,
        run_id=run_id,
        intercompany_elimination_account_id=intercompany_elimination_account_id,
    )


# =============================================================================
# Consolidation Postings
# =============================================================================


@router.post("/eliminations/{elimination_id}/post", response_model=PostingResultSchema)
def post_elimination_entry(
    elimination_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("cons:eliminations:post")),
    db: Session = Depends(get_db),
):
    """Post elimination entry to GL."""
    entry = db.get(EliminationEntry, elimination_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Elimination entry not found")

    result = cons_posting_adapter.post_elimination_entry(
        db=db,
        group_id=organization_id,
        run_id=entry.consolidation_run_id,
        entry_id=elimination_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=result.entry_number,
        message=result.message,
    )


@router.post("/runs/{run_id}/post-all", response_model=dict)
def post_all_eliminations(
    run_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("cons:eliminations:post")),
    db: Session = Depends(get_db),
):
    """Post all elimination entries for a run to GL."""
    results = cons_posting_adapter.post_all_eliminations(
        db=db,
        group_id=organization_id,
        run_id=run_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    successes = sum(1 for r in results if r.success)
    total = len(results)
    message = (
        "Posted eliminations" if successes == total else "Some eliminations failed"
    )
    return {
        "success": successes == total and total > 0,
        "entries_posted": successes,
        "message": message,
    }
