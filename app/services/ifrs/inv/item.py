"""
ItemService - Inventory item master data management.

Manages items, categories, and item-level configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.ifrs.inv.item import Item, ItemType, CostingMethod
from app.models.ifrs.inv.item_category import ItemCategory
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class ItemCategoryInput:
    """Input for creating/updating an item category."""

    category_code: str
    category_name: str
    inventory_account_id: UUID
    cogs_account_id: UUID
    revenue_account_id: UUID
    inventory_adjustment_account_id: UUID
    description: Optional[str] = None
    parent_category_id: Optional[UUID] = None
    purchase_variance_account_id: Optional[UUID] = None


@dataclass
class ItemInput:
    """Input for creating an inventory item."""

    item_code: str
    item_name: str
    category_id: UUID
    base_uom: str
    currency_code: str
    item_type: ItemType = ItemType.INVENTORY
    costing_method: CostingMethod = CostingMethod.WEIGHTED_AVERAGE
    description: Optional[str] = None
    purchase_uom: Optional[str] = None
    sales_uom: Optional[str] = None
    standard_cost: Optional[Decimal] = None
    list_price: Optional[Decimal] = None
    track_inventory: bool = True
    track_lots: bool = False
    track_serial_numbers: bool = False
    reorder_point: Optional[Decimal] = None
    reorder_quantity: Optional[Decimal] = None
    minimum_stock: Optional[Decimal] = None
    maximum_stock: Optional[Decimal] = None
    lead_time_days: Optional[int] = None
    weight: Optional[Decimal] = None
    weight_uom: Optional[str] = None
    volume: Optional[Decimal] = None
    volume_uom: Optional[str] = None
    barcode: Optional[str] = None
    manufacturer_part_number: Optional[str] = None
    tax_code_id: Optional[UUID] = None
    is_taxable: bool = True
    inventory_account_id: Optional[UUID] = None
    cogs_account_id: Optional[UUID] = None
    revenue_account_id: Optional[UUID] = None
    default_supplier_id: Optional[UUID] = None
    is_purchaseable: bool = True
    is_saleable: bool = True


class ItemCategoryService(ListResponseMixin):
    """
    Service for inventory item category management.

    Manages item classifications with default GL accounts.
    """

    @staticmethod
    def create_category(
        db: Session,
        organization_id: UUID,
        input: ItemCategoryInput,
    ) -> ItemCategory:
        """
        Create a new item category.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Category input data

        Returns:
            Created ItemCategory
        """
        org_id = coerce_uuid(organization_id)

        # Check for duplicate
        existing = (
            db.query(ItemCategory)
            .filter(
                and_(
                    ItemCategory.organization_id == org_id,
                    ItemCategory.category_code == input.category_code,
                )
            )
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Category code '{input.category_code}' already exists",
            )

        category = ItemCategory(
            organization_id=org_id,
            category_code=input.category_code,
            category_name=input.category_name,
            description=input.description,
            parent_category_id=input.parent_category_id,
            inventory_account_id=input.inventory_account_id,
            cogs_account_id=input.cogs_account_id,
            revenue_account_id=input.revenue_account_id,
            inventory_adjustment_account_id=input.inventory_adjustment_account_id,
            purchase_variance_account_id=input.purchase_variance_account_id,
            is_active=True,
        )

        db.add(category)
        db.commit()
        db.refresh(category)

        return category

    @staticmethod
    def update_category(
        db: Session,
        organization_id: UUID,
        category_id: UUID,
        updates: dict[str, Any],
    ) -> ItemCategory:
        """
        Update a category's attributes.

        Args:
            db: Database session
            organization_id: Organization scope
            category_id: Category to update
            updates: Dictionary of field updates

        Returns:
            Updated ItemCategory
        """
        org_id = coerce_uuid(organization_id)
        cat_id = coerce_uuid(category_id)

        category = db.get(ItemCategory, cat_id)
        if not category or category.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item category not found")

        # Fields that cannot be updated
        immutable_fields = {"category_code", "organization_id", "category_id"}

        for key, value in updates.items():
            if key in immutable_fields:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot update field '{key}'",
                )
            if hasattr(category, key):
                setattr(category, key, value)

        db.commit()
        db.refresh(category)

        return category

    @staticmethod
    def deactivate_category(
        db: Session,
        organization_id: UUID,
        category_id: UUID,
    ) -> ItemCategory:
        """
        Deactivate a category (soft delete).

        The category cannot be deactivated if it has active items.

        Args:
            db: Database session
            organization_id: Organization scope
            category_id: Category to deactivate

        Returns:
            Updated ItemCategory

        Raises:
            HTTPException: If category not found or has active items
        """
        org_id = coerce_uuid(organization_id)
        cat_id = coerce_uuid(category_id)

        category = db.get(ItemCategory, cat_id)
        if not category or category.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item category not found")

        if not category.is_active:
            raise HTTPException(status_code=400, detail="Category is already inactive")

        # Check if category has active items
        from app.models.ifrs.inv.item import Item
        active_items_count = (
            db.query(Item)
            .filter(
                and_(
                    Item.category_id == cat_id,
                    Item.is_active.is_(True),
                )
            )
            .count()
        )

        if active_items_count > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot deactivate category with {active_items_count} active items",
            )

        category.is_active = False
        db.commit()
        db.refresh(category)

        return category

    @staticmethod
    def get(
        db: Session,
        category_id: str,
    ) -> ItemCategory:
        """Get a category by ID."""
        category = db.get(ItemCategory, coerce_uuid(category_id))
        if not category:
            raise HTTPException(status_code=404, detail="Item category not found")
        return category

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ItemCategory]:
        """List item categories."""
        query = db.query(ItemCategory)

        if organization_id:
            query = query.filter(
                ItemCategory.organization_id == coerce_uuid(organization_id)
            )

        if is_active is not None:
            query = query.filter(ItemCategory.is_active == is_active)

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (ItemCategory.category_code.ilike(search_pattern))
                | (ItemCategory.category_name.ilike(search_pattern))
            )

        query = query.order_by(ItemCategory.category_code)
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def count(
        db: Session,
        organization_id: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
    ) -> int:
        """Count item categories with filters."""
        query = db.query(ItemCategory)

        if organization_id:
            query = query.filter(
                ItemCategory.organization_id == coerce_uuid(organization_id)
            )

        if is_active is not None:
            query = query.filter(ItemCategory.is_active == is_active)

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (ItemCategory.category_code.ilike(search_pattern))
                | (ItemCategory.category_name.ilike(search_pattern))
            )

        return query.count()


class ItemService(ListResponseMixin):
    """
    Service for inventory item master data management.

    Handles item creation, updates, and stock-level queries.
    """

    @staticmethod
    def create_item(
        db: Session,
        organization_id: UUID,
        input: ItemInput,
    ) -> Item:
        """
        Create a new inventory item.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Item input data

        Returns:
            Created Item
        """
        org_id = coerce_uuid(organization_id)
        cat_id = coerce_uuid(input.category_id)

        # Check for duplicate
        existing = (
            db.query(Item)
            .filter(
                and_(
                    Item.organization_id == org_id,
                    Item.item_code == input.item_code,
                )
            )
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Item code '{input.item_code}' already exists",
            )

        # Validate category
        category = db.get(ItemCategory, cat_id)
        if not category or category.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item category not found")

        if not category.is_active:
            raise HTTPException(status_code=400, detail="Item category is not active")

        item = Item(
            organization_id=org_id,
            item_code=input.item_code,
            item_name=input.item_name,
            description=input.description,
            item_type=input.item_type,
            category_id=cat_id,
            base_uom=input.base_uom,
            purchase_uom=input.purchase_uom or input.base_uom,
            sales_uom=input.sales_uom or input.base_uom,
            costing_method=input.costing_method,
            standard_cost=input.standard_cost,
            currency_code=input.currency_code,
            list_price=input.list_price,
            track_inventory=input.track_inventory,
            track_lots=input.track_lots,
            track_serial_numbers=input.track_serial_numbers,
            reorder_point=input.reorder_point,
            reorder_quantity=input.reorder_quantity,
            minimum_stock=input.minimum_stock,
            maximum_stock=input.maximum_stock,
            lead_time_days=input.lead_time_days,
            weight=input.weight,
            weight_uom=input.weight_uom,
            volume=input.volume,
            volume_uom=input.volume_uom,
            barcode=input.barcode,
            manufacturer_part_number=input.manufacturer_part_number,
            tax_code_id=input.tax_code_id,
            is_taxable=input.is_taxable,
            inventory_account_id=input.inventory_account_id,
            cogs_account_id=input.cogs_account_id,
            revenue_account_id=input.revenue_account_id,
            default_supplier_id=input.default_supplier_id,
            is_active=True,
            is_purchaseable=input.is_purchaseable,
            is_saleable=input.is_saleable,
        )

        db.add(item)
        db.commit()
        db.refresh(item)

        return item

    @staticmethod
    def update_item(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        updates: dict[str, Any],
    ) -> Item:
        """
        Update an item's attributes.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item to update
            updates: Dictionary of field updates

        Returns:
            Updated Item
        """
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)

        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item not found")

        # Fields that cannot be updated after creation
        immutable_fields = {"item_code", "organization_id", "item_id"}

        for key, value in updates.items():
            if key in immutable_fields:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot update field '{key}'",
                )
            if hasattr(item, key):
                setattr(item, key, value)

        db.commit()
        db.refresh(item)

        return item

    @staticmethod
    def update_cost(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        new_average_cost: Optional[Decimal] = None,
        new_last_purchase_cost: Optional[Decimal] = None,
        new_standard_cost: Optional[Decimal] = None,
    ) -> Item:
        """
        Update item cost fields.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item to update
            new_average_cost: New weighted average cost
            new_last_purchase_cost: New last purchase cost
            new_standard_cost: New standard cost

        Returns:
            Updated Item
        """
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)

        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item not found")

        if new_average_cost is not None:
            item.average_cost = new_average_cost

        if new_last_purchase_cost is not None:
            item.last_purchase_cost = new_last_purchase_cost

        if new_standard_cost is not None:
            item.standard_cost = new_standard_cost

        db.commit()
        db.refresh(item)

        return item

    @staticmethod
    def deactivate_item(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
    ) -> Item:
        """Deactivate an item (soft delete)."""
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)

        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item not found")

        item.is_active = False
        db.commit()
        db.refresh(item)

        return item

    @staticmethod
    def get(
        db: Session,
        item_id: str,
    ) -> Item:
        """Get an item by ID."""
        item = db.get(Item, coerce_uuid(item_id))
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        return item

    @staticmethod
    def get_by_code(
        db: Session,
        organization_id: UUID,
        item_code: str,
    ) -> Optional[Item]:
        """Get an item by code."""
        org_id = coerce_uuid(organization_id)

        return (
            db.query(Item)
            .filter(
                and_(
                    Item.organization_id == org_id,
                    Item.item_code == item_code,
                )
            )
            .first()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        category_id: Optional[str] = None,
        item_type: Optional[ItemType] = None,
        is_active: Optional[bool] = None,
        is_purchaseable: Optional[bool] = None,
        is_saleable: Optional[bool] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Item]:
        """List items with optional filters."""
        query = db.query(Item)

        if organization_id:
            query = query.filter(Item.organization_id == coerce_uuid(organization_id))

        if category_id:
            query = query.filter(Item.category_id == coerce_uuid(category_id))

        if item_type:
            query = query.filter(Item.item_type == item_type)

        if is_active is not None:
            query = query.filter(Item.is_active == is_active)

        if is_purchaseable is not None:
            query = query.filter(Item.is_purchaseable == is_purchaseable)

        if is_saleable is not None:
            query = query.filter(Item.is_saleable == is_saleable)

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Item.item_code.ilike(search_pattern))
                | (Item.item_name.ilike(search_pattern))
                | (Item.barcode.ilike(search_pattern))
            )

        query = query.order_by(Item.item_code)
        return query.limit(limit).offset(offset).all()


# Module-level singleton instances
item_category_service = ItemCategoryService()
item_service = ItemService()
