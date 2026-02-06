"""
Item Sync Service - ERPNext to DotMac ERP.
"""

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.inventory.item import Item
from app.models.inventory.item_category import ItemCategory
from app.services.erpnext.mappings.items import ItemCategoryMapping, ItemMapping

from .base import BaseSyncService

logger = logging.getLogger(__name__)

# Default account codes for item categories
# These should match the chart of accounts in the target organization
DEFAULT_INVENTORY_ACCOUNT = "1300"  # Materials
DEFAULT_COGS_ACCOUNT = "5000"  # Purchases
DEFAULT_REVENUE_ACCOUNT = "4010"  # Other Business Revenue
DEFAULT_ADJUSTMENT_ACCOUNT = "1300"  # Materials (used for inventory adjustments)


# Valid values for item_type (VARCHAR)
ITEM_TYPE_MAP = {
    "INVENTORY": "INVENTORY",
    "NON_INVENTORY": "NON_INVENTORY",
    "SERVICE": "SERVICE",
    "KIT": "KIT",
}

# Valid values for costing_method (VARCHAR)
COSTING_METHOD_MAP = {
    "FIFO": "FIFO",
    "WEIGHTED_AVERAGE": "WEIGHTED_AVERAGE",
    "SPECIFIC_IDENTIFICATION": "SPECIFIC_IDENTIFICATION",
    "STANDARD_COST": "STANDARD_COST",
}


class ItemCategorySyncService(BaseSyncService[ItemCategory]):
    """Sync Item Groups (Categories) from ERPNext."""

    source_doctype = "Item Group"
    target_table = "inv.item_category"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = ItemCategoryMapping()
        self._category_cache: dict[str, ItemCategory] = {}
        self._default_accounts: dict[str, uuid.UUID] = {}

    def _get_default_accounts(self) -> dict[str, uuid.UUID]:
        """Get or cache default GL accounts for item categories."""
        if self._default_accounts:
            return self._default_accounts

        account_codes = [
            DEFAULT_INVENTORY_ACCOUNT,
            DEFAULT_COGS_ACCOUNT,
            DEFAULT_REVENUE_ACCOUNT,
            DEFAULT_ADJUSTMENT_ACCOUNT,
        ]

        accounts = (
            self.db.execute(
                select(Account).where(
                    Account.organization_id == self.organization_id,
                    Account.account_code.in_(account_codes),
                )
            )
            .scalars()
            .all()
        )

        for acc in accounts:
            self._default_accounts[acc.account_code] = acc.account_id

        return self._default_accounts

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        """Fetch item groups from ERPNext."""
        if since:
            yield from client.get_modified_since(
                doctype="Item Group",
                since=since,
            )
        else:
            yield from client.get_item_groups()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext item group to DotMac ERP format."""
        return self._mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> ItemCategory:
        """Create ItemCategory entity."""
        data.pop("_parent_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_is_group", None)

        # Truncate category_code to fit column
        category_code = (data.get("category_code") or "DEFAULT")[:30]
        category_name = (data.get("category_name") or category_code)[:100]

        # Get default accounts
        accounts = self._get_default_accounts()

        category = ItemCategory(
            organization_id=self.organization_id,
            category_code=category_code,
            category_name=category_name,
            inventory_account_id=accounts.get(DEFAULT_INVENTORY_ACCOUNT),
            cogs_account_id=accounts.get(DEFAULT_COGS_ACCOUNT),
            revenue_account_id=accounts.get(DEFAULT_REVENUE_ACCOUNT),
            inventory_adjustment_account_id=accounts.get(DEFAULT_ADJUSTMENT_ACCOUNT),
            is_active=data.get("is_active", True),
        )
        return category

    def update_entity(self, entity: ItemCategory, data: dict[str, Any]) -> ItemCategory:
        """Update existing ItemCategory."""
        data.pop("_parent_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_is_group", None)

        entity.category_name = (data.get("category_name") or entity.category_name)[:100]
        entity.is_active = data.get("is_active", True)

        return entity

    def get_entity_id(self, entity: ItemCategory) -> uuid.UUID:
        """Get category ID."""
        return entity.category_id

    def find_existing_entity(self, source_name: str) -> Optional[ItemCategory]:
        """Find existing category by code."""
        if source_name in self._category_cache:
            return self._category_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            category = self.db.get(ItemCategory, sync_entity.target_id)
            if category:
                self._category_cache[source_name] = category
                return category

        # Truncate to match column size
        code = source_name[:30] if source_name else ""
        result = self.db.execute(
            select(ItemCategory).where(
                ItemCategory.organization_id == self.organization_id,
                ItemCategory.category_code == code,
            )
        ).scalar_one_or_none()

        if result:
            self._category_cache[source_name] = result

        return result


class ItemSyncService(BaseSyncService[Item]):
    """Sync Items from ERPNext."""

    source_doctype = "Item"
    target_table = "inv.item"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = ItemMapping()
        self._item_cache: dict[str, Item] = {}
        self._category_sync = ItemCategorySyncService(db, organization_id, user_id)

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        """Fetch items from ERPNext."""
        if since:
            yield from client.get_modified_since(
                doctype="Item",
                since=since,
            )
        else:
            yield from client.get_items()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext item to DotMac ERP format."""
        return self._mapping.transform_record(record)

    def _get_or_create_category(self, category_name: str) -> uuid.UUID:
        """Get or create item category."""
        if not category_name:
            category_name = "General"

        # Check cache
        if category_name in self._category_sync._category_cache:
            return self._category_sync._category_cache[category_name].category_id

        # Try to find existing
        existing = self._category_sync.find_existing_entity(category_name)
        if existing:
            return existing.category_id

        # Get default accounts from category sync service
        accounts = self._category_sync._get_default_accounts()

        # Create new category with required accounts
        category = ItemCategory(
            organization_id=self.organization_id,
            category_code=category_name[:30],
            category_name=category_name[:100],
            inventory_account_id=accounts.get(DEFAULT_INVENTORY_ACCOUNT),
            cogs_account_id=accounts.get(DEFAULT_COGS_ACCOUNT),
            revenue_account_id=accounts.get(DEFAULT_REVENUE_ACCOUNT),
            inventory_adjustment_account_id=accounts.get(DEFAULT_ADJUSTMENT_ACCOUNT),
            is_active=True,
        )
        self.db.add(category)
        self.db.flush()
        self._category_sync._category_cache[category_name] = category
        return category.category_id

    def create_entity(self, data: dict[str, Any]) -> Item:
        """Create Item entity."""
        category_source = data.pop("_category_source_name", None)
        data.pop("_source_modified", None)

        # Get or create category
        category_id = self._get_or_create_category(category_source)

        # Map item_type - it's stored as VARCHAR, just use the string
        item_type = data.get("item_type", "INVENTORY")
        if item_type not in ITEM_TYPE_MAP:
            item_type = "INVENTORY"

        # Map costing_method
        costing_method = data.get("costing_method", "WEIGHTED_AVERAGE")
        if costing_method not in COSTING_METHOD_MAP:
            costing_method = "WEIGHTED_AVERAGE"

        # Parse decimals
        standard_cost = None
        if data.get("standard_cost"):
            try:
                standard_cost = Decimal(str(data["standard_cost"]))
            except Exception:
                pass

        last_purchase_cost = None
        if data.get("last_purchase_cost"):
            try:
                last_purchase_cost = Decimal(str(data["last_purchase_cost"]))
            except Exception:
                pass

        item = Item(
            organization_id=self.organization_id,
            item_code=data["item_code"][:50],
            item_name=data["item_name"][:200],
            description=data.get("description"),
            item_type=item_type,
            category_id=category_id,
            base_uom=(data.get("base_uom") or "Nos")[:20],
            costing_method=costing_method,
            track_inventory=bool(data.get("track_inventory", True)),
            track_lots=bool(data.get("track_lots", False)),
            track_serial_numbers=bool(data.get("track_serial_numbers", False)),
            standard_cost=standard_cost,
            last_purchase_cost=last_purchase_cost,
            currency_code=(data.get("currency_code") or "NGN")[:3],
            is_active=data.get("is_active", True),
            is_purchaseable=data.get("is_purchaseable", True),
            is_saleable=data.get("is_saleable", True),
        )
        return item

    def update_entity(self, entity: Item, data: dict[str, Any]) -> Item:
        """Update existing Item."""
        category_source = data.pop("_category_source_name", None)
        data.pop("_source_modified", None)

        category_id = self._get_or_create_category(category_source)

        item_type = data.get("item_type", "INVENTORY")
        if item_type not in ITEM_TYPE_MAP:
            item_type = "INVENTORY"

        costing_method = data.get("costing_method", "WEIGHTED_AVERAGE")
        if costing_method not in COSTING_METHOD_MAP:
            costing_method = "WEIGHTED_AVERAGE"

        entity.item_name = data["item_name"][:200]
        entity.description = data.get("description")
        entity.item_type = item_type
        entity.category_id = category_id
        entity.base_uom = (data.get("base_uom") or "Nos")[:20]
        entity.costing_method = costing_method
        entity.track_inventory = bool(data.get("track_inventory", True))
        entity.track_lots = bool(data.get("track_lots", False))
        entity.track_serial_numbers = bool(data.get("track_serial_numbers", False))
        entity.is_active = data.get("is_active", True)
        entity.is_purchaseable = data.get("is_purchaseable", True)
        entity.is_saleable = data.get("is_saleable", True)

        return entity

    def get_entity_id(self, entity: Item) -> uuid.UUID:
        """Get item ID."""
        return entity.item_id

    def find_existing_entity(self, source_name: str) -> Optional[Item]:
        """Find existing item by code."""
        if source_name in self._item_cache:
            return self._item_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            item = self.db.get(Item, sync_entity.target_id)
            if item:
                self._item_cache[source_name] = item
                return item

        code = source_name[:50] if source_name else ""
        result = self.db.execute(
            select(Item).where(
                Item.organization_id == self.organization_id,
                Item.item_code == code,
            )
        ).scalar_one_or_none()

        if result:
            self._item_cache[source_name] = result

        return result
