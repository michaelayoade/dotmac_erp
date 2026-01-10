"""
AssetService - Fixed Asset master data management.

Manages asset records, categorization, and lifecycle status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.ifrs.fa.asset import Asset, AssetStatus
from app.models.ifrs.fa.asset_category import AssetCategory, DepreciationMethod
from app.models.ifrs.core_config.numbering_sequence import SequenceType
from app.services.common import coerce_uuid
from app.services.ifrs.platform.sequence import SequenceService
from app.services.response import ListResponseMixin


@dataclass
class AssetCategoryInput:
    """Input for creating/updating an asset category."""

    category_code: str
    category_name: str
    asset_account_id: UUID
    accumulated_depreciation_account_id: UUID
    depreciation_expense_account_id: UUID
    gain_loss_disposal_account_id: UUID
    useful_life_months: int
    depreciation_method: DepreciationMethod = DepreciationMethod.STRAIGHT_LINE
    residual_value_percent: Decimal = Decimal("0")
    capitalization_threshold: Decimal = Decimal("0")
    revaluation_model_allowed: bool = False
    revaluation_surplus_account_id: Optional[UUID] = None
    impairment_loss_account_id: Optional[UUID] = None
    parent_category_id: Optional[UUID] = None
    description: Optional[str] = None


@dataclass
class AssetInput:
    """Input for creating a fixed asset."""

    asset_name: str
    category_id: UUID
    acquisition_date: date
    acquisition_cost: Decimal
    currency_code: str
    description: Optional[str] = None
    location_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    custodian_user_id: Optional[UUID] = None
    in_service_date: Optional[date] = None
    source_type: Optional[str] = None
    source_document_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    invoice_reference: Optional[str] = None
    depreciation_method: Optional[str] = None
    useful_life_months: Optional[int] = None
    residual_value: Optional[Decimal] = None
    serial_number: Optional[str] = None
    barcode: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    warranty_expiry_date: Optional[date] = None
    insured_value: Optional[Decimal] = None
    insurance_policy_number: Optional[str] = None
    cash_generating_unit_id: Optional[UUID] = None
    parent_asset_id: Optional[UUID] = None
    exchange_rate: Optional[Decimal] = None


class AssetCategoryService(ListResponseMixin):
    """
    Service for asset category management.

    Manages asset classifications with default depreciation parameters.
    """

    @staticmethod
    def create_category(
        db: Session,
        organization_id: UUID,
        input: AssetCategoryInput,
    ) -> AssetCategory:
        """
        Create a new asset category.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Category input data

        Returns:
            Created AssetCategory
        """
        org_id = coerce_uuid(organization_id)

        # Check for duplicate category code
        existing = (
            db.query(AssetCategory)
            .filter(
                and_(
                    AssetCategory.organization_id == org_id,
                    AssetCategory.category_code == input.category_code,
                )
            )
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Category code '{input.category_code}' already exists",
            )

        category = AssetCategory(
            organization_id=org_id,
            category_code=input.category_code,
            category_name=input.category_name,
            description=input.description,
            parent_category_id=input.parent_category_id,
            depreciation_method=input.depreciation_method,
            useful_life_months=input.useful_life_months,
            residual_value_percent=input.residual_value_percent,
            asset_account_id=input.asset_account_id,
            accumulated_depreciation_account_id=input.accumulated_depreciation_account_id,
            depreciation_expense_account_id=input.depreciation_expense_account_id,
            gain_loss_disposal_account_id=input.gain_loss_disposal_account_id,
            revaluation_surplus_account_id=input.revaluation_surplus_account_id,
            impairment_loss_account_id=input.impairment_loss_account_id,
            capitalization_threshold=input.capitalization_threshold,
            revaluation_model_allowed=input.revaluation_model_allowed,
            is_active=True,
        )

        db.add(category)
        db.commit()
        db.refresh(category)

        return category

    @staticmethod
    def get(
        db: Session,
        category_id: str,
    ) -> AssetCategory:
        """Get a category by ID."""
        category = db.get(AssetCategory, coerce_uuid(category_id))
        if not category:
            raise HTTPException(status_code=404, detail="Asset category not found")
        return category

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        is_active: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AssetCategory]:
        """List asset categories."""
        query = db.query(AssetCategory)

        if organization_id:
            query = query.filter(
                AssetCategory.organization_id == coerce_uuid(organization_id)
            )

        if is_active is not None:
            query = query.filter(AssetCategory.is_active == is_active)

        query = query.order_by(AssetCategory.category_code)
        return query.limit(limit).offset(offset).all()


class AssetService(ListResponseMixin):
    """
    Service for fixed asset master data management.

    Handles asset creation, updates, and lifecycle status changes.
    """

    @staticmethod
    def create_asset(
        db: Session,
        organization_id: UUID,
        input: AssetInput,
        created_by_user_id: UUID,
    ) -> Asset:
        """
        Create a new fixed asset in DRAFT status.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Asset input data
            created_by_user_id: User creating the asset

        Returns:
            Created Asset
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        cat_id = coerce_uuid(input.category_id)

        # Load category for defaults
        category = db.get(AssetCategory, cat_id)
        if not category or category.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset category not found")

        if not category.is_active:
            raise HTTPException(status_code=400, detail="Asset category is not active")

        # Check capitalization threshold
        if input.acquisition_cost < category.capitalization_threshold:
            raise HTTPException(
                status_code=400,
                detail=f"Acquisition cost {input.acquisition_cost} is below capitalization threshold {category.capitalization_threshold}",
            )

        # Generate asset number
        asset_number = SequenceService.get_next_number(
            db, org_id, SequenceType.ASSET
        )

        # Calculate functional currency cost
        exchange_rate = input.exchange_rate or Decimal("1.0")
        functional_cost = input.acquisition_cost * exchange_rate

        # Use category defaults if not specified
        depreciation_method = input.depreciation_method or category.depreciation_method.value
        useful_life = input.useful_life_months or category.useful_life_months
        residual_value = input.residual_value
        if residual_value is None:
            residual_value = input.acquisition_cost * (category.residual_value_percent / Decimal("100"))

        # Calculate initial net book value
        net_book_value = input.acquisition_cost

        asset = Asset(
            organization_id=org_id,
            asset_number=asset_number,
            asset_name=input.asset_name,
            description=input.description,
            category_id=cat_id,
            location_id=input.location_id,
            cost_center_id=input.cost_center_id,
            custodian_user_id=input.custodian_user_id,
            acquisition_date=input.acquisition_date,
            in_service_date=input.in_service_date,
            acquisition_cost=input.acquisition_cost,
            currency_code=input.currency_code,
            functional_currency_cost=functional_cost,
            source_type=input.source_type,
            source_document_id=input.source_document_id,
            supplier_id=input.supplier_id,
            invoice_reference=input.invoice_reference,
            depreciation_method=depreciation_method,
            useful_life_months=useful_life,
            remaining_life_months=useful_life,
            residual_value=residual_value,
            accumulated_depreciation=Decimal("0"),
            net_book_value=net_book_value,
            impairment_loss=Decimal("0"),
            status=AssetStatus.DRAFT,
            cash_generating_unit_id=input.cash_generating_unit_id,
            serial_number=input.serial_number,
            barcode=input.barcode,
            manufacturer=input.manufacturer,
            model=input.model,
            warranty_expiry_date=input.warranty_expiry_date,
            insured_value=input.insured_value,
            insurance_policy_number=input.insurance_policy_number,
            is_component_parent=input.parent_asset_id is None and False,
            parent_asset_id=input.parent_asset_id,
            created_by_user_id=user_id,
        )

        db.add(asset)
        db.commit()
        db.refresh(asset)

        return asset

    @staticmethod
    def activate_asset(
        db: Session,
        organization_id: UUID,
        asset_id: UUID,
        in_service_date: Optional[date] = None,
        depreciation_start_date: Optional[date] = None,
    ) -> Asset:
        """
        Activate an asset and set it ready for depreciation.

        Args:
            db: Database session
            organization_id: Organization scope
            asset_id: Asset to activate
            in_service_date: Date asset was put in service
            depreciation_start_date: Date to start depreciation

        Returns:
            Updated Asset
        """
        org_id = coerce_uuid(organization_id)
        ast_id = coerce_uuid(asset_id)

        asset = db.get(Asset, ast_id)
        if not asset or asset.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset not found")

        if asset.status != AssetStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot activate asset with status '{asset.status.value}'",
            )

        asset.status = AssetStatus.ACTIVE
        asset.in_service_date = in_service_date or asset.acquisition_date
        asset.depreciation_start_date = depreciation_start_date or asset.in_service_date

        db.commit()
        db.refresh(asset)

        return asset

    @staticmethod
    def update_asset(
        db: Session,
        organization_id: UUID,
        asset_id: UUID,
        updates: dict[str, Any],
    ) -> Asset:
        """
        Update asset attributes.

        Only certain fields can be updated after activation.

        Args:
            db: Database session
            organization_id: Organization scope
            asset_id: Asset to update
            updates: Dictionary of field updates

        Returns:
            Updated Asset
        """
        org_id = coerce_uuid(organization_id)
        ast_id = coerce_uuid(asset_id)

        asset = db.get(Asset, ast_id)
        if not asset or asset.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset not found")

        # Fields that can be updated after activation
        always_updatable = {
            "description",
            "location_id",
            "cost_center_id",
            "custodian_user_id",
            "serial_number",
            "barcode",
            "manufacturer",
            "model",
            "warranty_expiry_date",
            "insured_value",
            "insurance_policy_number",
            "cash_generating_unit_id",
        }

        # Fields only updatable in DRAFT status
        draft_only = {
            "asset_name",
            "category_id",
            "acquisition_date",
            "acquisition_cost",
            "depreciation_method",
            "useful_life_months",
            "residual_value",
        }

        for key, value in updates.items():
            if key in always_updatable:
                setattr(asset, key, value)
            elif key in draft_only:
                if asset.status != AssetStatus.DRAFT:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot update '{key}' after asset activation",
                    )
                setattr(asset, key, value)

        db.commit()
        db.refresh(asset)

        return asset

    @staticmethod
    def mark_fully_depreciated(
        db: Session,
        organization_id: UUID,
        asset_id: UUID,
    ) -> Asset:
        """Mark an asset as fully depreciated."""
        org_id = coerce_uuid(organization_id)
        ast_id = coerce_uuid(asset_id)

        asset = db.get(Asset, ast_id)
        if not asset or asset.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset not found")

        if asset.status != AssetStatus.ACTIVE:
            raise HTTPException(
                status_code=400,
                detail=f"Only ACTIVE assets can be marked fully depreciated",
            )

        asset.status = AssetStatus.FULLY_DEPRECIATED
        asset.remaining_life_months = 0

        db.commit()
        db.refresh(asset)

        return asset

    @staticmethod
    def get(
        db: Session,
        asset_id: str,
    ) -> Asset:
        """Get an asset by ID."""
        asset = db.get(Asset, coerce_uuid(asset_id))
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        return asset

    @staticmethod
    def get_by_number(
        db: Session,
        organization_id: UUID,
        asset_number: str,
    ) -> Optional[Asset]:
        """Get an asset by asset number."""
        org_id = coerce_uuid(organization_id)

        return (
            db.query(Asset)
            .filter(
                and_(
                    Asset.organization_id == org_id,
                    Asset.asset_number == asset_number,
                )
            )
            .first()
        )

    @staticmethod
    def get_depreciable_assets(
        db: Session,
        organization_id: UUID,
        as_of_date: Optional[date] = None,
    ) -> list[Asset]:
        """
        Get all assets eligible for depreciation.

        Args:
            db: Database session
            organization_id: Organization scope
            as_of_date: Reference date for depreciation eligibility

        Returns:
            List of depreciable assets
        """
        org_id = coerce_uuid(organization_id)
        ref_date = as_of_date or date.today()

        return (
            db.query(Asset)
            .filter(
                and_(
                    Asset.organization_id == org_id,
                    Asset.status == AssetStatus.ACTIVE,
                    Asset.depreciation_start_date <= ref_date,
                    Asset.remaining_life_months > 0,
                    Asset.net_book_value > Asset.residual_value,
                )
            )
            .order_by(Asset.asset_number)
            .all()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        category_id: Optional[str] = None,
        status: Optional[AssetStatus] = None,
        location_id: Optional[str] = None,
        cost_center_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Asset]:
        """List assets with optional filters."""
        query = db.query(Asset)

        if organization_id:
            query = query.filter(
                Asset.organization_id == coerce_uuid(organization_id)
            )

        if category_id:
            query = query.filter(Asset.category_id == coerce_uuid(category_id))

        if status:
            query = query.filter(Asset.status == status)

        if location_id:
            query = query.filter(Asset.location_id == coerce_uuid(location_id))

        if cost_center_id:
            query = query.filter(Asset.cost_center_id == coerce_uuid(cost_center_id))

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Asset.asset_number.ilike(search_pattern))
                | (Asset.asset_name.ilike(search_pattern))
                | (Asset.serial_number.ilike(search_pattern))
                | (Asset.barcode.ilike(search_pattern))
            )

        query = query.order_by(Asset.asset_number)
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def get_asset_summary(
        db: Session,
        organization_id: UUID,
        category_id: Optional[UUID] = None,
    ) -> dict[str, Any]:
        """
        Get summary statistics for assets.

        Args:
            db: Database session
            organization_id: Organization scope
            category_id: Optional filter by category

        Returns:
            Summary statistics dictionary
        """
        org_id = coerce_uuid(organization_id)

        query = db.query(Asset).filter(Asset.organization_id == org_id)

        if category_id:
            query = query.filter(Asset.category_id == coerce_uuid(category_id))

        assets = query.all()

        total_cost = Decimal("0")
        total_accum_dep = Decimal("0")
        total_nbv = Decimal("0")
        count_by_status: dict[str, int] = {}

        for asset in assets:
            total_cost += asset.acquisition_cost
            total_accum_dep += asset.accumulated_depreciation
            total_nbv += asset.net_book_value

            status_name = asset.status.value
            count_by_status[status_name] = count_by_status.get(status_name, 0) + 1

        return {
            "total_assets": len(assets),
            "total_cost": total_cost,
            "total_accumulated_depreciation": total_accum_dep,
            "total_net_book_value": total_nbv,
            "count_by_status": count_by_status,
        }


# Module-level singleton instances
asset_category_service = AssetCategoryService()
asset_service = AssetService()
