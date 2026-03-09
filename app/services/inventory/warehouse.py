"""
WarehouseService - Warehouse and inventory location management.

Manages warehouses, locations, and inventory balances.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.inventory.inventory_transaction import (
    InventoryTransaction,
    TransactionType,
)
from app.models.inventory.item import Item
from app.models.inventory.warehouse import Warehouse
from app.models.inventory.warehouse_location import WarehouseLocation
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class WarehouseInput:
    """Input for creating/updating a warehouse."""

    warehouse_code: str
    warehouse_name: str
    description: str | None = None
    location_id: UUID | None = None
    address: dict[str, Any] | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    is_receiving: bool = True
    is_shipping: bool = True
    is_consignment: bool = False
    is_transit: bool = False
    cost_center_id: UUID | None = None


@dataclass
class WarehouseLocationInput:
    """Input for creating a warehouse location (bin/rack)."""

    warehouse_id: UUID
    location_code: str
    location_name: str
    description: str | None = None
    location_type: str | None = None
    aisle: str | None = None
    rack: str | None = None
    shelf: str | None = None
    bin: str | None = None
    is_receiving: bool = True
    is_shipping: bool = True
    is_pickable: bool = True


@dataclass
class InventoryBalance:
    """Inventory balance for an item at a location."""

    item_id: UUID
    item_code: str
    item_name: str
    warehouse_id: UUID
    warehouse_code: str
    quantity_on_hand: Decimal
    average_cost: Decimal
    total_value: Decimal
    currency_code: str


class WarehouseService(ListResponseMixin):
    """
    Service for warehouse management.

    Handles warehouse and location configuration.
    """

    @staticmethod
    def create_warehouse(
        db: Session,
        organization_id: UUID,
        input: WarehouseInput,
    ) -> Warehouse:
        """
        Create a new warehouse.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Warehouse input data

        Returns:
            Created Warehouse
        """
        org_id = coerce_uuid(organization_id)

        # Check for duplicate
        existing = (
            select(Warehouse)
            .where(Warehouse.organization_id == org_id)
            .where(Warehouse.warehouse_code == input.warehouse_code)
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Warehouse code '{input.warehouse_code}' already exists",
            )

        warehouse = Warehouse(
            organization_id=org_id,
            warehouse_code=input.warehouse_code,
            warehouse_name=input.warehouse_name,
            description=input.description,
            location_id=input.location_id,
            address=input.address,
            contact_name=input.contact_name,
            contact_phone=input.contact_phone,
            contact_email=input.contact_email,
            is_receiving=input.is_receiving,
            is_shipping=input.is_shipping,
            is_consignment=input.is_consignment,
            is_transit=input.is_transit,
            cost_center_id=input.cost_center_id,
            is_active=True,
        )

        db.add(warehouse)
        db.commit()
        db.refresh(warehouse)

        return warehouse

    @staticmethod
    def create_location(
        db: Session,
        organization_id: UUID,
        input: WarehouseLocationInput,
    ) -> WarehouseLocation:
        """
        Create a warehouse location (bin/rack).

        Args:
            db: Database session
            organization_id: Organization scope
            input: Location input data

        Returns:
            Created WarehouseLocation
        """
        org_id = coerce_uuid(organization_id)
        wh_id = coerce_uuid(input.warehouse_id)

        # Validate warehouse
        warehouse = db.get(Warehouse, wh_id)
        if not warehouse or warehouse.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        # Check for duplicate
        existing = (
            select(WarehouseLocation)
            .where(WarehouseLocation.warehouse_id == wh_id)
            .where(WarehouseLocation.location_code == input.location_code)
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Location code '{input.location_code}' already exists in this warehouse",
            )

        location = WarehouseLocation(
            warehouse_id=wh_id,
            location_code=input.location_code,
            location_name=input.location_name,
            description=input.description,
            location_type=input.location_type,
            aisle=input.aisle,
            rack=input.rack,
            shelf=input.shelf,
            bin=input.bin,
            is_receiving=input.is_receiving,
            is_shipping=input.is_shipping,
            is_pickable=input.is_pickable,
            is_active=True,
        )

        db.add(location)
        db.commit()
        db.refresh(location)

        return location

    @staticmethod
    def get_inventory_balance(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID | None = None,
    ) -> list[InventoryBalance]:
        """
        Get inventory balance for an item across warehouses.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item to check
            warehouse_id: Optional warehouse filter

        Returns:
            List of InventoryBalance by warehouse
        """
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)

        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item not found")

        # Calculate running balance from transactions
        query = (
            select(
                InventoryTransaction.warehouse_id,
                func.sum(
                    case(
                        (
                            InventoryTransaction.transaction_type.in_(
                                [
                                    TransactionType.RECEIPT,
                                    TransactionType.RETURN,
                                    TransactionType.ASSEMBLY,
                                ]
                            ),
                            InventoryTransaction.quantity,
                        ),
                        (
                            InventoryTransaction.transaction_type.in_(
                                [
                                    TransactionType.ISSUE,
                                    TransactionType.SALE,
                                    TransactionType.SCRAP,
                                    TransactionType.DISASSEMBLY,
                                ]
                            ),
                            -InventoryTransaction.quantity,
                        ),
                        (
                            InventoryTransaction.transaction_type
                            == TransactionType.ADJUSTMENT,
                            InventoryTransaction.quantity,
                        ),
                        (
                            InventoryTransaction.transaction_type
                            == TransactionType.COUNT_ADJUSTMENT,
                            InventoryTransaction.quantity,
                        ),
                        else_=Decimal("0"),
                    )
                ).label("quantity_on_hand"),
            )
            .where(
                and_(
                    InventoryTransaction.organization_id == org_id,
                    InventoryTransaction.item_id == itm_id,
                )
            )
            .group_by(InventoryTransaction.warehouse_id)
        )

        if warehouse_id:
            query = query.where(
                InventoryTransaction.warehouse_id == coerce_uuid(warehouse_id)
            )

        results = db.execute(query).all()

        balances = []
        for wh_id, qty in results:
            warehouse = db.get(Warehouse, wh_id)
            if warehouse:
                avg_cost = item.average_cost or Decimal("0")
                total_value = qty * avg_cost if qty else Decimal("0")

                balances.append(
                    InventoryBalance(
                        item_id=item.item_id,
                        item_code=item.item_code,
                        item_name=item.item_name,
                        warehouse_id=wh_id,
                        warehouse_code=warehouse.warehouse_code,
                        quantity_on_hand=qty or Decimal("0"),
                        average_cost=avg_cost,
                        total_value=total_value,
                        currency_code=item.currency_code,
                    )
                )

        return balances

    @staticmethod
    def get_warehouse_inventory(
        db: Session,
        organization_id: UUID,
        warehouse_id: UUID,
    ) -> list[InventoryBalance]:
        """
        Get all inventory in a warehouse.

        Args:
            db: Database session
            organization_id: Organization scope
            warehouse_id: Warehouse to query

        Returns:
            List of InventoryBalance for all items
        """
        org_id = coerce_uuid(organization_id)
        wh_id = coerce_uuid(warehouse_id)

        warehouse = db.get(Warehouse, wh_id)
        if not warehouse or warehouse.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        # Get distinct items in warehouse
        item_ids = db.execute(
            select(InventoryTransaction.item_id)
            .where(
                and_(
                    InventoryTransaction.organization_id == org_id,
                    InventoryTransaction.warehouse_id == wh_id,
                )
            )
            .distinct()
        ).all()

        balances = []
        for (itm_id,) in item_ids:
            item_balances = WarehouseService.get_inventory_balance(
                db, org_id, itm_id, wh_id
            )
            balances.extend(item_balances)

        return [b for b in balances if b.quantity_on_hand != Decimal("0")]

    @staticmethod
    def get(
        db: Session,
        warehouse_id: str,
        organization_id: UUID | None = None,
    ) -> Warehouse:
        """Get a warehouse by ID."""
        warehouse = db.get(Warehouse, coerce_uuid(warehouse_id))
        if not warehouse:
            raise HTTPException(status_code=404, detail="Warehouse not found")
        if organization_id is not None and warehouse.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Warehouse not found")
        return warehouse

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        is_active: bool | None = None,
        is_receiving: bool | None = None,
        is_shipping: bool | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[Warehouse]:
        """List warehouses with optional filters."""
        query = select(Warehouse)

        if organization_id:
            query = query.where(
                Warehouse.organization_id == coerce_uuid(organization_id)
            )

        if is_active is not None:
            query = query.where(Warehouse.is_active == is_active)

        if is_receiving is not None:
            query = query.where(Warehouse.is_receiving == is_receiving)

        if is_shipping is not None:
            query = query.where(Warehouse.is_shipping == is_shipping)

        if search:
            search_pattern = f"%{search}%"
            query = query.where(
                or_(
                    Warehouse.warehouse_code.ilike(search_pattern),
                    Warehouse.warehouse_name.ilike(search_pattern),
                )
            )

        return (
            query.order_by(Warehouse.warehouse_code).limit(limit).offset(offset).all()
        )

    @staticmethod
    def list_locations(
        db: Session,
        warehouse_id: str,
        is_active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> builtins.list[WarehouseLocation]:
        """List locations in a warehouse."""
        wh_id = coerce_uuid(warehouse_id)

        query = select(WarehouseLocation).where(
            WarehouseLocation.warehouse_id == wh_id
        )

        if is_active is not None:
            query = query.where(WarehouseLocation.is_active == is_active)

        return (
            query.order_by(WarehouseLocation.location_code)
            .limit(limit)
            .offset(offset)
            .all()
        )

    @staticmethod
    def update_warehouse(
        db: Session,
        organization_id: UUID,
        warehouse_id: UUID,
        updates: dict[str, Any],
    ) -> Warehouse:
        """
        Update a warehouse's attributes.

        Args:
            db: Database session
            organization_id: Organization scope
            warehouse_id: Warehouse to update
            updates: Dictionary of field updates

        Returns:
            Updated Warehouse
        """
        org_id = coerce_uuid(organization_id)
        wh_id = coerce_uuid(warehouse_id)

        warehouse = db.get(Warehouse, wh_id)
        if not warehouse or warehouse.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        # Fields that cannot be updated
        immutable_fields = {"warehouse_code", "organization_id", "warehouse_id"}

        for key, value in updates.items():
            if key in immutable_fields:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot update field '{key}'",
                )
            if hasattr(warehouse, key):
                setattr(warehouse, key, value)

        db.commit()
        db.refresh(warehouse)

        return warehouse

    @staticmethod
    def count(
        db: Session,
        organization_id: str | None = None,
        is_active: bool | None = None,
        search: str | None = None,
    ) -> int:
        """Count warehouses with filters."""
        query = select(Warehouse)

        if organization_id:
            query = query.where(
                Warehouse.organization_id == coerce_uuid(organization_id)
            )

        if is_active is not None:
            query = query.where(Warehouse.is_active == is_active)

        if search:
            search_pattern = f"%{search}%"
            query = query.where(
                or_(
                    Warehouse.warehouse_code.ilike(search_pattern),
                    Warehouse.warehouse_name.ilike(search_pattern),
                )
            )

        return db.scalar(select(func.count()).select_from(query.subquery())) or 0

    @staticmethod
    def deactivate_warehouse(
        db: Session,
        organization_id: UUID,
        warehouse_id: UUID,
    ) -> Warehouse:
        """
        Deactivate a warehouse (soft delete).

        The warehouse cannot be deactivated if it has inventory.

        Args:
            db: Database session
            organization_id: Organization scope
            warehouse_id: Warehouse to deactivate

        Returns:
            Updated Warehouse

        Raises:
            HTTPException: If warehouse not found or has inventory
        """
        org_id = coerce_uuid(organization_id)
        wh_id = coerce_uuid(warehouse_id)

        warehouse = db.get(Warehouse, wh_id)
        if not warehouse or warehouse.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        if not warehouse.is_active:
            raise HTTPException(status_code=400, detail="Warehouse is already inactive")

        # Check if warehouse has inventory
        inventory = WarehouseService.get_warehouse_inventory(db, org_id, wh_id)
        if inventory:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot deactivate warehouse with {len(inventory)} items in stock",
            )

        warehouse.is_active = False
        db.commit()
        db.refresh(warehouse)

        return warehouse

    @staticmethod
    def deactivate_location(
        db: Session,
        organization_id: UUID,
        location_id: UUID,
    ) -> WarehouseLocation:
        """
        Deactivate a warehouse location (soft delete).

        Args:
            db: Database session
            organization_id: Organization scope
            location_id: Location to deactivate

        Returns:
            Updated WarehouseLocation

        Raises:
            HTTPException: If location not found
        """
        org_id = coerce_uuid(organization_id)
        loc_id = coerce_uuid(location_id)

        location = db.get(WarehouseLocation, loc_id)
        if not location:
            raise HTTPException(status_code=404, detail="Location not found")

        # Validate warehouse belongs to org
        warehouse = db.get(Warehouse, location.warehouse_id)
        if not warehouse or warehouse.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Location not found")

        if not location.is_active:
            raise HTTPException(status_code=400, detail="Location is already inactive")

        location.is_active = False
        db.commit()
        db.refresh(location)

        return location


# Module-level singleton instance
warehouse_service = WarehouseService()
