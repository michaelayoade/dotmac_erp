"""Contract routes and API models for the AR API."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.api.finance.ar_routes.base import router
from app.models.finance.ar.contract import ContractStatus, ContractType
from app.schemas.finance.common import ListResponse
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ar import ContractInput, ProgressUpdateInput, contract_service


class PerformanceObligationCreate(BaseModel):
    """Performance obligation input."""

    description: str
    standalone_price: Decimal
    recognition_method: str = "OVER_TIME"
    measure_type: str | None = "OUTPUT"
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
    db: Session = Depends(get_db_with_org),
):
    """Create an IFRS 15 revenue contract."""
    obligations = [
        contract_service.build_obligation_input(
            description=obligation.description,
            standalone_price=obligation.standalone_price,
            revenue_account_id=obligation.revenue_account_id,
            recognition_method=obligation.recognition_method,
            ssp_determination_method=obligation.ssp_determination_method,
            is_distinct=obligation.is_distinct,
            over_time_method=obligation.over_time_method,
            progress_measure=obligation.progress_measure,
            measure_type=obligation.measure_type,
            expected_completion_date=obligation.expected_completion_date,
            contract_asset_account_id=obligation.contract_asset_account_id,
            contract_liability_account_id=obligation.contract_liability_account_id,
        )
        for obligation in payload.performance_obligations
    ]
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
    db: Session = Depends(get_db_with_org),
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
    db: Session = Depends(get_db_with_org),
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
        items=contracts,
        count=len(contracts),
        limit=limit,
        offset=offset,
    )


@router.post("/contracts/{contract_id}/activate", response_model=ContractRead)
def activate_contract(
    contract_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    approved_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ar:contracts:approve")),
    db: Session = Depends(get_db_with_org),
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
    db: Session = Depends(get_db_with_org),
):
    """Add a performance obligation to a contract."""
    input_data = contract_service.build_obligation_input(
        description=payload.description,
        standalone_price=payload.standalone_price,
        revenue_account_id=payload.revenue_account_id,
        recognition_method=payload.recognition_method,
        ssp_determination_method=payload.ssp_determination_method,
        is_distinct=payload.is_distinct,
        over_time_method=payload.over_time_method,
        progress_measure=payload.progress_measure,
        measure_type=payload.measure_type,
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
    db: Session = Depends(get_db_with_org),
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
