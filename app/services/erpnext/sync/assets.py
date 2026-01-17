"""
Asset Sync Service - ERPNext to DotMac Books.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ifrs.gl.account import Account
from app.models.ifrs.fa.asset import Asset, AssetStatus
from app.models.ifrs.fa.asset_category import AssetCategory, DepreciationMethod
from app.services.erpnext.mappings.assets import AssetMapping, AssetCategoryMapping

from .base import BaseSyncService


# Default account codes for asset categories
DEFAULT_ASSET_ACCOUNT = "ACC00064"  # Office Equipment (general fixed asset)
DEFAULT_ACCUM_DEPR_ACCOUNT = "ACC00068"  # Accumulated depreciation on PPE
DEFAULT_DEPR_EXPENSE_ACCOUNT = "ACC00036"  # Depreciation Expense
DEFAULT_DISPOSAL_ACCOUNT = "ACC00135"  # Loss on disposal of assets


class AssetCategorySyncService(BaseSyncService[AssetCategory]):
    """Sync Asset Categories from ERPNext."""

    source_doctype = "Asset Category"
    target_table = "fa.asset_category"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = AssetCategoryMapping()
        self._category_cache: dict[str, AssetCategory] = {}
        self._default_accounts: dict[str, uuid.UUID] = {}

    def _get_default_accounts(self) -> dict[str, uuid.UUID]:
        """Get or cache default GL accounts for asset categories."""
        if self._default_accounts:
            return self._default_accounts

        account_codes = [
            DEFAULT_ASSET_ACCOUNT,
            DEFAULT_ACCUM_DEPR_ACCOUNT,
            DEFAULT_DEPR_EXPENSE_ACCOUNT,
            DEFAULT_DISPOSAL_ACCOUNT,
        ]

        accounts = self.db.execute(
            select(Account).where(
                Account.organization_id == self.organization_id,
                Account.account_code.in_(account_codes),
            )
        ).scalars().all()

        for acc in accounts:
            self._default_accounts[acc.account_code] = acc.account_id

        return self._default_accounts

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        """Fetch asset categories from ERPNext."""
        if since:
            yield from client.get_modified_since(
                doctype="Asset Category",
                since=since,
            )
        else:
            yield from client.get_asset_categories()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext asset category to DotMac Books format."""
        return self._mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> AssetCategory:
        """Create AssetCategory entity."""
        data.pop("_source_modified", None)

        # Map depreciation method
        dep_method = DepreciationMethod.STRAIGHT_LINE
        if data.get("depreciation_method"):
            try:
                dep_method = DepreciationMethod(data["depreciation_method"])
            except ValueError:
                pass

        # Get default accounts
        accounts = self._get_default_accounts()

        category = AssetCategory(
            organization_id=self.organization_id,
            category_code=data["category_code"][:30],
            category_name=data["category_name"][:100],
            depreciation_method=dep_method,
            useful_life_months=data.get("useful_life_months", 60),
            residual_value_percent=Decimal(str(data.get("residual_value_percent", 0))),
            is_active=data.get("is_active", True),
            # Required accounts
            asset_account_id=accounts.get(DEFAULT_ASSET_ACCOUNT),
            accumulated_depreciation_account_id=accounts.get(DEFAULT_ACCUM_DEPR_ACCOUNT),
            depreciation_expense_account_id=accounts.get(DEFAULT_DEPR_EXPENSE_ACCOUNT),
            gain_loss_disposal_account_id=accounts.get(DEFAULT_DISPOSAL_ACCOUNT),
        )
        return category

    def update_entity(self, entity: AssetCategory, data: dict[str, Any]) -> AssetCategory:
        """Update existing AssetCategory."""
        data.pop("_source_modified", None)

        entity.category_name = data["category_name"]
        if data.get("depreciation_method"):
            try:
                entity.depreciation_method = DepreciationMethod(data["depreciation_method"])
            except ValueError:
                pass
        entity.useful_life_months = data.get("useful_life_months", entity.useful_life_months)
        entity.residual_value_percent = Decimal(str(data.get("residual_value_percent", 0)))
        entity.is_active = data.get("is_active", True)

        return entity

    def get_entity_id(self, entity: AssetCategory) -> uuid.UUID:
        """Get category ID."""
        return entity.category_id

    def find_existing_entity(self, source_name: str) -> Optional[AssetCategory]:
        """Find existing category by code."""
        if source_name in self._category_cache:
            return self._category_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            category = self.db.get(AssetCategory, sync_entity.target_id)
            if category:
                self._category_cache[source_name] = category
                return category

        result = self.db.execute(
            select(AssetCategory).where(
                AssetCategory.organization_id == self.organization_id,
                AssetCategory.category_code == source_name,
            )
        ).scalar_one_or_none()

        if result:
            self._category_cache[source_name] = result

        return result


class AssetSyncService(BaseSyncService[Asset]):
    """Sync Assets from ERPNext."""

    source_doctype = "Asset"
    target_table = "fa.asset"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = AssetMapping()
        self._asset_cache: dict[str, Asset] = {}
        self._category_sync = AssetCategorySyncService(db, organization_id, user_id)

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        """Fetch assets from ERPNext."""
        if since:
            yield from client.get_modified_since(
                doctype="Asset",
                since=since,
            )
        else:
            yield from client.get_assets()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext asset to DotMac Books format."""
        return self._mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> Asset:
        """Create Asset entity."""
        # Resolve category
        category_source = data.pop("_category_source_name", None)
        category_id = self._resolve_category_id(category_source)

        # Location is optional - skip for now
        data.pop("_location_source_name", None)
        data.pop("_source_modified", None)

        # Map status
        status = AssetStatus.ACTIVE
        if data.get("status"):
            try:
                status = AssetStatus(data["status"])
            except ValueError:
                pass

        # Handle required acquisition_date - fallback to in_service_date or today
        acquisition_date = data.get("acquisition_date")
        if not acquisition_date:
            acquisition_date = data.get("in_service_date")
        if not acquisition_date:
            from datetime import date
            acquisition_date = date.today()

        # Calculate costs
        acquisition_cost = Decimal(str(data.get("acquisition_cost", 0)))

        asset = Asset(
            organization_id=self.organization_id,
            asset_number=data["asset_number"][:30],
            asset_name=data["asset_name"][:200],
            category_id=category_id,
            acquisition_date=acquisition_date,
            in_service_date=data.get("in_service_date") or acquisition_date,
            acquisition_cost=acquisition_cost,
            currency_code=data.get("currency_code", "NGN")[:3],
            functional_currency_cost=acquisition_cost,  # Same as acquisition_cost for NGN
            depreciation_method=data.get("depreciation_method", "STRAIGHT_LINE"),
            useful_life_months=data.get("useful_life_months", 60),
            remaining_life_months=data.get("remaining_life_months", 60),
            residual_value=Decimal(str(data.get("residual_value", 0))),
            accumulated_depreciation=Decimal(str(data.get("accumulated_depreciation", 0))),
            net_book_value=Decimal(str(data.get("net_book_value", 0))),
            impairment_loss=Decimal("0"),  # Default no impairment
            status=status,
            serial_number=data.get("serial_number"),
            disposal_date=data.get("disposal_date"),
            is_component_parent=False,
            created_by_user_id=self.user_id,
        )
        return asset

    def update_entity(self, entity: Asset, data: dict[str, Any]) -> Asset:
        """Update existing Asset."""
        category_source = data.pop("_category_source_name", None)
        category_id = self._resolve_category_id(category_source)
        data.pop("_location_source_name", None)
        data.pop("_source_modified", None)

        # Handle acquisition_date - fallback to existing or in_service_date
        acquisition_date = data.get("acquisition_date")
        if not acquisition_date:
            acquisition_date = data.get("in_service_date") or entity.acquisition_date

        entity.asset_name = data["asset_name"][:200]
        entity.category_id = category_id
        entity.acquisition_date = acquisition_date
        entity.in_service_date = data.get("in_service_date") or acquisition_date
        entity.acquisition_cost = Decimal(str(data.get("acquisition_cost", 0)))
        entity.depreciation_method = data.get("depreciation_method", "STRAIGHT_LINE")
        entity.useful_life_months = data.get("useful_life_months", 60)
        entity.remaining_life_months = data.get("remaining_life_months", 60)
        entity.residual_value = Decimal(str(data.get("residual_value", 0)))
        entity.accumulated_depreciation = Decimal(str(data.get("accumulated_depreciation", 0)))
        entity.net_book_value = Decimal(str(data.get("net_book_value", 0)))
        entity.serial_number = data.get("serial_number")
        entity.disposal_date = data.get("disposal_date")

        if data.get("status"):
            try:
                entity.status = AssetStatus(data["status"])
            except ValueError:
                pass

        return entity

    def get_entity_id(self, entity: Asset) -> uuid.UUID:
        """Get asset ID."""
        return entity.asset_id

    def find_existing_entity(self, source_name: str) -> Optional[Asset]:
        """Find existing asset by number."""
        if source_name in self._asset_cache:
            return self._asset_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            asset = self.db.get(Asset, sync_entity.target_id)
            if asset:
                self._asset_cache[source_name] = asset
                return asset

        result = self.db.execute(
            select(Asset).where(
                Asset.organization_id == self.organization_id,
                Asset.asset_number == source_name,
            )
        ).scalar_one_or_none()

        if result:
            self._asset_cache[source_name] = result

        return result

    def _resolve_category_id(self, category_source: Optional[str]) -> uuid.UUID:
        """Resolve category ID, raising error if not found."""
        if not category_source:
            raise ValueError("Asset category is required")

        # Check category sync entity
        existing = self._category_sync.find_existing_entity(category_source)
        if existing:
            return existing.category_id

        raise ValueError(f"Asset category not found: {category_source}")
