"""
Items/Inventory Importer.

Imports inventory items from CSV data into the IFRS-based inventory system.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.inv.item import Item, ItemType, CostingMethod
from app.models.finance.inv.item_category import ItemCategory

from .base import BaseImporter, FieldMapping, ImportConfig


class ItemCategoryImporter(BaseImporter[ItemCategory]):
    """
    Importer for item categories.

    Creates categories from unique item groups/categories in the source data.
    """

    entity_name = "Item Category"
    model_class = ItemCategory

    def __init__(
        self,
        db: Session,
        config: ImportConfig,
        inventory_account_id: UUID,
        cogs_account_id: UUID,
        revenue_account_id: UUID,
        adjustment_account_id: UUID,
    ):
        super().__init__(db, config)
        self.inventory_account_id = inventory_account_id
        self.cogs_account_id = cogs_account_id
        self.revenue_account_id = revenue_account_id
        self.adjustment_account_id = adjustment_account_id
        self._category_cache: Dict[str, UUID] = {}

    def get_field_mappings(self) -> List[FieldMapping]:
        return []

    def get_unique_key(self, row: Dict[str, Any]) -> str:
        value = row.get("Category Name") or row.get("Item Group") or "Default"
        return str(value).strip()

    def check_duplicate(self, row: Dict[str, Any]) -> Optional[ItemCategory]:
        category_name = self.get_unique_key(row)
        category_code = self._make_category_code(category_name)

        if category_code in self._category_cache:
            return self.db.get(ItemCategory, self._category_cache[category_code])

        existing = self.db.execute(
            select(ItemCategory).where(
                ItemCategory.organization_id == self.config.organization_id,
                ItemCategory.category_code == category_code,
            )
        ).scalar_one_or_none()

        if existing:
            self._category_cache[category_code] = existing.category_id

        return existing

    def create_entity(self, row: Dict[str, Any]) -> ItemCategory:
        category_name = self.get_unique_key(row)
        category_code = self._make_category_code(category_name)

        category = ItemCategory(
            category_id=uuid4(),
            organization_id=self.config.organization_id,
            category_code=category_code,
            category_name=category_name[:100],
            description=f"Imported category: {category_name}",
            inventory_account_id=self.inventory_account_id,
            cogs_account_id=self.cogs_account_id,
            revenue_account_id=self.revenue_account_id,
            inventory_adjustment_account_id=self.adjustment_account_id,
            is_active=True,
        )

        self._category_cache[category_code] = category.category_id
        return category

    def _make_category_code(self, name: str) -> str:
        return name.upper().replace(" ", "_")[:30]

    def get_category_id(self, category_name: str) -> Optional[UUID]:
        code = self._make_category_code(category_name)
        return self._category_cache.get(code)

    def ensure_categories(self, rows: List[Dict[str, Any]]) -> None:
        """Ensure all required categories exist."""
        unique_categories = set()
        for row in rows:
            cat_name = row.get("Category Name") or row.get("Item Group") or "Default"
            if cat_name:
                unique_categories.add(cat_name.strip())

        for cat_name in unique_categories:
            row = {"Category Name": cat_name}
            if not self.check_duplicate(row):
                category = self.create_entity(row)
                self.db.add(category)
                self.db.flush()


class ItemImporter(BaseImporter[Item]):
    """
    Importer for inventory items from CSV data.

    Expected CSV columns (flexible - maps common naming conventions):
    - Item Name / Name / Product Name: Item name (required)
    - Item Code / SKU / Product Code: Item code (required)
    - Description / Item Description: Description
    - Item Type / Type: INVENTORY, SERVICE, NON_INVENTORY, KIT
    - Category Name / Item Group / Category: Category for the item
    - Unit / UOM / Base Unit: Base unit of measure
    - Purchase Price / Cost / Unit Cost: Purchase/cost price
    - Selling Price / Sales Price / List Price: Selling price
    - Currency Code / Currency: Currency code (default: NGN)
    - Reorder Point / Reorder Level: Reorder point
    - Track Inventory: Whether to track inventory (true/false)
    - Is Taxable / Taxable: Whether taxable
    - Status / Is Active: Active status
    """

    entity_name = "Item"
    model_class = Item

    def __init__(
        self,
        db: Session,
        config: ImportConfig,
        inventory_account_id: UUID,
        cogs_account_id: UUID,
        revenue_account_id: UUID,
        adjustment_account_id: UUID,
    ):
        super().__init__(db, config)
        self._code_counter = 0
        self._category_importer = ItemCategoryImporter(
            db, config,
            inventory_account_id, cogs_account_id,
            revenue_account_id, adjustment_account_id
        )

    def get_field_mappings(self) -> List[FieldMapping]:
        """Define flexible field mappings supporting various CSV formats."""
        return [
            # Name mappings - try multiple common column names
            FieldMapping("Item Name", "item_name", required=False),
            FieldMapping("Name", "item_name_alt", required=False),
            FieldMapping("Product Name", "item_name_alt2", required=False),
            # Code mappings
            FieldMapping("Item Code", "item_code", required=False),
            FieldMapping("SKU", "sku", required=False),
            FieldMapping("Product Code", "product_code", required=False),
            # Description
            FieldMapping("Description", "description", required=False),
            FieldMapping("Item Description", "description_alt", required=False),
            # Type
            FieldMapping("Item Type", "item_type_str", required=False),
            FieldMapping("Type", "type_alt", required=False),
            # Category
            FieldMapping("Category Name", "category_name", required=False),
            FieldMapping("Item Group", "item_group", required=False),
            FieldMapping("Category", "category_alt", required=False),
            # UOM
            FieldMapping("Unit", "base_uom", required=False, default="EACH"),
            FieldMapping("UOM", "uom_alt", required=False),
            FieldMapping("Base Unit", "base_unit_alt", required=False),
            # Pricing
            FieldMapping("Purchase Price", "purchase_cost", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Cost", "cost_alt", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Unit Cost", "unit_cost_alt", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Selling Price", "list_price", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Sales Price", "sales_price_alt", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("List Price", "list_price_alt", required=False,
                         transformer=self.parse_decimal),
            # Currency
            FieldMapping("Currency Code", "currency_code", required=False, default="NGN"),
            FieldMapping("Currency", "currency_alt", required=False),
            # Stock management
            FieldMapping("Reorder Point", "reorder_point", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Reorder Level", "reorder_level_alt", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Reorder Quantity", "reorder_quantity", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Minimum Stock", "minimum_stock", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Maximum Stock", "maximum_stock", required=False,
                         transformer=self.parse_decimal),
            # Flags
            FieldMapping("Track Inventory", "track_inventory", required=False,
                         transformer=self.parse_boolean, default=True),
            FieldMapping("Is Taxable", "is_taxable", required=False,
                         transformer=self.parse_boolean, default=True),
            FieldMapping("Taxable", "taxable_alt", required=False,
                         transformer=self.parse_boolean),
            FieldMapping("Status", "status_str", required=False),
            FieldMapping("Is Active", "is_active", required=False,
                         transformer=self.parse_boolean, default=True),
            # Additional
            FieldMapping("Barcode", "barcode", required=False),
            FieldMapping("Manufacturer Part Number", "manufacturer_part_number", required=False),
            FieldMapping("MPN", "mpn_alt", required=False),
            FieldMapping("Weight", "weight", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Weight Unit", "weight_uom", required=False),
        ]

    def get_unique_key(self, row: Dict[str, Any]) -> str:
        """Unique key is item code or SKU."""
        code = str(row.get("Item Code") or row.get("SKU") or
                   row.get("Product Code") or "").strip()
        if code:
            return code
        # Fallback to name
        name = str(row.get("Item Name") or row.get("Name") or
                   row.get("Product Name") or "").strip()
        return name

    def check_duplicate(self, row: Dict[str, Any]) -> Optional[Item]:
        """Check if item already exists."""
        key = self.get_unique_key(row)
        if not key:
            return None

        # Check by code
        existing = self.db.execute(
            select(Item).where(
                Item.organization_id == self.config.organization_id,
                Item.item_code == key,
            )
        ).scalar_one_or_none()

        if existing:
            return existing

        # Check by name
        name = str(row.get("Item Name") or row.get("Name") or
                   row.get("Product Name") or "").strip()
        if name:
            existing = self.db.execute(
                select(Item).where(
                    Item.organization_id == self.config.organization_id,
                    Item.item_name == name,
                )
            ).scalar_one_or_none()

        return existing

    def create_entity(self, row: Dict[str, Any]) -> Item:
        """Create a new item from transformed row data."""
        # Get item name (try multiple fields)
        item_name = str(row.get("item_name") or row.get("item_name_alt") or
                        row.get("item_name_alt2") or "Unknown Item").strip()

        # Get item code (try multiple fields or generate)
        item_code = str(row.get("item_code") or row.get("sku") or
                        row.get("product_code") or "").strip()
        if not item_code:
            self._code_counter += 1
            item_code = f"ITEM{self._code_counter:05d}"

        # Get description
        description = row.get("description") or row.get("description_alt")

        # Determine item type
        type_str = str(row.get("item_type_str") or row.get("type_alt") or "INVENTORY").upper()
        item_type = self._parse_item_type(type_str)

        # Get category
        category_name = str(row.get("category_name") or row.get("item_group") or
                            row.get("category_alt") or "Default")
        category_id = self._category_importer.get_category_id(category_name)

        # Get UOM
        base_uom = str(row.get("base_uom") or row.get("uom_alt") or
                       row.get("base_unit_alt") or "EACH")[:20]

        # Get pricing
        purchase_cost = (row.get("purchase_cost") or row.get("cost_alt") or
                         row.get("unit_cost_alt"))
        list_price = (row.get("list_price") or row.get("sales_price_alt") or
                      row.get("list_price_alt"))

        # Get currency
        currency_code = str(row.get("currency_code") or
                            row.get("currency_alt") or "NGN")[:3]

        # Get stock management
        reorder_point = row.get("reorder_point") or row.get("reorder_level_alt")

        # Get flags
        track_inventory = row.get("track_inventory", True)
        is_taxable = row.get("is_taxable") or row.get("taxable_alt")
        if is_taxable is None:
            is_taxable = True

        is_active = row.get("is_active", True)
        status_val = row.get("status_str")
        if status_val:
            is_active = str(status_val).lower() not in ("inactive", "disabled", "false")

        item = Item(
            item_id=uuid4(),
            organization_id=self.config.organization_id,
            item_code=item_code[:50],
            item_name=item_name[:200],
            description=description,
            item_type=item_type,
            category_id=category_id,
            base_uom=base_uom,
            costing_method=CostingMethod.WEIGHTED_AVERAGE,
            last_purchase_cost=purchase_cost,
            currency_code=currency_code,
            list_price=list_price,
            track_inventory=track_inventory if item_type == ItemType.INVENTORY else False,
            track_lots=False,
            track_serial_numbers=False,
            reorder_point=reorder_point,
            reorder_quantity=row.get("reorder_quantity"),
            minimum_stock=row.get("minimum_stock"),
            maximum_stock=row.get("maximum_stock"),
            barcode=row.get("barcode"),
            manufacturer_part_number=row.get("manufacturer_part_number") or row.get("mpn_alt"),
            weight=row.get("weight"),
            weight_uom=row.get("weight_uom"),
            is_taxable=is_taxable,
            is_active=is_active,
            is_purchaseable=True,
            is_saleable=True,
        )

        return item

    def _parse_item_type(self, type_str: str) -> ItemType:
        """Parse item type string to enum."""
        type_map = {
            "INVENTORY": ItemType.INVENTORY,
            "GOODS": ItemType.INVENTORY,
            "PRODUCT": ItemType.INVENTORY,
            "SERVICE": ItemType.SERVICE,
            "SERVICES": ItemType.SERVICE,
            "NON_INVENTORY": ItemType.NON_INVENTORY,
            "NON-INVENTORY": ItemType.NON_INVENTORY,
            "NONINVENTORY": ItemType.NON_INVENTORY,
            "KIT": ItemType.KIT,
            "BUNDLE": ItemType.KIT,
        }
        return type_map.get(type_str.upper().replace(" ", "_"), ItemType.INVENTORY)

    def import_file(self, file_path):
        """Override to ensure categories are created first."""
        import csv
        from pathlib import Path

        file_path = Path(file_path)
        if not file_path.exists():
            self.result.add_error(0, f"File not found: {file_path}", None)
            return self.result

        with open(file_path, "r", encoding=self.config.encoding) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        # Ensure categories exist
        self._category_importer.ensure_categories(rows)
        self.db.flush()

        return super().import_rows(rows)
