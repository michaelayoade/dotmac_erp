"""
CONS API Router.

Consolidation API endpoints per IFRS 10.
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
from app.services.ifrs.cons import (
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


router = APIRouter(prefix="/cons", tags=["consolidation"])


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
    entity_type: str = Field(max_length=30)  # PARENT, SUBSIDIARY, ASSOCIATE, JV
    country_code: str = Field(max_length=3)
    functional_currency: str = Field(max_length=3)
    reporting_currency: str = Field(max_length=3)
    consolidation_method: str = "FULL"  # FULL, PROPORTIONAL, EQUITY, NONE
    parent_entity_id: Optional[UUID] = None
    is_reporting_entity: bool = False


class LegalEntityRead(BaseModel):
    """Legal entity response."""

    model_config = ConfigDict(from_attributes=True)

    entity_id: UUID
    organization_id: UUID
    entity_code: str
    entity_name: str
    entity_type: str
    country_code: str
    functional_currency: str
    reporting_currency: str
    consolidation_method: str
    parent_entity_id: Optional[UUID]
    is_reporting_entity: bool
    goodwill_amount: Decimal
    is_active: bool


class GroupStructureRead(BaseModel):
    """Group structure response."""

    parent_entity_id: UUID
    parent_entity_code: str
    parent_entity_name: str
    subsidiaries: list["EntityHierarchyRead"]
    total_entities: int


class EntityHierarchyRead(BaseModel):
    """Entity hierarchy item."""

    entity_id: UUID
    entity_code: str
    entity_name: str
    entity_type: str
    consolidation_method: str
    effective_ownership: Decimal
    children: list["EntityHierarchyRead"] = []


class OwnershipCreate(BaseModel):
    """Create ownership interest request."""

    parent_entity_id: UUID
    subsidiary_entity_id: UUID
    ownership_percentage: Decimal
    voting_percentage: Decimal
    acquisition_date: date
    acquisition_cost: Decimal


class OwnershipRead(BaseModel):
    """Ownership interest response."""

    model_config = ConfigDict(from_attributes=True)

    ownership_id: UUID
    organization_id: UUID
    parent_entity_id: UUID
    subsidiary_entity_id: UUID
    ownership_percentage: Decimal
    voting_percentage: Decimal
    effective_ownership: Decimal
    acquisition_date: date
    acquisition_cost: Decimal
    disposal_date: Optional[date]
    is_active: bool


class NCISummaryRead(BaseModel):
    """NCI summary response."""

    entity_id: UUID
    entity_code: str
    entity_name: str
    nci_percentage: Decimal
    nci_equity: Decimal
    nci_profit: Decimal


class IntercompanyBalanceCreate(BaseModel):
    """Create intercompany balance request."""

    from_entity_id: UUID
    to_entity_id: UUID
    balance_date: date
    account_id: UUID
    currency_code: str = Field(max_length=3)
    functional_amount: Decimal
    reporting_amount: Decimal
    transaction_type: str = Field(max_length=30)
    reference: Optional[str] = None


class IntercompanyBalanceRead(BaseModel):
    """Intercompany balance response."""

    model_config = ConfigDict(from_attributes=True)

    balance_id: UUID
    organization_id: UUID
    from_entity_id: UUID
    to_entity_id: UUID
    balance_date: date
    account_id: UUID
    functional_amount: Decimal
    reporting_amount: Decimal
    is_matched: bool
    difference_amount: Decimal


class MatchingResultRead(BaseModel):
    """Intercompany matching result."""

    matched_pairs: int
    unmatched_balances: int
    total_difference: Decimal
    unmatched_items: list[dict]


class ConsolidationRunCreate(BaseModel):
    """Create consolidation run request."""

    fiscal_period_id: UUID
    consolidation_date: date
    description: Optional[str] = None


class ConsolidationRunRead(BaseModel):
    """Consolidation run response."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    organization_id: UUID
    fiscal_period_id: UUID
    consolidation_date: date
    status: str
    total_eliminations: Decimal
    entities_processed: int
    completed_at: Optional[date]


class EliminationEntryCreate(BaseModel):
    """Create elimination entry request."""

    elimination_type: str = Field(max_length=30)
    from_entity_id: UUID
    to_entity_id: Optional[UUID] = None
    debit_account_id: UUID
    credit_account_id: UUID
    amount: Decimal
    description: str = Field(max_length=200)


class EliminationEntryRead(BaseModel):
    """Elimination entry response."""

    model_config = ConfigDict(from_attributes=True)

    elimination_id: UUID
    run_id: UUID
    elimination_type: str
    from_entity_id: UUID
    to_entity_id: Optional[UUID]
    debit_account_id: UUID
    credit_account_id: UUID
    amount: Decimal
    description: str
    is_posted: bool


class ConsolidationSummaryRead(BaseModel):
    """Consolidation summary."""

    run_id: UUID
    consolidation_date: date
    entities_consolidated: int
    intercompany_eliminations: Decimal
    investment_eliminations: Decimal
    unrealized_profit_eliminations: Decimal
    total_eliminations: Decimal
    nci_adjustments: Decimal


# =============================================================================
# Legal Entities
# =============================================================================

@router.post("/entities", response_model=LegalEntityRead, status_code=status.HTTP_201_CREATED)
def create_legal_entity(
    payload: LegalEntityCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new legal entity."""
    input_data = LegalEntityInput(
        entity_code=payload.entity_code,
        entity_name=payload.entity_name,
        entity_type=payload.entity_type,
        country_code=payload.country_code,
        functional_currency=payload.functional_currency,
        reporting_currency=payload.reporting_currency,
        consolidation_method=payload.consolidation_method,
        parent_entity_id=payload.parent_entity_id,
        is_reporting_entity=payload.is_reporting_entity,
    )
    return legal_entity_service.create_entity(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/entities/{entity_id}", response_model=LegalEntityRead)
def get_legal_entity(
    entity_id: UUID,
    db: Session = Depends(get_db),
):
    """Get a legal entity by ID."""
    return legal_entity_service.get(db, str(entity_id))


@router.get("/entities", response_model=ListResponse[LegalEntityRead])
def list_legal_entities(
    organization_id: UUID = Query(...),
    entity_type: Optional[str] = None,
    consolidation_method: Optional[str] = None,
    is_active: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List legal entities with filters."""
    entities = legal_entity_service.list(
        db=db,
        organization_id=str(organization_id),
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


@router.get("/structure", response_model=GroupStructureRead)
def get_group_structure(
    organization_id: UUID = Query(...),
    parent_entity_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    """Get group structure hierarchy."""
    return legal_entity_service.get_group_structure(
        db=db,
        organization_id=str(organization_id),
        parent_entity_id=str(parent_entity_id) if parent_entity_id else None,
    )


@router.post("/entities/{entity_id}/update-consolidation-method", response_model=LegalEntityRead)
def update_consolidation_method(
    entity_id: UUID,
    consolidation_method: str = Query(...),
    effective_date: date = Query(...),
    organization_id: UUID = Query(...),
    updated_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Update entity consolidation method."""
    return legal_entity_service.update_consolidation_method(
        db=db,
        organization_id=organization_id,
        entity_id=entity_id,
        consolidation_method=consolidation_method,
        effective_date=effective_date,
        updated_by_user_id=updated_by_user_id,
    )


@router.post("/entities/{entity_id}/record-goodwill-impairment", response_model=LegalEntityRead)
def record_goodwill_impairment(
    entity_id: UUID,
    impairment_amount: Decimal = Query(...),
    impairment_date: date = Query(...),
    organization_id: UUID = Query(...),
    recorded_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Record goodwill impairment for entity."""
    return legal_entity_service.record_goodwill_impairment(
        db=db,
        organization_id=organization_id,
        entity_id=entity_id,
        impairment_amount=impairment_amount,
        impairment_date=impairment_date,
        recorded_by_user_id=recorded_by_user_id,
    )


# =============================================================================
# Ownership Interests
# =============================================================================

@router.post("/ownership", response_model=OwnershipRead, status_code=status.HTTP_201_CREATED)
def create_ownership(
    payload: OwnershipCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new ownership interest."""
    input_data = OwnershipInput(
        parent_entity_id=payload.parent_entity_id,
        subsidiary_entity_id=payload.subsidiary_entity_id,
        ownership_percentage=payload.ownership_percentage,
        voting_percentage=payload.voting_percentage,
        acquisition_date=payload.acquisition_date,
        acquisition_cost=payload.acquisition_cost,
    )
    return ownership_service.create_ownership(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/ownership", response_model=ListResponse[OwnershipRead])
def list_ownership_interests(
    organization_id: UUID = Query(...),
    parent_entity_id: Optional[UUID] = None,
    subsidiary_entity_id: Optional[UUID] = None,
    is_active: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List ownership interests with filters."""
    interests = ownership_service.list(
        db=db,
        organization_id=str(organization_id),
        parent_entity_id=str(parent_entity_id) if parent_entity_id else None,
        subsidiary_entity_id=str(subsidiary_entity_id) if subsidiary_entity_id else None,
        is_active=is_active,
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
    organization_id: UUID = Query(...),
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """Calculate effective ownership for an entity."""
    return ownership_service.calculate_effective_ownership(
        db=db,
        organization_id=str(organization_id),
        entity_id=str(entity_id),
        as_of_date=as_of_date,
    )


@router.get("/nci-summary", response_model=list[NCISummaryRead])
def get_nci_summary(
    organization_id: UUID = Query(...),
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """Get NCI summary for all subsidiaries."""
    return ownership_service.get_nci_summary(
        db=db,
        organization_id=str(organization_id),
        as_of_date=as_of_date,
    )


# =============================================================================
# Intercompany Balances
# =============================================================================

@router.post("/intercompany", response_model=IntercompanyBalanceRead, status_code=status.HTTP_201_CREATED)
def create_intercompany_balance(
    payload: IntercompanyBalanceCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Record an intercompany balance."""
    input_data = IntercompanyBalanceInput(
        from_entity_id=payload.from_entity_id,
        to_entity_id=payload.to_entity_id,
        balance_date=payload.balance_date,
        account_id=payload.account_id,
        currency_code=payload.currency_code,
        functional_amount=payload.functional_amount,
        reporting_amount=payload.reporting_amount,
        transaction_type=payload.transaction_type,
        reference=payload.reference,
    )
    return intercompany_service.record_balance(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/intercompany", response_model=ListResponse[IntercompanyBalanceRead])
def list_intercompany_balances(
    organization_id: UUID = Query(...),
    from_entity_id: Optional[UUID] = None,
    to_entity_id: Optional[UUID] = None,
    balance_date: Optional[date] = None,
    is_matched: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List intercompany balances with filters."""
    balances = intercompany_service.list(
        db=db,
        organization_id=str(organization_id),
        from_entity_id=str(from_entity_id) if from_entity_id else None,
        to_entity_id=str(to_entity_id) if to_entity_id else None,
        balance_date=balance_date,
        is_matched=is_matched,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=balances,
        count=len(balances),
        limit=limit,
        offset=offset,
    )


@router.post("/intercompany/match", response_model=MatchingResultRead)
def perform_intercompany_matching(
    organization_id: UUID = Query(...),
    balance_date: date = Query(...),
    tolerance_amount: Decimal = Query(default=Decimal("0.01")),
    db: Session = Depends(get_db),
):
    """Perform intercompany balance matching."""
    return intercompany_service.perform_matching(
        db=db,
        organization_id=str(organization_id),
        balance_date=balance_date,
        tolerance_amount=tolerance_amount,
    )


# =============================================================================
# Consolidation Runs
# =============================================================================

@router.post("/runs", response_model=ConsolidationRunRead, status_code=status.HTTP_201_CREATED)
def create_consolidation_run(
    payload: ConsolidationRunCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new consolidation run."""
    input_data = ConsolidationRunInput(
        fiscal_period_id=payload.fiscal_period_id,
        consolidation_date=payload.consolidation_date,
        description=payload.description,
    )
    return consolidation_service.create_run(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/runs/{run_id}", response_model=ConsolidationRunRead)
def get_consolidation_run(
    run_id: UUID,
    db: Session = Depends(get_db),
):
    """Get a consolidation run by ID."""
    return consolidation_service.get(db, str(run_id))


@router.get("/runs", response_model=ListResponse[ConsolidationRunRead])
def list_consolidation_runs(
    organization_id: UUID = Query(...),
    fiscal_period_id: Optional[UUID] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List consolidation runs with filters."""
    runs = consolidation_service.list(
        db=db,
        organization_id=str(organization_id),
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
    organization_id: UUID = Query(...),
    started_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Start a consolidation run."""
    return consolidation_service.start_run(
        db=db,
        organization_id=organization_id,
        run_id=run_id,
        started_by_user_id=started_by_user_id,
    )


@router.post("/runs/{run_id}/complete", response_model=ConsolidationRunRead)
def complete_consolidation_run(
    run_id: UUID,
    organization_id: UUID = Query(...),
    completed_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Complete a consolidation run."""
    return consolidation_service.complete_run(
        db=db,
        organization_id=organization_id,
        run_id=run_id,
        completed_by_user_id=completed_by_user_id,
    )


@router.get("/runs/{run_id}/summary", response_model=ConsolidationSummaryRead)
def get_consolidation_summary(
    run_id: UUID,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Get consolidation run summary."""
    return consolidation_service.get_summary(
        db=db,
        organization_id=str(organization_id),
        run_id=str(run_id),
    )


# =============================================================================
# Elimination Entries
# =============================================================================

@router.post("/runs/{run_id}/eliminations", response_model=EliminationEntryRead, status_code=status.HTTP_201_CREATED)
def create_elimination_entry(
    run_id: UUID,
    payload: EliminationEntryCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create an elimination entry."""
    input_data = EliminationInput(
        elimination_type=payload.elimination_type,
        from_entity_id=payload.from_entity_id,
        to_entity_id=payload.to_entity_id,
        debit_account_id=payload.debit_account_id,
        credit_account_id=payload.credit_account_id,
        amount=payload.amount,
        description=payload.description,
    )
    return consolidation_service.create_elimination_entry(
        db=db,
        organization_id=organization_id,
        run_id=run_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/runs/{run_id}/eliminations", response_model=ListResponse[EliminationEntryRead])
def list_elimination_entries(
    run_id: UUID,
    organization_id: UUID = Query(...),
    elimination_type: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List elimination entries for a run."""
    entries = consolidation_service.list_eliminations(
        db=db,
        organization_id=str(organization_id),
        run_id=str(run_id),
        elimination_type=elimination_type,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=entries,
        count=len(entries),
        limit=limit,
        offset=offset,
    )


@router.post("/runs/{run_id}/generate-intercompany-eliminations", response_model=list[EliminationEntryRead])
def generate_intercompany_eliminations(
    run_id: UUID,
    organization_id: UUID = Query(...),
    generated_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Auto-generate intercompany elimination entries."""
    return consolidation_service.generate_intercompany_eliminations(
        db=db,
        organization_id=organization_id,
        run_id=run_id,
        generated_by_user_id=generated_by_user_id,
    )


# =============================================================================
# Consolidation Postings
# =============================================================================

@router.post("/eliminations/{elimination_id}/post", response_model=PostingResultSchema)
def post_elimination_entry(
    elimination_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Post elimination entry to GL."""
    result = cons_posting_adapter.post_elimination_entry(
        db=db,
        organization_id=organization_id,
        elimination_id=elimination_id,
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
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Post all elimination entries for a run to GL."""
    result = cons_posting_adapter.post_all_eliminations(
        db=db,
        organization_id=organization_id,
        run_id=run_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return {
        "success": result.success,
        "entries_posted": result.entries_posted,
        "message": result.message,
    }
