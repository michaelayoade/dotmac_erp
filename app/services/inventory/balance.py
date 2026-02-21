"""
InventoryBalanceService - Stock level calculations.

Computes on-hand, reserved, and available inventory from transactions.
Provides single source of truth for inventory quantities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.inventory.inventory_lot import InventoryLot
from app.models.inventory.inventory_transaction import (
    InventoryTransaction,
    TransactionType,
)
from app.models.inventory.item import CostingMethod, Item
from app.models.inventory.item_category import ItemCategory
from app.models.inventory.warehouse import Warehouse
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


@dataclass
class InventoryBalance:
    """Inventory balance for an item at a location."""

    item_id: UUID
    item_code: str
    item_name: str
    warehouse_id: UUID | None
    warehouse_code: str | None
    quantity_on_hand: Decimal
    quantity_reserved: Decimal
    quantity_available: Decimal
    average_cost: Decimal
    total_value: Decimal


@dataclass
class ItemStockSummary:
    """Summary of stock levels across all warehouses."""

    item_id: UUID
    item_code: str
    item_name: str
    total_on_hand: Decimal
    total_reserved: Decimal
    total_available: Decimal
    reorder_point: Decimal | None
    minimum_stock: Decimal | None
    maximum_stock: Decimal | None
    below_reorder: bool
    below_minimum: bool
    above_maximum: bool
    warehouses: list[InventoryBalance]


@dataclass
class LowStockItem:
    """Item with low stock alert."""

    item_id: UUID
    item_code: str
    item_name: str
    quantity_on_hand: Decimal
    quantity_available: Decimal
    reorder_point: Decimal
    reorder_quantity: Decimal | None
    suggested_order_qty: Decimal
    default_supplier_id: UUID | None
    lead_time_days: int | None


class InventoryBalanceService:
    """
    Service for computing inventory balances.

    Calculates on-hand from transactions and reserved from allocations.
    """

    @staticmethod
    def get_on_hand(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID | None = None,
    ) -> Decimal:
        """
        Calculate on-hand quantity from transactions.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item to check
            warehouse_id: Optional warehouse filter

        Returns:
            Quantity on hand
        """
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)

        # Build transaction sum query
        query = select(
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
                    else_=InventoryTransaction.quantity,
                )
            )
        ).where(
            and_(
                InventoryTransaction.organization_id == org_id,
                InventoryTransaction.item_id == itm_id,
            )
        )

        if warehouse_id:
            query = query.where(
                InventoryTransaction.warehouse_id == coerce_uuid(warehouse_id)
            )

        result = db.scalar(query)
        return result or Decimal("0")

    @staticmethod
    def get_reserved(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID | None = None,
    ) -> Decimal:
        """
        Calculate reserved quantity from lot allocations.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item to check
            warehouse_id: Optional warehouse filter (uses lot warehouse when available)

        Returns:
            Quantity reserved
        """
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)

        # Sum allocated quantities from lots
        query = select(func.sum(InventoryLot.quantity_allocated)).where(
            and_(
                InventoryLot.organization_id == org_id,
                InventoryLot.item_id == itm_id,
                InventoryLot.is_active == True,
            )
        )

        if warehouse_id:
            wh_id = coerce_uuid(warehouse_id)
            query = query.where(
                or_(
                    InventoryLot.warehouse_id == wh_id,
                    InventoryLot.warehouse_id.is_(None),
                )
            )

        result = db.scalar(query)

        return result or Decimal("0")

    @staticmethod
    def get_available(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID | None = None,
    ) -> Decimal:
        """
        Calculate available quantity (on-hand minus reserved).

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item to check
            warehouse_id: Optional warehouse filter

        Returns:
            Quantity available
        """
        on_hand = InventoryBalanceService.get_on_hand(
            db, organization_id, item_id, warehouse_id
        )
        reserved = InventoryBalanceService.get_reserved(
            db, organization_id, item_id, warehouse_id
        )
        return on_hand - reserved

    @staticmethod
    def get_item_balance(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID | None = None,
    ) -> InventoryBalance | None:
        """
        Get full inventory balance for an item.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item to check
            warehouse_id: Optional warehouse filter

        Returns:
            InventoryBalance or None if item not found
        """
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)

        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            return None

        warehouse = None
        if warehouse_id:
            wh_id = coerce_uuid(warehouse_id)
            warehouse = db.get(Warehouse, wh_id)

        on_hand = InventoryBalanceService.get_on_hand(db, org_id, itm_id, warehouse_id)
        reserved = InventoryBalanceService.get_reserved(
            db, org_id, itm_id, warehouse_id
        )
        available = on_hand - reserved

        avg_cost = item.average_cost or Decimal("0")
        total_value = on_hand * avg_cost

        return InventoryBalance(
            item_id=item.item_id,
            item_code=item.item_code,
            item_name=item.item_name,
            warehouse_id=warehouse.warehouse_id if warehouse else None,
            warehouse_code=warehouse.warehouse_code if warehouse else None,
            quantity_on_hand=on_hand,
            quantity_reserved=reserved,
            quantity_available=available,
            average_cost=avg_cost,
            total_value=total_value,
        )

    @staticmethod
    def get_item_stock_summary(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
    ) -> ItemStockSummary | None:
        """
        Get stock summary across all warehouses for an item.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item to check

        Returns:
            ItemStockSummary with warehouse breakdown
        """
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)

        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            return None

        category = db.get(ItemCategory, item.category_id) if item.category_id else None

        # Get all warehouses with inventory for this item
        warehouse_ids = (
            db.query(InventoryTransaction.warehouse_id)
            .filter(
                and_(
                    InventoryTransaction.organization_id == org_id,
                    InventoryTransaction.item_id == itm_id,
                )
            )
            .all()
        )

        warehouse_balances = []
        total_on_hand = Decimal("0")
        total_reserved = Decimal("0")

        for (wh_id,) in warehouse_ids:
            balance = InventoryBalanceService.get_item_balance(
                db, org_id, itm_id, wh_id
            )
            if balance:
                warehouse_balances.append(balance)
                total_on_hand += balance.quantity_on_hand
                total_reserved += balance.quantity_reserved

        total_available = total_on_hand - total_reserved

        # Check stock alerts
        effective_reorder_point = (
            item.reorder_point
            if item.reorder_point is not None
            else (category.reorder_point if category else None)
        )
        effective_minimum_stock = (
            item.minimum_stock
            if item.minimum_stock is not None
            else (category.minimum_stock if category else None)
        )

        reorder_point = effective_reorder_point or Decimal("0")
        minimum_stock = effective_minimum_stock or Decimal("0")
        maximum_stock = item.maximum_stock

        return ItemStockSummary(
            item_id=item.item_id,
            item_code=item.item_code,
            item_name=item.item_name,
            total_on_hand=total_on_hand,
            total_reserved=total_reserved,
            total_available=total_available,
            reorder_point=effective_reorder_point,
            minimum_stock=effective_minimum_stock,
            maximum_stock=item.maximum_stock,
            below_reorder=total_available <= reorder_point if reorder_point else False,
            below_minimum=total_available < minimum_stock if minimum_stock else False,
            above_maximum=total_on_hand > maximum_stock if maximum_stock else False,
            warehouses=warehouse_balances,
        )

    @staticmethod
    def get_low_stock_items(
        db: Session,
        organization_id: UUID,
        include_below_minimum: bool = True,
    ) -> list[LowStockItem]:
        """
        Get items that are at or below reorder point.

        Args:
            db: Database session
            organization_id: Organization scope
            include_below_minimum: Also include items below minimum stock

        Returns:
            List of LowStockItem objects
        """
        org_id = coerce_uuid(organization_id)

        stock_filters = [
            and_(
                Item.reorder_point.isnot(None),
                Item.reorder_point > 0,
            ),
            and_(
                Item.reorder_point.is_(None),
                ItemCategory.reorder_point.isnot(None),
                ItemCategory.reorder_point > 0,
            ),
        ]
        if include_below_minimum:
            stock_filters.extend(
                [
                    and_(
                        Item.minimum_stock.isnot(None),
                        Item.minimum_stock > 0,
                    ),
                    and_(
                        Item.minimum_stock.is_(None),
                        ItemCategory.minimum_stock.isnot(None),
                        ItemCategory.minimum_stock > 0,
                    ),
                ]
            )

        # Get items with reorder point or minimum stock (item-level or category fallback)
        items = (
            db.query(Item, ItemCategory)
            .join(ItemCategory, Item.category_id == ItemCategory.category_id)
            .filter(
                and_(
                    Item.organization_id == org_id,
                    Item.is_active == True,
                    Item.track_inventory == True,
                    or_(*stock_filters),
                )
            )
            .all()
        )

        low_stock = []
        for item, category in items:
            on_hand = InventoryBalanceService.get_on_hand(db, org_id, item.item_id)
            reserved = InventoryBalanceService.get_reserved(db, org_id, item.item_id)
            available = on_hand - reserved

            effective_reorder_point = (
                item.reorder_point
                if item.reorder_point is not None
                else (category.reorder_point if category else None)
            )
            effective_minimum_stock = (
                item.minimum_stock
                if item.minimum_stock is not None
                else (category.minimum_stock if category else None)
            )

            reorder_point = (
                cast(Decimal, effective_reorder_point)
                if effective_reorder_point is not None
                else None
            )
            minimum_stock = (
                cast(Decimal, effective_minimum_stock)
                if effective_minimum_stock is not None
                else None
            )

            is_low = False
            if reorder_point is not None:
                is_low = available <= reorder_point
            if include_below_minimum and minimum_stock is not None:
                is_low = is_low or available < minimum_stock

            if is_low:
                # Calculate suggested order quantity
                reorder_qty = item.reorder_quantity or Decimal("0")
                basis_point = reorder_point or minimum_stock or Decimal("0")
                max_stock = item.maximum_stock or (basis_point * 2)
                suggested_qty = max(reorder_qty, max_stock - on_hand)

                low_stock.append(
                    LowStockItem(
                        item_id=item.item_id,
                        item_code=item.item_code,
                        item_name=item.item_name,
                        quantity_on_hand=on_hand,
                        quantity_available=available,
                        reorder_point=basis_point,
                        reorder_quantity=item.reorder_quantity,
                        suggested_order_qty=suggested_qty
                        if suggested_qty > 0
                        else reorder_qty,
                        default_supplier_id=item.default_supplier_id,
                        lead_time_days=int(item.lead_time_days)
                        if item.lead_time_days
                        else None,
                    )
                )

        return low_stock

    @staticmethod
    def get_batch_stock_levels(
        db: Session,
        organization_id: UUID,
        item_ids: list[UUID],
        warehouse_id: UUID | None = None,
    ) -> dict[UUID, tuple[Decimal, Decimal]]:
        """
        Get on-hand and reserved quantities for multiple items in 2 queries.

        Args:
            db: Database session
            organization_id: Organization scope
            item_ids: Items to check
            warehouse_id: Optional warehouse filter

        Returns:
            Dict mapping item_id -> (on_hand, reserved)
        """
        if not item_ids:
            return {}

        org_id = coerce_uuid(organization_id)

        # Query 1: on-hand per item
        on_hand_expr = func.sum(
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
                else_=InventoryTransaction.quantity,
            )
        )

        on_hand_query = select(InventoryTransaction.item_id, on_hand_expr).where(
            and_(
                InventoryTransaction.organization_id == org_id,
                InventoryTransaction.item_id.in_(item_ids),
            )
        )
        if warehouse_id:
            on_hand_query = on_hand_query.where(
                InventoryTransaction.warehouse_id == coerce_uuid(warehouse_id)
            )
        on_hand_query = on_hand_query.group_by(InventoryTransaction.item_id)

        on_hand_map: dict[UUID, Decimal] = {
            row[0]: row[1] or Decimal("0") for row in db.execute(on_hand_query).all()
        }

        # Query 2: reserved per item
        reserved_query = select(
            InventoryLot.item_id,
            func.sum(InventoryLot.quantity_allocated),
        ).where(
            and_(
                InventoryLot.organization_id == org_id,
                InventoryLot.item_id.in_(item_ids),
                InventoryLot.is_active == True,
            )
        )
        if warehouse_id:
            wh_id = coerce_uuid(warehouse_id)
            reserved_query = reserved_query.where(
                or_(
                    InventoryLot.warehouse_id == wh_id,
                    InventoryLot.warehouse_id.is_(None),
                )
            )
        reserved_query = reserved_query.group_by(InventoryLot.item_id)

        reserved_map: dict[UUID, Decimal] = {
            row[0]: row[1] or Decimal("0") for row in db.execute(reserved_query).all()
        }

        # Merge into result
        result: dict[UUID, tuple[Decimal, Decimal]] = {}
        for iid in item_ids:
            result[iid] = (
                on_hand_map.get(iid, Decimal("0")),
                reserved_map.get(iid, Decimal("0")),
            )
        return result

    @staticmethod
    def get_warehouse_inventory(
        db: Session,
        organization_id: UUID,
        warehouse_id: UUID,
    ) -> list[InventoryBalance]:
        """
        Get all inventory balances for a warehouse.

        Args:
            db: Database session
            organization_id: Organization scope
            warehouse_id: Warehouse to query

        Returns:
            List of InventoryBalance for items with inventory
        """
        org_id = coerce_uuid(organization_id)
        wh_id = coerce_uuid(warehouse_id)

        # Get all items with transactions at this warehouse
        item_ids = (
            db.query(InventoryTransaction.item_id)
            .filter(
                and_(
                    InventoryTransaction.organization_id == org_id,
                    InventoryTransaction.warehouse_id == wh_id,
                )
            )
            .all()
        )

        balances = []
        for (item_id,) in item_ids:
            balance = InventoryBalanceService.get_item_balance(
                db, org_id, item_id, wh_id
            )
            if balance and balance.quantity_on_hand != 0:
                balances.append(balance)

        return balances

    @staticmethod
    def allocate_inventory(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        quantity: Decimal,
        reference_type: str,
        reference_id: UUID,
        warehouse_id: UUID | None = None,
        lot_id: UUID | None = None,
    ) -> bool:
        """
        Allocate (reserve) inventory for a sales order or other document.

        For lot-tracked items, allocates from specific lot.
        For non-lot items, creates a synthetic allocation record.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item to allocate
            quantity: Quantity to allocate
            reference_type: Type of document (e.g., "SALES_ORDER")
            reference_id: ID of the referencing document
            warehouse_id: Optional warehouse
            lot_id: Specific lot for lot-tracked items

        Returns:
            True if allocation succeeded
        """
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)

        # Check available quantity
        available = InventoryBalanceService.get_available(
            db, org_id, itm_id, warehouse_id
        )

        if available < quantity:
            return False

        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            return False

        # For lot-tracked items, allocate from lot
        if (
            item.track_lots or item.costing_method == CostingMethod.FIFO
        ) and not lot_id:
            return False

        if (item.track_lots or item.costing_method == CostingMethod.FIFO) and lot_id:
            lot = db.get(InventoryLot, coerce_uuid(lot_id))
            if not lot or lot.item_id != itm_id:
                return False
            if hasattr(lot, "_sa_instance_state"):
                lot_org_id = getattr(lot, "organization_id", None)
                if lot_org_id and lot_org_id != org_id:
                    return False

            if lot.quantity_available < quantity:
                return False

            lot.quantity_allocated = (lot.quantity_allocated or Decimal("0")) + quantity
            lot.allocation_reference = f"{reference_type}:{reference_id}"
            lot.quantity_available = lot.quantity_on_hand - (
                lot.quantity_allocated or Decimal("0")
            )
            db.commit()

        # For non-lot items, we track in lots table as a general allocation
        # This could also be done with a separate allocation table
        else:
            # Find or create a "general" lot for this item to track allocations
            lot_number = "__GENERAL__"
            if warehouse_id:
                lot_number = f"__GENERAL__:{warehouse_id}"

            general_lot = db.scalar(
                select(InventoryLot).where(
                    and_(
                        InventoryLot.organization_id == org_id,
                        InventoryLot.item_id == itm_id,
                        InventoryLot.lot_number == lot_number,
                    )
                )
            )

            if not general_lot:
                from datetime import date as date_type

                general_lot = InventoryLot(
                    organization_id=org_id,
                    item_id=itm_id,
                    lot_number=lot_number,
                    warehouse_id=coerce_uuid(warehouse_id) if warehouse_id else None,
                    received_date=date_type.today(),
                    unit_cost=item.average_cost or Decimal("0"),
                    initial_quantity=Decimal(
                        "0"
                    ),  # Not actual inventory, just allocation tracking
                    quantity_on_hand=Decimal("0"),
                    quantity_allocated=Decimal("0"),
                    quantity_available=Decimal("0"),
                    is_active=True,
                )
                db.add(general_lot)

            general_lot.quantity_allocated = (
                general_lot.quantity_allocated or Decimal("0")
            ) + quantity
            general_lot.quantity_available = general_lot.quantity_on_hand - (
                general_lot.quantity_allocated or Decimal("0")
            )
            db.commit()

        return True

    @staticmethod
    def deallocate_inventory(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        quantity: Decimal,
        lot_id: UUID | None = None,
        warehouse_id: UUID | None = None,
    ) -> bool:
        """
        Release an allocation.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item to deallocate
            quantity: Quantity to release
            lot_id: Specific lot for lot-tracked items

        Returns:
            True if deallocation succeeded
        """
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)

        if lot_id:
            lot = db.get(InventoryLot, coerce_uuid(lot_id))
            if not lot or lot.item_id != itm_id:
                return False
            if hasattr(lot, "_sa_instance_state"):
                lot_org_id = getattr(lot, "organization_id", None)
                if lot_org_id and lot_org_id != org_id:
                    return False

            current_allocated = lot.quantity_allocated or Decimal("0")
            lot.quantity_allocated = max(Decimal("0"), current_allocated - quantity)
            lot.quantity_available = lot.quantity_on_hand - lot.quantity_allocated
            db.commit()
        else:
            # Deallocate from general lot
            lot_number = "__GENERAL__"
            if warehouse_id:
                lot_number = f"__GENERAL__:{warehouse_id}"
            general_lot = db.scalar(
                select(InventoryLot).where(
                    and_(
                        InventoryLot.organization_id == org_id,
                        InventoryLot.item_id == itm_id,
                        InventoryLot.lot_number == lot_number,
                    )
                )
            )

            if general_lot:
                current_allocated = general_lot.quantity_allocated or Decimal("0")
                general_lot.quantity_allocated = max(
                    Decimal("0"), current_allocated - quantity
                )
                general_lot.quantity_available = (
                    general_lot.quantity_on_hand - general_lot.quantity_allocated
                )
                db.commit()

        return True


# Module-level singleton instance
inventory_balance_service = InventoryBalanceService()
