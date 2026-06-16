"""Inventory item, category and warehouse views for CRM.

Extracted from the former monolithic dotmac_crm_sync_service.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select

from app.config import settings

if TYPE_CHECKING:
    from app.models.finance.ap.supplier import Supplier  # noqa: F401

from app.schemas.sync.dotmac_crm import (
    CRMInventoryItemPayload,
    CRMInventoryItemResponse,
    InventoryItemDetail,
    InventoryItemStock,
    InventoryListResponse,
    WarehouseStock,
)

# CRM → ERP translation policy lives in crm_mappings (pure, side-effect-free).
# Re-imported here so the canonical import sites
# (`from ...dotmac_crm_sync_service import PROJECT_STATUS_MAP`) and the in-class
# references keep resolving against this module's namespace.

from app.services.sync.crm.base import _CRMSyncBase

logger = logging.getLogger(__name__)


class _InventoryMixin(_CRMSyncBase):
    def upsert_inventory_item(
        self,
        org_id: UUID,
        data: CRMInventoryItemPayload,
    ) -> CRMInventoryItemResponse:
        """Create or update an ERP inventory item from CRM payload."""
        from app.models.inventory.item import CostingMethod, Item, ItemType
        from app.models.inventory.item_category import ItemCategory

        item_code = (data.item_code or "").strip()
        item_name = (data.item_name or "").strip()
        if not item_code:
            raise ValueError("item_code is required")
        if not item_name:
            raise ValueError("item_name is required")

        category: ItemCategory | None
        if data.category_code:
            category = self.db.scalar(
                select(ItemCategory).where(
                    ItemCategory.organization_id == org_id,
                    ItemCategory.category_code == data.category_code,
                    ItemCategory.is_active.is_(True),
                )
            )
            if not category:
                raise ValueError(f"Item category not found: {data.category_code}")
        else:
            category = self.db.scalar(
                select(ItemCategory)
                .where(
                    ItemCategory.organization_id == org_id,
                    ItemCategory.is_active.is_(True),
                )
                .order_by(ItemCategory.category_code.asc())
            )
            if not category:
                raise ValueError(
                    "No active item category available in ERP for this organization"
                )
        if category is None:
            raise ValueError("No item category available for CRM item sync")

        item = self.db.scalar(
            select(Item).where(
                Item.organization_id == org_id,
                Item.item_code == item_code,
            )
        )

        if item:
            item.item_name = item_name
            item.description = data.description
            if data.category_code:
                item.category_id = category.category_id
            item.base_uom = (data.base_uom or "EA").strip().upper()
            item.purchase_uom = item.base_uom
            item.sales_uom = item.base_uom
            item.currency_code = (
                (data.currency_code or settings.default_functional_currency_code)
                .strip()
                .upper()
            )
            item.list_price = data.list_price
            item.reorder_point = data.reorder_point
            item.barcode = data.barcode
            item.is_active = data.is_active
            # Reuse sync-tracking fields for CRM source correlation.
            item.erpnext_id = data.crm_id
            item.last_synced_at = datetime.now(UTC)
            status = "updated"
        else:
            item = Item(
                organization_id=org_id,
                item_code=item_code,
                item_name=item_name,
                description=data.description,
                item_type=ItemType.INVENTORY,
                category_id=category.category_id,
                base_uom=(data.base_uom or "EA").strip().upper(),
                purchase_uom=(data.base_uom or "EA").strip().upper(),
                sales_uom=(data.base_uom or "EA").strip().upper(),
                costing_method=CostingMethod.WEIGHTED_AVERAGE,
                currency_code=(
                    data.currency_code or settings.default_functional_currency_code
                )
                .strip()
                .upper(),
                list_price=data.list_price,
                reorder_point=data.reorder_point,
                barcode=data.barcode,
                track_inventory=True,
                is_active=data.is_active,
                is_purchaseable=True,
                is_saleable=True,
                erpnext_id=data.crm_id,
                last_synced_at=datetime.now(UTC),
            )
            self.db.add(item)
            status = "created"

        self.db.flush()
        logger.info(
            "Upserted CRM inventory item crm_id=%s item_code=%s status=%s",
            data.crm_id,
            item.item_code,
            status,
        )
        return CRMInventoryItemResponse(
            item_id=item.item_id,
            item_code=item.item_code,
            status=status,
            crm_id=data.crm_id,
        )

    def list_inventory_items(
        self,
        org_id: UUID,
        search: str | None = None,
        category_code: str | None = None,
        warehouse_id: UUID | None = None,
        include_zero_stock: bool = False,
        only_below_reorder: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> InventoryListResponse:
        """
        List inventory items with current stock levels for CRM.

        Uses batch stock loading (2 queries) instead of per-item queries.

        Args:
            org_id: Organization ID
            search: Search term for item code/name
            category_code: Filter by category code
            warehouse_id: Filter by specific warehouse
            include_zero_stock: Include items with zero stock (default: False)
            only_below_reorder: Only show items below reorder point
            limit: Max items to return
            offset: Pagination offset

        Returns:
            InventoryListResponse with items and pagination info
        """
        from app.models.inventory.item import Item
        from app.models.inventory.item_category import ItemCategory
        from app.services.inventory.balance import InventoryBalanceService

        # Build base query for active inventory items
        stmt = (
            select(Item, ItemCategory)
            .outerjoin(ItemCategory, Item.category_id == ItemCategory.category_id)
            .where(
                Item.organization_id == org_id,
                Item.is_active.is_(True),
            )
        )

        if search:
            search_filter = f"%{search}%"
            stmt = stmt.where(
                (Item.item_code.ilike(search_filter))
                | (Item.item_name.ilike(search_filter))
                | (Item.barcode.ilike(search_filter))
            )

        if category_code:
            stmt = stmt.where(ItemCategory.category_code == category_code)

        # Fast path: no stock-level filtering needed
        if include_zero_stock and not only_below_reorder:
            count_stmt = select(func.count()).select_from(
                stmt.with_only_columns(Item.item_id).subquery()
            )
            total_count = self.db.scalar(count_stmt) or 0

            stmt = stmt.order_by(Item.item_code).offset(offset).limit(limit + 1)
            results = self.db.execute(stmt).all()

            has_more = len(results) > limit
            if has_more:
                results = results[:limit]

            # Batch-load stock levels (2 queries instead of 2*N)
            item_ids = [item.item_id for item, _cat in results]
            stock_map = InventoryBalanceService.get_batch_stock_levels(
                self.db, org_id, item_ids, warehouse_id
            )

            items = self._build_stock_items(list(results), stock_map)
            return InventoryListResponse(
                items=items, total_count=total_count, has_more=has_more
            )

        # Filtered path: need stock levels to filter, process in batches
        all_qualified: list[InventoryItemStock] = []
        batch_size = 500
        current_offset = 0

        while True:
            page_stmt = (
                stmt.order_by(Item.item_code).offset(current_offset).limit(batch_size)
            )
            results = self.db.execute(page_stmt).all()
            if not results:
                break

            # Batch-load stock for this page (2 queries per batch)
            item_ids = [item.item_id for item, _cat in results]
            stock_map = InventoryBalanceService.get_batch_stock_levels(
                self.db, org_id, item_ids, warehouse_id
            )

            for item, category in results:
                on_hand, reserved = stock_map.get(
                    item.item_id, (Decimal("0"), Decimal("0"))
                )
                available = on_hand - reserved

                if not include_zero_stock and available <= 0:
                    continue

                reorder_point = item.reorder_point or Decimal("0")
                is_below = available <= reorder_point if reorder_point else False
                if only_below_reorder and not is_below:
                    continue

                all_qualified.append(
                    InventoryItemStock(
                        item_id=item.item_id,
                        item_code=item.item_code,
                        item_name=item.item_name,
                        description=item.description,
                        category_code=category.category_code if category else None,
                        category_name=category.category_name if category else None,
                        base_uom=item.base_uom,
                        quantity_on_hand=on_hand,
                        quantity_reserved=reserved,
                        quantity_available=available,
                        reorder_point=item.reorder_point,
                        list_price=item.list_price,
                        currency_code=item.currency_code,
                        barcode=item.barcode,
                        is_below_reorder=is_below,
                    )
                )

            current_offset += batch_size

        total_count = len(all_qualified)
        page_items = all_qualified[offset : offset + limit]
        has_more = (offset + limit) < total_count

        return InventoryListResponse(
            items=page_items, total_count=total_count, has_more=has_more
        )

    def _build_stock_items(
        self,
        results: list,
        stock_map: dict[UUID, tuple[Decimal, Decimal]],
    ) -> list[InventoryItemStock]:
        """Build InventoryItemStock list from query results and batch stock data."""
        items: list[InventoryItemStock] = []
        for item, category in results:
            on_hand, reserved = stock_map.get(
                item.item_id, (Decimal("0"), Decimal("0"))
            )
            available = on_hand - reserved
            reorder_point = item.reorder_point or Decimal("0")

            items.append(
                InventoryItemStock(
                    item_id=item.item_id,
                    item_code=item.item_code,
                    item_name=item.item_name,
                    description=item.description,
                    category_code=category.category_code if category else None,
                    category_name=category.category_name if category else None,
                    base_uom=item.base_uom,
                    quantity_on_hand=on_hand,
                    quantity_reserved=reserved,
                    quantity_available=available,
                    reorder_point=item.reorder_point,
                    list_price=item.list_price,
                    currency_code=item.currency_code,
                    barcode=item.barcode,
                    is_below_reorder=(
                        available <= reorder_point if reorder_point else False
                    ),
                )
            )
        return items

    def get_inventory_item_detail(
        self,
        org_id: UUID,
        item_id: UUID,
    ) -> InventoryItemDetail | None:
        """
        Get detailed inventory item info with warehouse breakdown.

        Args:
            org_id: Organization ID
            item_id: Item ID to retrieve

        Returns:
            InventoryItemDetail with warehouse-level stock, or None if not found
        """
        from app.models.inventory.inventory_serial import InventorySerial
        from app.models.inventory.item import Item
        from app.models.inventory.item_category import ItemCategory
        from app.services.inventory.balance import InventoryBalanceService

        # Get item
        item = self.db.get(Item, item_id)
        if not item or item.organization_id != org_id:
            return None

        # Get category
        category = (
            self.db.get(ItemCategory, item.category_id) if item.category_id else None
        )

        # Get stock summary with warehouse breakdown
        summary = InventoryBalanceService.get_item_stock_summary(
            self.db, org_id, item_id
        )

        serials_by_warehouse: dict[UUID, list[str]] = {}
        if item.track_serial_numbers:
            serial_rows = self.db.execute(
                select(InventorySerial.warehouse_id, InventorySerial.serial_number)
                .where(
                    InventorySerial.organization_id == org_id,
                    InventorySerial.item_id == item_id,
                    InventorySerial.is_active.is_(True),
                    InventorySerial.status == "AVAILABLE",
                    InventorySerial.warehouse_id.is_not(None),
                )
                .order_by(
                    InventorySerial.warehouse_id.asc(),
                    InventorySerial.serial_number.asc(),
                )
            ).all()
            for warehouse_id, serial_number in serial_rows:
                if warehouse_id is None:
                    continue
                serials_by_warehouse.setdefault(warehouse_id, []).append(serial_number)

        warehouse_stocks: list[WarehouseStock] = []
        if summary:
            for wh_balance in summary.warehouses:
                if wh_balance.warehouse_id:
                    warehouse_stocks.append(
                        WarehouseStock(
                            warehouse_id=wh_balance.warehouse_id,
                            warehouse_code=wh_balance.warehouse_code or "",
                            warehouse_name=(
                                getattr(wh_balance, "warehouse_name", None)
                                or wh_balance.warehouse_code
                                or ""
                            ),
                            quantity_on_hand=wh_balance.quantity_on_hand,
                            quantity_reserved=wh_balance.quantity_reserved,
                            quantity_available=wh_balance.quantity_available,
                            serial_numbers=serials_by_warehouse.get(
                                wh_balance.warehouse_id, []
                            ),
                        )
                    )

        total_on_hand = summary.total_on_hand if summary else Decimal("0")
        total_reserved = summary.total_reserved if summary else Decimal("0")
        total_available = summary.total_available if summary else Decimal("0")

        return InventoryItemDetail(
            item_id=item.item_id,
            item_code=item.item_code,
            item_name=item.item_name,
            description=item.description,
            category_code=category.category_code if category else None,
            category_name=category.category_name if category else None,
            base_uom=item.base_uom,
            total_on_hand=total_on_hand,
            total_reserved=total_reserved,
            total_available=total_available,
            reorder_point=item.reorder_point,
            list_price=item.list_price,
            currency_code=item.currency_code,
            barcode=item.barcode,
            track_serial_numbers=item.track_serial_numbers,
            warehouses=warehouse_stocks,
        )

    def get_categories(self, org_id: UUID) -> list[dict]:
        """
        Get list of item categories for filtering.

        Returns:
            List of {code, name} dicts
        """
        from app.models.inventory.item_category import ItemCategory

        stmt = (
            select(ItemCategory.category_code, ItemCategory.category_name)
            .where(
                ItemCategory.organization_id == org_id,
                ItemCategory.is_active.is_(True),
            )
            .order_by(ItemCategory.category_name)
        )
        results = self.db.execute(stmt).all()
        return [{"code": code, "name": name} for code, name in results]

    def get_warehouses(self, org_id: UUID) -> list[dict]:
        """
        Get list of warehouses for filtering.

        Returns:
            List of {warehouse_id, code, name} dicts
        """
        from app.models.inventory.warehouse import Warehouse

        stmt = (
            select(
                Warehouse.warehouse_id,
                Warehouse.warehouse_code,
                Warehouse.warehouse_name,
            )
            .where(
                Warehouse.organization_id == org_id,
                Warehouse.is_active.is_(True),
            )
            .order_by(Warehouse.warehouse_name)
        )
        results = self.db.execute(stmt).all()
        return [
            {"warehouse_id": str(wh_id), "code": code, "name": name}
            for wh_id, code, name in results
        ]
