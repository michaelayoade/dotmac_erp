"""
FA API Router.

Fixed Assets API endpoints for asset management, depreciation, and disposals.
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.services.auth_dependencies import require_tenant_permission
from app.services.feature_flags import require_feature, FEATURE_FIXED_ASSETS
from app.api.finance.utils import parse_enum
from app.db import SessionLocal
from app.schemas.finance.common import ListResponse, PostingResultSchema
from app.models.fixed_assets.asset import AssetStatus
from app.services.fixed_assets import (
    asset_service,
    depreciation_service,
    fa_posting_adapter,
    asset_disposal_service,
    DisposalInput,
    AssetInput,
)
from app.models.fixed_assets.asset_disposal import DisposalType


router = APIRouter(
    prefix="/fixed-assets",
    tags=["fixed-assets"],
    dependencies=[Depends(require_tenant_auth), Depends(require_feature(FEATURE_FIXED_ASSETS))],
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

class AssetCreate(BaseModel):
    """Create asset request."""

    asset_name: str = Field(max_length=200)
    category_id: UUID = Field(alias="asset_category_id")
    acquisition_date: date
    acquisition_cost: Decimal
    currency_code: str = Field(max_length=3)
    useful_life_months: Optional[int] = None
    residual_value: Decimal = Decimal("0")
    depreciation_method: Optional[str] = "STRAIGHT_LINE"
    location_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    description: Optional[str] = None


class AssetRead(BaseModel):
    """Asset response."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    asset_id: UUID
    organization_id: UUID
    asset_number: str = Field(alias="asset_code")
    asset_name: str
    category_id: UUID = Field(alias="asset_category_id")
    acquisition_date: date
    acquisition_cost: Decimal
    accumulated_depreciation: Decimal
    net_book_value: Decimal
    useful_life_months: int
    remaining_life_months: int
    depreciation_method: str
    status: str


class DepreciationRunCreate(BaseModel):
    """Depreciation run request."""

    fiscal_period_id: UUID
    description: Optional[str] = None


class DepreciationRunRead(BaseModel):
    """Depreciation run response."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    fiscal_period_id: UUID
    run_number: int
    run_description: Optional[str] = None
    total_depreciation: Decimal
    assets_processed: int
    status: str


class DisposalCreate(BaseModel):
    """Asset disposal request."""

    fiscal_period_id: UUID
    disposal_date: date
    disposal_type: str = Field(max_length=30)
    disposal_proceeds: Decimal = Decimal("0")
    costs_of_disposal: Decimal = Decimal("0")
    buyer_name: Optional[str] = None
    buyer_reference: Optional[str] = None
    invoice_number: Optional[str] = None
    disposal_reason: Optional[str] = None
    authorization_reference: Optional[str] = None
    trade_in_asset_id: Optional[UUID] = None
    insurance_claim_reference: Optional[str] = None
    insurance_proceeds: Optional[Decimal] = None


class DisposalRead(BaseModel):
    """Asset disposal response."""

    model_config = ConfigDict(from_attributes=True)

    disposal_id: UUID
    asset_id: UUID
    fiscal_period_id: UUID
    disposal_date: date
    disposal_type: str
    cost_at_disposal: Decimal
    accumulated_depreciation_at_disposal: Decimal
    net_book_value_at_disposal: Decimal
    disposal_proceeds: Decimal
    costs_of_disposal: Decimal
    net_proceeds: Decimal
    gain_loss_on_disposal: Decimal


# =============================================================================
# Assets
# =============================================================================

@router.post("/assets", response_model=AssetRead, status_code=status.HTTP_201_CREATED)
def create_asset(
    payload: AssetCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("fa:assets:create")),
    db: Session = Depends(get_db),
):
    """Create a new fixed asset."""
    input_data = AssetInput(
        asset_name=payload.asset_name,
        category_id=payload.category_id,
        acquisition_date=payload.acquisition_date,
        acquisition_cost=payload.acquisition_cost,
        currency_code=payload.currency_code,
        useful_life_months=payload.useful_life_months,
        residual_value=payload.residual_value,
        depreciation_method=payload.depreciation_method,
        location_id=payload.location_id,
        cost_center_id=payload.cost_center_id,
        description=payload.description,
    )
    return asset_service.create_asset(db, organization_id, input_data, created_by_user_id)


@router.get("/assets/{asset_id}", response_model=AssetRead)
def get_asset(
    asset_id: UUID,
    auth: dict = Depends(require_tenant_permission("fa:assets:read")),
    db: Session = Depends(get_db),
):
    """Get a fixed asset by ID."""
    return asset_service.get(db, str(asset_id))


@router.get("/assets", response_model=ListResponse[AssetRead])
def list_assets(
    organization_id: UUID = Depends(require_organization_id),
    asset_category_id: Optional[UUID] = None,
    status: Optional[str] = None,
    location_id: Optional[UUID] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("fa:assets:read")),
    db: Session = Depends(get_db),
):
    """List fixed assets with filters."""
    assets = asset_service.list(
        db=db,
        organization_id=str(organization_id),
        category_id=str(asset_category_id) if asset_category_id else None,
        status=parse_enum(AssetStatus, status),
        location_id=str(location_id) if location_id else None,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=assets,
        count=len(assets),
        limit=limit,
        offset=offset,
    )


@router.post("/assets/{asset_id}/capitalize", response_model=AssetRead)
def capitalize_asset(
    asset_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    in_service_date: Optional[date] = None,
    depreciation_start_date: Optional[date] = None,
    auth: dict = Depends(require_tenant_permission("fa:assets:capitalize")),
    db: Session = Depends(get_db),
):
    """Capitalize an asset (put in service)."""
    return asset_service.activate_asset(
        db=db,
        organization_id=organization_id,
        asset_id=asset_id,
        in_service_date=in_service_date,
        depreciation_start_date=depreciation_start_date,
    )


@router.post("/assets/{asset_id}/post-acquisition", response_model=PostingResultSchema)
def post_asset_acquisition(
    asset_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    credit_account_id: UUID = Query(...),
    description: Optional[str] = None,
    auth: dict = Depends(require_tenant_permission("fa:assets:post")),
    db: Session = Depends(get_db),
):
    """Post asset acquisition to GL."""
    result = fa_posting_adapter.post_asset_acquisition(
        db=db,
        organization_id=organization_id,
        asset_id=asset_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
        credit_account_id=credit_account_id,
        description=description,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=None,
        message=result.message,
    )


# =============================================================================
# Depreciation
# =============================================================================

@router.post("/depreciation/run", response_model=DepreciationRunRead, status_code=status.HTTP_201_CREATED)
def run_depreciation(
    payload: DepreciationRunCreate,
    organization_id: UUID = Depends(require_organization_id),
    run_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("fa:depreciation:run")),
    db: Session = Depends(get_db),
):
    """Run depreciation for a fiscal period."""
    run = depreciation_service.create_depreciation_run(
        db=db,
        organization_id=organization_id,
        fiscal_period_id=payload.fiscal_period_id,
        created_by_user_id=run_by_user_id,
        description=payload.description,
    )
    return depreciation_service.calculate_run(
        db=db,
        organization_id=organization_id,
        run_id=run.run_id,
    )


@router.get("/depreciation/runs", response_model=ListResponse[DepreciationRunRead])
def list_depreciation_runs(
    organization_id: UUID = Depends(require_organization_id),
    fiscal_period_id: Optional[UUID] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("fa:depreciation:read")),
    db: Session = Depends(get_db),
):
    """List depreciation runs."""
    runs = depreciation_service.list(
        db=db,
        organization_id=str(organization_id),
        fiscal_period_id=str(fiscal_period_id) if fiscal_period_id else None,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=runs,
        count=len(runs),
        limit=limit,
        offset=offset,
    )


@router.post("/depreciation/runs/{run_id}/post", response_model=PostingResultSchema)
def post_depreciation(
    run_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("fa:depreciation:post")),
    db: Session = Depends(get_db),
):
    """Post depreciation run to GL."""
    result = fa_posting_adapter.post_depreciation_run(
        db=db,
        organization_id=organization_id,
        run_id=run_id,
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
# Disposals
# =============================================================================

@router.post("/assets/{asset_id}/dispose", response_model=DisposalRead, status_code=status.HTTP_201_CREATED)
def dispose_asset(
    asset_id: UUID,
    payload: DisposalCreate,
    organization_id: UUID = Depends(require_organization_id),
    disposed_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("fa:disposals:create")),
    db: Session = Depends(get_db),
):
    """Dispose of a fixed asset."""
    try:
        disposal_type = DisposalType(payload.disposal_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    input_data = DisposalInput(
        asset_id=asset_id,
        fiscal_period_id=payload.fiscal_period_id,
        disposal_date=payload.disposal_date,
        disposal_type=disposal_type,
        disposal_proceeds=payload.disposal_proceeds,
        costs_of_disposal=payload.costs_of_disposal,
        buyer_name=payload.buyer_name,
        buyer_reference=payload.buyer_reference,
        invoice_number=payload.invoice_number,
        disposal_reason=payload.disposal_reason,
        authorization_reference=payload.authorization_reference,
        trade_in_asset_id=payload.trade_in_asset_id,
        insurance_claim_reference=payload.insurance_claim_reference,
        insurance_proceeds=payload.insurance_proceeds,
    )
    return asset_disposal_service.create_disposal(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=disposed_by_user_id,
    )


@router.post("/disposals/{disposal_id}/post", response_model=PostingResultSchema)
def post_disposal(
    disposal_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("fa:disposals:post")),
    db: Session = Depends(get_db),
):
    """Post asset disposal to GL."""
    result = fa_posting_adapter.post_asset_disposal(
        db=db,
        organization_id=organization_id,
        disposal_id=disposal_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=None,
        message=result.message,
    )
