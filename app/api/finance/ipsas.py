"""
IPSAS API Routes.

FastAPI routes for IPSAS fund accounting, appropriations,
commitments, virements, and reporting.
"""

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import (
    _get_db as get_db,
)
from app.api.deps import (
    require_organization_id,
    require_tenant_auth,
    require_tenant_permission,
)
from app.schemas.finance.ipsas import (
    AllotmentCreate,
    AllotmentResponse,
    AppropriationCreate,
    AppropriationResponse,
    AvailableBalanceResponse,
    BudgetComparisonResponse,
    CoASegmentDefinitionCreate,
    CoASegmentDefinitionResponse,
    CoASegmentValueCreate,
    CoASegmentValueResponse,
    CommitmentResponse,
    FundCreate,
    FundResponse,
    FundUpdate,
    VirementCreate,
    VirementResponse,
)

router = APIRouter(
    prefix="/ipsas",
    tags=["ipsas"],
    dependencies=[Depends(require_tenant_auth)],
)


# =============================================================================
# Funds
# =============================================================================


@router.get("/funds", response_model=list[FundResponse])
def list_funds(
    organization_id: UUID = Depends(require_organization_id),
    status: str | None = None,
    fund_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ipsas:funds:read")),
    db: Session = Depends(get_db),
):
    """List funds for the organization."""
    from app.services.finance.ipsas.fund_service import FundService

    svc = FundService(db)
    return svc.list_for_org(
        organization_id, status=status, fund_type=fund_type, limit=limit, offset=offset
    )


@router.post("/funds", response_model=FundResponse, status_code=201)
def create_fund(
    payload: FundCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:funds:create")),
    db: Session = Depends(get_db),
):
    """Create a new fund."""
    from app.services.finance.ipsas.fund_service import FundService

    svc = FundService(db)
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id in auth context")
    user_id = UUID(person_id)
    fund = svc.create(organization_id, payload, user_id)
    return fund


@router.get("/funds/{fund_id}", response_model=FundResponse)
def get_fund(
    fund_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:funds:read")),
    db: Session = Depends(get_db),
):
    """Get a fund by ID."""
    from app.services.finance.ipsas.fund_service import FundService

    return FundService(db).get_or_404(fund_id, organization_id)


@router.put("/funds/{fund_id}", response_model=FundResponse)
def update_fund(
    fund_id: UUID,
    payload: FundUpdate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:funds:update")),
    db: Session = Depends(get_db),
):
    """Update a fund."""
    from app.services.finance.ipsas.fund_service import FundService

    svc = FundService(db)
    svc.get_or_404(fund_id, organization_id)  # verify tenant ownership
    fund = svc.update(fund_id, payload)
    return fund


@router.get("/funds/{fund_id}/balance")
def get_fund_balance(
    fund_id: UUID,
    fiscal_period_id: UUID | None = None,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:funds:read")),
    db: Session = Depends(get_db),
):
    """Get aggregated balance for a fund."""
    from app.services.finance.ipsas.fund_service import FundService

    svc = FundService(db)
    svc.get_or_404(fund_id, organization_id)  # verify tenant ownership
    balance = svc.get_fund_balance(fund_id, fiscal_period_id)
    return {"fund_id": str(fund_id), "balance": str(balance)}


# =============================================================================
# Appropriations
# =============================================================================


@router.get("/appropriations", response_model=list[AppropriationResponse])
def list_appropriations(
    organization_id: UUID = Depends(require_organization_id),
    fiscal_year_id: UUID | None = None,
    fund_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ipsas:appropriations:read")),
    db: Session = Depends(get_db),
):
    """List appropriations."""
    from app.services.finance.ipsas.appropriation_service import AppropriationService

    svc = AppropriationService(db)
    return svc.list_for_org(
        organization_id,
        fiscal_year_id=fiscal_year_id,
        fund_id=fund_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.post("/appropriations", response_model=AppropriationResponse, status_code=201)
def create_appropriation(
    payload: AppropriationCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:appropriations:create")),
    db: Session = Depends(get_db),
):
    """Create a new appropriation."""
    from app.services.finance.ipsas.appropriation_service import AppropriationService

    svc = AppropriationService(db)
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id in auth context")
    user_id = UUID(person_id)
    approp = svc.create(organization_id, payload, user_id)
    return approp


@router.get("/appropriations/{appropriation_id}", response_model=AppropriationResponse)
def get_appropriation(
    appropriation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:appropriations:read")),
    db: Session = Depends(get_db),
):
    """Get an appropriation by ID."""
    from app.services.finance.ipsas.appropriation_service import AppropriationService

    return AppropriationService(db).get_or_404(appropriation_id, organization_id)


@router.post(
    "/appropriations/{appropriation_id}/approve", response_model=AppropriationResponse
)
def approve_appropriation(
    appropriation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:appropriations:approve")),
    db: Session = Depends(get_db),
):
    """Approve an appropriation."""
    from app.services.finance.ipsas.appropriation_service import AppropriationService

    svc = AppropriationService(db)
    svc.get_or_404(appropriation_id, organization_id)  # verify tenant ownership
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id")
    user_id = UUID(person_id)
    approp = svc.approve(appropriation_id, user_id)
    return approp


@router.get(
    "/appropriations/{appropriation_id}/available-balance",
    response_model=AvailableBalanceResponse,
)
def get_appropriation_available_balance(
    appropriation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:appropriations:read")),
    db: Session = Depends(get_db),
):
    """Get available balance for an appropriation."""
    from app.services.finance.ipsas.appropriation_service import AppropriationService
    from app.services.finance.ipsas.available_balance_service import (
        AvailableBalanceService,
    )

    AppropriationService(db).get_or_404(appropriation_id, organization_id)
    return AvailableBalanceService(db).calculate(
        organization_id, appropriation_id=appropriation_id
    )


# =============================================================================
# Allotments
# =============================================================================


@router.get("/allotments", response_model=list[AllotmentResponse])
def list_allotments(
    organization_id: UUID = Depends(require_organization_id),
    appropriation_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ipsas:appropriations:read")),
    db: Session = Depends(get_db),
):
    """List allotments."""
    from app.services.finance.ipsas.appropriation_service import AppropriationService

    svc = AppropriationService(db)
    return svc.list_allotments(
        organization_id, appropriation_id=appropriation_id, limit=limit, offset=offset
    )


@router.post("/allotments", response_model=AllotmentResponse, status_code=201)
def create_allotment(
    payload: AllotmentCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:appropriations:create")),
    db: Session = Depends(get_db),
):
    """Create an allotment."""
    from app.services.finance.ipsas.appropriation_service import AppropriationService

    svc = AppropriationService(db)
    allotment = svc.create_allotment(organization_id, payload)
    return allotment


# =============================================================================
# Commitments
# =============================================================================


@router.get("/commitments", response_model=list[CommitmentResponse])
def list_commitments(
    organization_id: UUID = Depends(require_organization_id),
    fund_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ipsas:commitments:read")),
    db: Session = Depends(get_db),
):
    """List commitments."""
    from app.services.finance.ipsas.commitment_service import CommitmentService

    svc = CommitmentService(db)
    return svc.list_for_org(
        organization_id, fund_id=fund_id, status=status, limit=limit, offset=offset
    )


@router.get("/commitments/{commitment_id}", response_model=CommitmentResponse)
def get_commitment(
    commitment_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:commitments:read")),
    db: Session = Depends(get_db),
):
    """Get a commitment by ID."""
    from app.services.finance.ipsas.commitment_service import CommitmentService

    return CommitmentService(db).get_or_404(commitment_id, organization_id)


@router.post(
    "/commitments/from-po/{po_id}", response_model=CommitmentResponse, status_code=201
)
def create_commitment_from_po(
    po_id: UUID,
    fund_id: UUID = Query(...),
    account_id: UUID = Query(...),
    fiscal_year_id: UUID = Query(...),
    fiscal_period_id: UUID = Query(...),
    amount: Decimal = Query(...),
    currency_code: str = Query(...),
    commitment_number: str = Query(...),
    appropriation_id: UUID | None = None,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:commitments:create")),
    db: Session = Depends(get_db),
):
    """Create a commitment from a purchase order."""
    from app.services.finance.ipsas.commitment_service import CommitmentService

    svc = CommitmentService(db)
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id in auth context")
    user_id = UUID(person_id)
    commitment = svc.create_commitment_from_po(
        organization_id=organization_id,
        po_id=po_id,
        fund_id=fund_id,
        account_id=account_id,
        fiscal_year_id=fiscal_year_id,
        fiscal_period_id=fiscal_period_id,
        amount=amount,
        currency_code=currency_code,
        created_by_user_id=user_id,
        commitment_number=commitment_number,
        appropriation_id=appropriation_id,
    )
    return commitment


@router.post("/commitments/{commitment_id}/obligate", response_model=CommitmentResponse)
def obligate_commitment(
    commitment_id: UUID,
    amount: Decimal = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:commitments:update")),
    db: Session = Depends(get_db),
):
    """Record obligation against a commitment."""
    from app.services.finance.ipsas.commitment_service import CommitmentService

    svc = CommitmentService(db)
    svc.get_or_404(commitment_id, organization_id)  # verify tenant ownership
    commitment = svc.record_obligation(commitment_id, amount)
    return commitment


@router.post("/commitments/{commitment_id}/expend", response_model=CommitmentResponse)
def expend_commitment(
    commitment_id: UUID,
    amount: Decimal = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:commitments:update")),
    db: Session = Depends(get_db),
):
    """Record expenditure against a commitment."""
    from app.services.finance.ipsas.commitment_service import CommitmentService

    svc = CommitmentService(db)
    svc.get_or_404(commitment_id, organization_id)  # verify tenant ownership
    commitment = svc.record_expenditure(commitment_id, amount)
    return commitment


@router.post("/commitments/{commitment_id}/cancel", response_model=CommitmentResponse)
def cancel_commitment(
    commitment_id: UUID,
    amount: Decimal | None = None,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:commitments:update")),
    db: Session = Depends(get_db),
):
    """Cancel a commitment (full or partial)."""
    from app.services.finance.ipsas.commitment_service import CommitmentService

    svc = CommitmentService(db)
    svc.get_or_404(commitment_id, organization_id)  # verify tenant ownership
    commitment = svc.cancel_commitment(commitment_id, amount)
    return commitment


# =============================================================================
# Virements
# =============================================================================


@router.get("/virements", response_model=list[VirementResponse])
def list_virements(
    organization_id: UUID = Depends(require_organization_id),
    fiscal_year_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ipsas:virements:read")),
    db: Session = Depends(get_db),
):
    """List virements."""
    from app.services.finance.ipsas.virement_service import VirementService

    svc = VirementService(db)
    return svc.list_for_org(
        organization_id,
        fiscal_year_id=fiscal_year_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.post("/virements", response_model=VirementResponse, status_code=201)
def create_virement(
    payload: VirementCreate,
    virement_number: str = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:virements:create")),
    db: Session = Depends(get_db),
):
    """Create a virement request."""
    from app.services.finance.ipsas.virement_service import VirementService

    svc = VirementService(db)
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id in auth context")
    user_id = UUID(person_id)
    virement = svc.create(organization_id, payload, user_id, virement_number)
    return virement


@router.post("/virements/{virement_id}/approve", response_model=VirementResponse)
def approve_virement(
    virement_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:virements:approve")),
    db: Session = Depends(get_db),
):
    """Approve a virement."""
    from app.services.finance.ipsas.virement_service import VirementService

    svc = VirementService(db)
    svc.get_or_404(virement_id, organization_id)  # verify tenant ownership
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id")
    user_id = UUID(person_id)
    virement = svc.approve(virement_id, user_id)
    return virement


@router.post("/virements/{virement_id}/apply", response_model=VirementResponse)
def apply_virement(
    virement_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:virements:approve")),
    db: Session = Depends(get_db),
):
    """Apply an approved virement."""
    from app.services.finance.ipsas.virement_service import VirementService

    svc = VirementService(db)
    svc.get_or_404(virement_id, organization_id)  # verify tenant ownership
    virement = svc.apply(virement_id)
    return virement


# =============================================================================
# Reports
# =============================================================================


@router.get("/reports/budget-comparison", response_model=BudgetComparisonResponse)
def get_budget_comparison(
    fiscal_year_id: UUID = Query(...),
    fund_id: UUID | None = None,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:reports:read")),
    db: Session = Depends(get_db),
):
    """Generate IPSAS 24 Budget vs Actual comparison."""
    from app.services.finance.ipsas.budget_comparison_service import (
        BudgetComparisonService,
    )

    return BudgetComparisonService(db).generate_comparison(
        organization_id, fiscal_year_id, fund_id=fund_id
    )


@router.get("/reports/financial-position")
def get_financial_position(
    fiscal_period_id: UUID = Query(...),
    fund_id: UUID | None = None,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:reports:read")),
    db: Session = Depends(get_db),
):
    """Generate IPSAS 1 Statement of Financial Position."""
    from app.services.finance.ipsas.ipsas_statement_service import IPSASStatementService

    return IPSASStatementService(db).generate_financial_position(
        organization_id, fiscal_period_id, fund_id=fund_id
    )


@router.get("/reports/financial-performance")
def get_financial_performance(
    fiscal_period_id: UUID = Query(...),
    fund_id: UUID | None = None,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:reports:read")),
    db: Session = Depends(get_db),
):
    """Generate IPSAS 1 Statement of Financial Performance."""
    from app.services.finance.ipsas.ipsas_statement_service import IPSASStatementService

    return IPSASStatementService(db).generate_financial_performance(
        organization_id, fiscal_period_id, fund_id=fund_id
    )


@router.get("/reports/changes-net-assets")
def get_changes_net_assets(
    fiscal_period_id: UUID = Query(...),
    fund_id: UUID | None = None,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:reports:read")),
    db: Session = Depends(get_db),
):
    """Generate IPSAS 1 Statement of Changes in Net Assets."""
    from app.services.finance.ipsas.ipsas_statement_service import IPSASStatementService

    return IPSASStatementService(db).generate_changes_in_net_assets(
        organization_id, fiscal_period_id, fund_id=fund_id
    )


@router.get("/reports/cash-flow")
def get_cash_flow(
    fiscal_period_id: UUID = Query(...),
    fund_id: UUID | None = None,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:reports:read")),
    db: Session = Depends(get_db),
):
    """Generate IPSAS 2 Cash Flow Statement."""
    from app.services.finance.ipsas.ipsas_statement_service import IPSASStatementService

    return IPSASStatementService(db).generate_cash_flow(
        organization_id, fiscal_period_id, fund_id=fund_id
    )


# =============================================================================
# Available Balance
# =============================================================================


@router.get("/available-balance", response_model=AvailableBalanceResponse)
def get_available_balance(
    appropriation_id: UUID | None = None,
    fund_id: UUID | None = None,
    account_id: UUID | None = None,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:reports:read")),
    db: Session = Depends(get_db),
):
    """Calculate available balance."""
    from app.services.finance.ipsas.available_balance_service import (
        AvailableBalanceService,
    )

    return AvailableBalanceService(db).calculate(
        organization_id,
        appropriation_id=appropriation_id,
        fund_id=fund_id,
        account_id=account_id,
    )


@router.get(
    "/available-balance/by-fund/{fund_id}", response_model=AvailableBalanceResponse
)
def get_available_balance_by_fund(
    fund_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:reports:read")),
    db: Session = Depends(get_db),
):
    """Calculate available balance for a specific fund."""
    from app.services.finance.ipsas.available_balance_service import (
        AvailableBalanceService,
    )

    return AvailableBalanceService(db).calculate_by_fund(organization_id, fund_id)


# =============================================================================
# CoA Segments
# =============================================================================


@router.get("/coa-segments", response_model=list[CoASegmentDefinitionResponse])
def list_coa_segments(
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:coa:read")),
    db: Session = Depends(get_db),
):
    """List CoA segment definitions."""
    from app.services.finance.ipsas.coa_segment_service import CoASegmentService

    return CoASegmentService(db).list_definitions(organization_id)


@router.post(
    "/coa-segments", response_model=CoASegmentDefinitionResponse, status_code=201
)
def create_coa_segment(
    payload: CoASegmentDefinitionCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:coa:create")),
    db: Session = Depends(get_db),
):
    """Create a CoA segment definition."""
    from app.services.finance.ipsas.coa_segment_service import CoASegmentService

    seg = CoASegmentService(db).define_segment(organization_id, payload)
    return seg


@router.get(
    "/coa-segments/{segment_def_id}/values",
    response_model=list[CoASegmentValueResponse],
)
def list_coa_segment_values(
    segment_def_id: UUID,
    auth: dict = Depends(require_tenant_permission("ipsas:coa:read")),
    db: Session = Depends(get_db),
):
    """List values for a CoA segment."""
    from app.services.finance.ipsas.coa_segment_service import CoASegmentService

    return CoASegmentService(db).list_values(segment_def_id)


@router.post(
    "/coa-segments/{segment_def_id}/values",
    response_model=CoASegmentValueResponse,
    status_code=201,
)
def create_coa_segment_value(
    segment_def_id: UUID,
    payload: CoASegmentValueCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ipsas:coa:create")),
    db: Session = Depends(get_db),
):
    """Create a value for a CoA segment."""
    from app.services.finance.ipsas.coa_segment_service import CoASegmentService

    val = CoASegmentService(db).create_value(organization_id, segment_def_id, payload)
    return val
