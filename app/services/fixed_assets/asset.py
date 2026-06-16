"""
AssetService - Fixed Asset master data management.

Manages asset records, categorization, and lifecycle status.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.finance.core_config.numbering_sequence import SequenceType
from app.models.fixed_assets.asset import Asset, AssetStatus
from app.models.fixed_assets.asset_category import AssetCategory, DepreciationMethod
from app.models.fixed_assets.depreciation_run import DepreciationRun
from app.models.fixed_assets.depreciation_schedule import DepreciationSchedule
from app.services.common import ValidationError, coerce_uuid
from app.services.finance.platform.sequence import SequenceService
from app.services.people.assets.lifecycle_event_service import (
    record_asset_lifecycle_event,
)
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)

ASSET_STATUS_UPDATE_ERROR = "Cannot update '{key}' after asset activation"
PRE_USE_ASSET_STATUSES = frozenset({AssetStatus.NOT_IN_USE, AssetStatus.IN_STORE})


def _record_lifecycle_event(db: Session, **kwargs: Any) -> None:
    """Persist lifecycle events only for real SQLAlchemy sessions."""
    if not isinstance(db, Session):
        return
    record_asset_lifecycle_event(db, **kwargs)


_MAX_CATEGORY_PARENT_WALK = 16


def _resolve_primary_category(
    db: Session, organization_id: UUID, category_id: UUID
) -> AssetCategory:
    """Resolve a category reference to its top-level (root) ancestor.

    Walks ``parent_category_id`` until it lands on a category with no parent.
    The cap (``_MAX_CATEGORY_PARENT_WALK``) is a defence against accidental
    cycles in the data — a non-malicious tree should never go that deep.
    """
    org_id = coerce_uuid(organization_id)
    category = db.get(AssetCategory, coerce_uuid(category_id))
    if not category or category.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Asset category not found")

    visited: set[UUID] = set()
    for _ in range(_MAX_CATEGORY_PARENT_WALK):
        if category.parent_category_id is None:
            break
        if category.category_id in visited:
            raise HTTPException(
                status_code=400, detail="Asset category hierarchy contains a cycle"
            )
        visited.add(category.category_id)
        parent_category = db.get(AssetCategory, category.parent_category_id)
        if not parent_category or parent_category.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset category not found")
        category = parent_category
    else:
        raise HTTPException(status_code=400, detail="Asset category hierarchy too deep")

    if not category.is_active:
        raise HTTPException(status_code=400, detail="Asset category is not active")

    return category


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
    revaluation_surplus_account_id: UUID | None = None
    impairment_loss_account_id: UUID | None = None
    parent_category_id: UUID | None = None
    description: str | None = None


@dataclass
class AssetInput:
    """Input for creating a fixed asset."""

    asset_name: str
    category_id: UUID
    acquisition_date: date
    acquisition_cost: Decimal
    currency_code: str
    asset_number: str | None = None
    description: str | None = None
    location_id: UUID | None = None
    cost_center_id: UUID | None = None
    custodian_user_id: UUID | None = None
    in_service_date: date | None = None
    source_type: str | None = None
    source_document_id: UUID | None = None
    supplier_id: UUID | None = None
    invoice_reference: str | None = None
    depreciation_method: str | None = None
    useful_life_months: int | None = None
    residual_value: Decimal | None = None
    serial_number: str | None = None
    barcode: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    warranty_expiry_date: date | None = None
    depreciation_schedule_id: UUID | None = None
    insured_value: Decimal | None = None
    insurance_policy_number: str | None = None
    cash_generating_unit_id: UUID | None = None
    parent_asset_id: UUID | None = None
    exchange_rate: Decimal | None = None
    status: AssetStatus | None = None


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
        existing = db.scalars(
            select(AssetCategory).where(
                and_(
                    AssetCategory.organization_id == org_id,
                    AssetCategory.category_code == input.category_code,
                )
            )
        ).first()

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
            parent_category_id=None,
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
        db.flush()
        db.refresh(category)

        return category

    @staticmethod
    def get(
        db: Session,
        category_id: str,
        organization_id: UUID | None = None,
    ) -> AssetCategory:
        """Get a category by ID."""
        category = db.get(AssetCategory, coerce_uuid(category_id))
        if not category:
            raise HTTPException(status_code=404, detail="Asset category not found")
        if organization_id is not None and category.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Asset category not found")
        return category

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        is_active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[AssetCategory]:
        """List asset categories."""
        query = select(AssetCategory)

        if organization_id:
            query = query.where(
                AssetCategory.organization_id == coerce_uuid(organization_id)
            )

        query = query.where(AssetCategory.parent_category_id.is_(None))

        if is_active is not None:
            query = query.where(AssetCategory.is_active == is_active)

        query = query.order_by(AssetCategory.category_code)
        return list(db.scalars(query.limit(limit).offset(offset)).all())

    @staticmethod
    def update_category(
        db: Session,
        organization_id: UUID,
        category_id: str,
        input: AssetCategoryInput,
        is_active: bool | None = None,
    ) -> AssetCategory:
        """Update an asset category."""
        org_id = coerce_uuid(organization_id)
        cat_id = coerce_uuid(category_id)

        category = db.get(AssetCategory, cat_id)
        if not category or category.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset category not found")

        if category.category_code != input.category_code:
            existing = db.scalars(
                select(AssetCategory).where(
                    and_(
                        AssetCategory.organization_id == org_id,
                        AssetCategory.category_code == input.category_code,
                    )
                )
            ).first()
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Category code '{input.category_code}' already exists",
                )

        category.category_code = input.category_code
        category.category_name = input.category_name
        category.description = input.description
        category.parent_category_id = None
        category.depreciation_method = input.depreciation_method
        category.useful_life_months = input.useful_life_months
        category.residual_value_percent = input.residual_value_percent
        category.asset_account_id = input.asset_account_id
        category.accumulated_depreciation_account_id = (
            input.accumulated_depreciation_account_id
        )
        category.depreciation_expense_account_id = input.depreciation_expense_account_id
        category.gain_loss_disposal_account_id = input.gain_loss_disposal_account_id
        category.revaluation_surplus_account_id = input.revaluation_surplus_account_id
        category.impairment_loss_account_id = input.impairment_loss_account_id
        category.capitalization_threshold = input.capitalization_threshold
        category.revaluation_model_allowed = input.revaluation_model_allowed
        if is_active is not None:
            category.is_active = is_active

        db.flush()
        db.refresh(category)
        return category

    @staticmethod
    def toggle_category(
        db: Session,
        organization_id: UUID,
        category_id: str,
    ) -> AssetCategory:
        """Toggle asset category active status."""
        org_id = coerce_uuid(organization_id)
        cat_id = coerce_uuid(category_id)

        category = db.get(AssetCategory, cat_id)
        if not category or category.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset category not found")

        category.is_active = not category.is_active
        db.flush()
        db.refresh(category)
        return category


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
        Create a new fixed asset in a pre-use status.

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

        # Validate financial inputs before any persistence work.
        if input.acquisition_cost is None or input.acquisition_cost <= 0:
            raise ValidationError("Acquisition cost must be greater than zero.")
        if input.useful_life_months is not None and input.useful_life_months <= 0:
            raise ValidationError("Useful life (months) must be greater than zero.")
        if (
            input.residual_value is not None
            and input.residual_value > input.acquisition_cost
        ):
            raise ValidationError("Residual value cannot exceed the acquisition cost.")
        if input.acquisition_date > date.today():
            raise ValidationError("Acquisition date cannot be in the future.")

        category = _resolve_primary_category(db, org_id, input.category_id)
        cat_id = category.category_id

        # Check capitalization threshold
        if (
            input.acquisition_cost > 0
            and input.acquisition_cost < category.capitalization_threshold
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Acquisition cost {input.acquisition_cost} is below capitalization threshold {category.capitalization_threshold}",
            )

        # Use provided asset number or generate from numbering sequence.
        asset_number = (input.asset_number or "").strip()
        if not asset_number:
            asset_number = SequenceService.get_next_number(
                db, org_id, SequenceType.ASSET
            )

        existing_asset = db.scalars(
            select(Asset).where(
                and_(
                    Asset.organization_id == org_id,
                    Asset.asset_number == asset_number,
                )
            )
        ).first()
        if existing_asset:
            raise HTTPException(
                status_code=400,
                detail=f"Asset number '{asset_number}' already exists",
            )

        # Calculate functional currency cost
        exchange_rate = input.exchange_rate or Decimal("1.0")
        functional_cost = input.acquisition_cost * exchange_rate

        schedule_id = (
            coerce_uuid(input.depreciation_schedule_id)
            if input.depreciation_schedule_id
            else None
        )
        if schedule_id is not None:
            schedule = db.get(DepreciationSchedule, schedule_id)
            if not schedule:
                raise HTTPException(
                    status_code=404,
                    detail="Depreciation schedule not found",
                )
            depreciation_run = db.get(DepreciationRun, schedule.run_id)
            if not depreciation_run or depreciation_run.organization_id != org_id:
                raise HTTPException(
                    status_code=400,
                    detail="Depreciation schedule does not belong to organization",
                )

        # Use category defaults if not specified
        depreciation_method = (
            input.depreciation_method or category.depreciation_method.value
        )
        useful_life = input.useful_life_months or category.useful_life_months
        residual_value = input.residual_value
        if residual_value is None:
            residual_value = input.acquisition_cost * (
                category.residual_value_percent / Decimal("100")
            )

        # Calculate initial net book value
        net_book_value = input.acquisition_cost

        initial_status = input.status or AssetStatus.NOT_IN_USE
        if initial_status == AssetStatus.RETIRED:
            raise HTTPException(
                status_code=400,
                detail="Retired status must be set through the disposal workflow",
            )

        in_service_date = input.in_service_date
        depreciation_start_date = None
        remaining_life_months = useful_life
        if initial_status in {AssetStatus.IN_USE, AssetStatus.FULLY_DEPRECIATED}:
            in_service_date = in_service_date or input.acquisition_date
            depreciation_start_date = in_service_date
        if initial_status == AssetStatus.FULLY_DEPRECIATED:
            remaining_life_months = 0

        asset = Asset(
            organization_id=org_id,
            asset_number=asset_number,
            asset_name=input.asset_name,
            description=input.description,
            category_id=cat_id,
            location_id=input.location_id,
            cost_center_id=input.cost_center_id,
            custodian_employee_id=input.custodian_user_id,
            acquisition_date=input.acquisition_date,
            in_service_date=in_service_date,
            acquisition_cost=input.acquisition_cost,
            currency_code=input.currency_code,
            functional_currency_cost=functional_cost,
            source_type=input.source_type,
            source_document_id=input.source_document_id,
            supplier_id=input.supplier_id,
            invoice_reference=input.invoice_reference,
            depreciation_method=depreciation_method,
            useful_life_months=useful_life,
            remaining_life_months=remaining_life_months,
            residual_value=residual_value,
            depreciation_start_date=depreciation_start_date,
            current_depreciation_schedule_id=schedule_id,
            accumulated_depreciation=Decimal("0"),
            net_book_value=net_book_value,
            impairment_loss=Decimal("0"),
            status=initial_status,
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
        db.flush()
        db.refresh(asset)
        _record_lifecycle_event(
            db,
            org_id=org_id,
            asset_id=asset.asset_id,
            event_category="STATE",
            event_type="STATE_CHANGED",
            source_type="asset",
            source_record_id=asset.asset_id,
            actor_user_id=user_id,
            new_status=AssetStatus.NOT_IN_USE.value,
            notes="Asset created and not yet in use",
            event_payload={"creation_method": "AssetService.create_asset"},
        )
        if asset.location_id:
            _record_lifecycle_event(
                db,
                org_id=org_id,
                asset_id=asset.asset_id,
                event_category="LOCATION",
                event_type="LOCATION_CHANGED",
                source_type="asset",
                source_record_id=asset.asset_id,
                actor_user_id=user_id,
                previous_location_id=None,
                new_location_id=asset.location_id,
                notes="Initial location set on asset creation",
            )
        if asset.custodian_employee_id:
            _record_lifecycle_event(
                db,
                org_id=org_id,
                asset_id=asset.asset_id,
                event_category="OWNERSHIP",
                event_type="OWNERSHIP_CHANGED",
                source_type="asset",
                source_record_id=asset.asset_id,
                actor_user_id=user_id,
                previous_owner_employee_id=None,
                new_owner_employee_id=asset.custodian_employee_id,
                notes="Initial custodian set on asset creation",
            )

        return asset

    @staticmethod
    def activate_asset(
        db: Session,
        organization_id: UUID,
        asset_id: UUID,
        in_service_date: date | None = None,
        depreciation_start_date: date | None = None,
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

        if asset.status not in PRE_USE_ASSET_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot activate asset with status '{asset.status.value}'",
            )

        previous_status = asset.status
        asset.status = AssetStatus.IN_USE
        asset.in_service_date = in_service_date or asset.acquisition_date
        asset.depreciation_start_date = depreciation_start_date or asset.in_service_date
        _record_lifecycle_event(
            db,
            org_id=org_id,
            asset_id=asset.asset_id,
            event_category="STATE",
            event_type="STATE_CHANGED",
            source_type="asset",
            source_record_id=asset.asset_id,
            previous_status=previous_status.value,
            new_status=asset.status.value,
            notes="Asset activated",
        )

        db.flush()
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
        previous_location_id = asset.location_id
        previous_owner_id = getattr(asset, "custodian_employee_id", None)
        previous_status = asset.status
        always_updatable = {
            "description",
            "location_id",
            "cost_center_id",
            "custodian_employee_id",
            "custodian_user_id",
            "serial_number",
            "barcode",
            "manufacturer",
            "model",
            "warranty_expiry_date",
            "insured_value",
            "insurance_policy_number",
            "cash_generating_unit_id",
            "current_depreciation_schedule_id",
            "status",
        }

        # Fields only updatable before the asset is placed in use.
        pre_use_only = {
            "asset_number",
            "asset_name",
            "category_id",
            "acquisition_date",
            "acquisition_cost",
            "currency_code",
            "depreciation_method",
            "useful_life_months",
            "residual_value",
        }

        for key, value in updates.items():
            if key in always_updatable:
                if key == "status":
                    new_status = (
                        value
                        if isinstance(value, AssetStatus)
                        else AssetStatus(str(value))
                    )
                    if new_status == AssetStatus.RETIRED:
                        raise HTTPException(
                            status_code=400,
                            detail="Retired status must be set through the disposal workflow",
                        )
                    asset.status = new_status
                    if new_status in {
                        AssetStatus.IN_USE,
                        AssetStatus.FULLY_DEPRECIATED,
                    }:
                        asset.in_service_date = (
                            asset.in_service_date or asset.acquisition_date
                        )
                        asset.depreciation_start_date = (
                            asset.depreciation_start_date or asset.in_service_date
                        )
                    if new_status == AssetStatus.FULLY_DEPRECIATED:
                        asset.remaining_life_months = 0
                    continue
                if key == "current_depreciation_schedule_id":
                    if value is not None:
                        schedule_id = coerce_uuid(value)
                        schedule = db.get(DepreciationSchedule, schedule_id)
                        if not schedule:
                            raise HTTPException(
                                status_code=404,
                                detail="Depreciation schedule not found",
                            )
                        depreciation_run = db.get(DepreciationRun, schedule.run_id)
                        if (
                            not depreciation_run
                            or depreciation_run.organization_id != org_id
                        ):
                            raise HTTPException(
                                status_code=400,
                                detail="Depreciation schedule does not belong to organization",
                            )
                        asset.current_depreciation_schedule_id = schedule_id
                    else:
                        asset.current_depreciation_schedule_id = None
                if key == "custodian_user_id":
                    asset.custodian_employee_id = value
                elif key != "current_depreciation_schedule_id":
                    setattr(asset, key, value)
            elif key in pre_use_only:
                if asset.status not in PRE_USE_ASSET_STATUSES:
                    raise HTTPException(
                        status_code=400,
                        detail=ASSET_STATUS_UPDATE_ERROR.format(key=key),
                    )
                if key == "acquisition_cost":
                    if value is None or value <= 0:
                        raise ValidationError(
                            "Acquisition cost must be greater than zero."
                        )
                if key == "useful_life_months":
                    if value is not None and value <= 0:
                        raise ValidationError(
                            "Useful life (months) must be greater than zero."
                        )
                if key == "residual_value" and value is not None:
                    effective_cost = updates.get(
                        "acquisition_cost", asset.acquisition_cost
                    )
                    if effective_cost is not None and value > effective_cost:
                        raise ValidationError(
                            "Residual value cannot exceed the acquisition cost."
                        )
                if key == "acquisition_date" and value is not None:
                    if value > date.today():
                        raise ValidationError(
                            "Acquisition date cannot be in the future."
                        )
                if key == "asset_number":
                    asset_number = (str(value) if value is not None else "").strip()
                    if not asset_number:
                        raise HTTPException(
                            status_code=400,
                            detail="Asset number is required",
                        )
                    existing_asset = db.scalars(
                        select(Asset).where(
                            and_(
                                Asset.organization_id == org_id,
                                Asset.asset_number == asset_number,
                                Asset.asset_id != asset.asset_id,
                            )
                        )
                    ).first()
                    if existing_asset:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Asset number '{asset_number}' already exists",
                        )
                    asset.asset_number = asset_number
                    continue
                if key == "category_id":
                    resolved_category = _resolve_primary_category(db, org_id, value)
                    asset.category_id = resolved_category.category_id
                    continue
                setattr(asset, key, value)
        if previous_location_id != asset.location_id:
            _record_lifecycle_event(
                db,
                org_id=org_id,
                asset_id=asset.asset_id,
                event_category="LOCATION",
                event_type="LOCATION_CHANGED",
                source_type="asset",
                source_record_id=asset.asset_id,
                previous_location_id=previous_location_id,
                new_location_id=asset.location_id,
                notes="Asset location updated",
            )
        if previous_owner_id != getattr(asset, "custodian_employee_id", None):
            _record_lifecycle_event(
                db,
                org_id=org_id,
                asset_id=asset.asset_id,
                event_category="OWNERSHIP",
                event_type="OWNERSHIP_CHANGED",
                source_type="asset",
                source_record_id=asset.asset_id,
                previous_owner_employee_id=previous_owner_id,
                new_owner_employee_id=getattr(asset, "custodian_employee_id", None),
                notes="Asset custodian updated",
            )
        if previous_status != asset.status:
            _record_lifecycle_event(
                db,
                org_id=org_id,
                asset_id=asset.asset_id,
                event_category="STATE",
                event_type="STATE_CHANGED",
                source_type="asset",
                source_record_id=asset.asset_id,
                previous_status=previous_status.value,
                new_status=asset.status.value,
                notes="Asset status updated",
            )

        db.flush()
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

        if asset.status != AssetStatus.IN_USE:
            raise HTTPException(
                status_code=400,
                detail="Only assets in use can be marked retired",
            )

        previous_status = asset.status
        asset.status = AssetStatus.FULLY_DEPRECIATED
        asset.remaining_life_months = 0
        _record_lifecycle_event(
            db,
            org_id=org_id,
            asset_id=asset.asset_id,
            event_category="STATE",
            event_type="STATE_CHANGED",
            source_type="asset",
            source_record_id=asset.asset_id,
            previous_status=previous_status.value,
            new_status=asset.status.value,
            notes="Asset retired after depreciation completion",
        )

        db.flush()
        db.refresh(asset)

        return asset

    @staticmethod
    def get(
        db: Session,
        asset_id: str,
        organization_id: UUID | None = None,
    ) -> Asset:
        """Get an asset by ID."""
        asset = db.get(Asset, coerce_uuid(asset_id))
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        if organization_id is not None and asset.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Asset not found")
        return asset

    @staticmethod
    def get_by_number(
        db: Session,
        organization_id: UUID,
        asset_number: str,
    ) -> Asset | None:
        """Get an asset by asset number."""
        org_id = coerce_uuid(organization_id)

        return db.scalars(
            select(Asset).where(
                and_(
                    Asset.organization_id == org_id,
                    Asset.asset_number == asset_number,
                )
            )
        ).first()

    @staticmethod
    def get_depreciable_assets(
        db: Session,
        organization_id: UUID,
        as_of_date: date | None = None,
    ) -> builtins.list[Asset]:
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

        stmt = (
            select(Asset)
            .where(
                and_(
                    Asset.organization_id == org_id,
                    Asset.status == AssetStatus.IN_USE,
                    Asset.depreciation_start_date <= ref_date,
                    Asset.remaining_life_months > 0,
                    Asset.net_book_value > Asset.residual_value,
                )
            )
            .order_by(Asset.asset_number)
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        category_id: str | None = None,
        status: AssetStatus | None = None,
        location_id: str | None = None,
        cost_center_id: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[Asset]:
        """List assets with optional filters."""
        query = select(Asset)

        if organization_id:
            query = query.where(Asset.organization_id == coerce_uuid(organization_id))

        if category_id:
            query = query.where(Asset.category_id == coerce_uuid(category_id))

        if status:
            query = query.where(Asset.status == status)

        if location_id:
            query = query.where(Asset.location_id == coerce_uuid(location_id))

        if cost_center_id:
            query = query.where(Asset.cost_center_id == coerce_uuid(cost_center_id))

        if search:
            search_pattern = f"%{search}%"
            query = query.where(
                or_(
                    Asset.asset_number.ilike(search_pattern),
                    Asset.asset_name.ilike(search_pattern),
                    Asset.serial_number.ilike(search_pattern),
                    Asset.barcode.ilike(search_pattern),
                )
            )

        query = query.order_by(Asset.asset_number)
        return list(db.scalars(query.limit(limit).offset(offset)).all())

    @staticmethod
    def get_asset_summary(
        db: Session,
        organization_id: UUID,
        category_id: UUID | None = None,
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

        query = select(Asset).where(Asset.organization_id == org_id)

        if category_id:
            query = query.where(Asset.category_id == coerce_uuid(category_id))

        assets = list(db.scalars(query).all())

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
