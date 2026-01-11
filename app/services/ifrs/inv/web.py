"""
Inventory web view service.

Provides view-focused data for inventory web routes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.ifrs.inv.inventory_transaction import InventoryTransaction, TransactionType
from app.models.ifrs.inv.item import Item
from app.models.ifrs.inv.item_category import ItemCategory
from app.models.ifrs.inv.warehouse import Warehouse
from app.services.common import coerce_uuid


def _format_date(value: Optional[date]) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _format_currency(amount: Optional[Decimal], currency: str = "USD") -> str:
    if amount is None:
        return ""
    value = Decimal(str(amount))
    if currency == "USD":
        return f"${value:,.2f}"
    return f"{currency} {value:,.2f}"


def _parse_transaction_type(value: Optional[str]) -> Optional[TransactionType]:
    if not value:
        return None
    try:
        return TransactionType(value)
    except ValueError:
        try:
            return TransactionType(value.upper())
        except ValueError:
            return None


def _try_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


class InventoryWebService:
    """View service for inventory web routes."""

    @staticmethod
    def item_form_context(
        db: Session,
        organization_id: str,
        item_id: Optional[str] = None,
    ) -> dict:
        """Build context for item form (create/edit)."""
        org_id = coerce_uuid(organization_id)

        # Get categories for dropdown
        categories = (
            db.query(ItemCategory)
            .filter(
                ItemCategory.organization_id == org_id,
                ItemCategory.is_active.is_(True),
            )
            .order_by(ItemCategory.category_code)
            .all()
        )

        # Item types and costing methods for dropdowns
        from app.models.ifrs.inv.item import ItemType, CostingMethod

        item_types = [{"value": t.value, "label": t.value.replace("_", " ").title()} for t in ItemType]
        costing_methods = [{"value": c.value, "label": c.value.replace("_", " ").title()} for c in CostingMethod]

        context = {
            "categories": categories,
            "item_types": item_types,
            "costing_methods": costing_methods,
            "item": None,
        }

        # Load existing item for edit
        if item_id:
            item_uuid = coerce_uuid(item_id)
            item = db.query(Item).filter(
                Item.item_id == item_uuid,
                Item.organization_id == org_id,
            ).first()
            context["item"] = item

        return context

    @staticmethod
    def item_detail_context(
        db: Session,
        organization_id: str,
        item_id: str,
    ) -> dict:
        """Build context for item detail view."""
        org_id = coerce_uuid(organization_id)
        item_uuid = coerce_uuid(item_id)

        item = (
            db.query(Item, ItemCategory)
            .join(ItemCategory, Item.category_id == ItemCategory.category_id)
            .filter(
                Item.item_id == item_uuid,
                Item.organization_id == org_id,
            )
            .first()
        )

        if not item:
            return {"item": None, "category": None}

        item_obj, category = item
        return {
            "item": item_obj,
            "category": category,
        }

    @staticmethod
    def list_items_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        category: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        query = (
            db.query(Item, ItemCategory)
            .join(ItemCategory, Item.category_id == ItemCategory.category_id)
            .filter(Item.organization_id == org_id)
        )

        category_id = _try_uuid(category)
        if category_id:
            query = query.filter(Item.category_id == category_id)
        elif category:
            query = query.filter(ItemCategory.category_code == category)

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Item.item_code.ilike(search_pattern),
                    Item.item_name.ilike(search_pattern),
                    Item.barcode.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(Item.item_id)).scalar() or 0
        rows = (
            query.order_by(Item.item_code)
            .limit(limit)
            .offset(offset)
            .all()
        )

        items_view = []
        for item, category_row in rows:
            items_view.append(
                {
                    "item_id": item.item_id,
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "category_name": category_row.category_name,
                    "category_code": category_row.category_code,
                    "item_type": item.item_type.value,
                    "costing_method": item.costing_method.value,
                    "standard_cost": _format_currency(
                        item.standard_cost, item.currency_code
                    ),
                    "list_price": _format_currency(
                        item.list_price, item.currency_code
                    ),
                    "currency_code": item.currency_code,
                    "is_active": item.is_active,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        categories = (
            db.query(ItemCategory)
            .filter(
                ItemCategory.organization_id == org_id,
                ItemCategory.is_active.is_(True),
            )
            .order_by(ItemCategory.category_code)
            .all()
        )

        return {
            "items": items_view,
            "categories": categories,
            "search": search,
            "category": category,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def list_transactions_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        transaction_type: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        type_value = _parse_transaction_type(transaction_type)

        query = (
            db.query(InventoryTransaction, Item, Warehouse)
            .join(Item, InventoryTransaction.item_id == Item.item_id)
            .join(Warehouse, InventoryTransaction.warehouse_id == Warehouse.warehouse_id)
            .filter(InventoryTransaction.organization_id == org_id)
        )

        if type_value:
            query = query.filter(InventoryTransaction.transaction_type == type_value)

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    InventoryTransaction.reference.ilike(search_pattern),
                    Item.item_code.ilike(search_pattern),
                    Item.item_name.ilike(search_pattern),
                )
            )

        total_count = (
            query.with_entities(func.count(InventoryTransaction.transaction_id)).scalar()
            or 0
        )
        rows = (
            query.order_by(InventoryTransaction.transaction_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        transactions_view = []
        for txn, item, warehouse in rows:
            transactions_view.append(
                {
                    "transaction_id": txn.transaction_id,
                    "transaction_date": _format_date(txn.transaction_date),
                    "transaction_type": txn.transaction_type.value,
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "warehouse_code": warehouse.warehouse_code,
                    "warehouse_name": warehouse.warehouse_name,
                    "quantity": txn.quantity,
                    "uom": txn.uom,
                    "unit_cost": _format_currency(txn.unit_cost, txn.currency_code),
                    "total_cost": _format_currency(txn.total_cost, txn.currency_code),
                    "reference": txn.reference,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "transactions": transactions_view,
            "transaction_types": [t.value for t in TransactionType],
            "search": search,
            "transaction_type": transaction_type,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def low_stock_dashboard_context(
        db: Session,
        organization_id: str,
        include_below_minimum: bool = True,
    ) -> dict:
        """
        Build context for low stock / reorder alerts dashboard.

        Shows items at or below reorder point, with suggested order quantities.
        """
        from app.services.ifrs.inv.balance import inventory_balance_service

        org_id = coerce_uuid(organization_id)

        # Get low stock items
        low_stock_items = inventory_balance_service.get_low_stock_items(
            db=db,
            organization_id=org_id,
            include_below_minimum=include_below_minimum,
        )

        # Format for display
        items_view = []
        total_suggested_value = Decimal("0")

        for item in low_stock_items:
            # Get item details for pricing
            db_item = db.get(Item, item.item_id)
            unit_cost = db_item.average_cost or db_item.last_purchase_cost or Decimal("0") if db_item else Decimal("0")
            suggested_value = item.suggested_order_qty * unit_cost
            total_suggested_value += suggested_value

            items_view.append({
                "item_id": str(item.item_id),
                "item_code": item.item_code,
                "item_name": item.item_name,
                "quantity_on_hand": item.quantity_on_hand,
                "quantity_available": item.quantity_available,
                "reorder_point": item.reorder_point,
                "reorder_quantity": item.reorder_quantity,
                "suggested_order_qty": item.suggested_order_qty,
                "suggested_value": _format_currency(suggested_value),
                "suggested_value_raw": suggested_value,
                "default_supplier_id": str(item.default_supplier_id) if item.default_supplier_id else None,
                "lead_time_days": item.lead_time_days,
                "urgency": _calculate_urgency(item.quantity_available, item.reorder_point),
            })

        # Sort by urgency (most urgent first)
        items_view.sort(key=lambda x: (
            0 if x["urgency"] == "CRITICAL" else
            1 if x["urgency"] == "LOW" else
            2 if x["urgency"] == "WARNING" else 3
        ))

        # Summary statistics
        critical_count = sum(1 for i in items_view if i["urgency"] == "CRITICAL")
        low_count = sum(1 for i in items_view if i["urgency"] == "LOW")
        warning_count = sum(1 for i in items_view if i["urgency"] == "WARNING")

        return {
            "items": items_view,
            "total_items": len(items_view),
            "critical_count": critical_count,
            "low_count": low_count,
            "warning_count": warning_count,
            "total_suggested_value": _format_currency(total_suggested_value),
            "total_suggested_value_raw": total_suggested_value,
            "include_below_minimum": include_below_minimum,
        }

    @staticmethod
    def warehouse_stock_context(
        db: Session,
        organization_id: str,
        warehouse_id: str,
    ) -> dict:
        """Build context for warehouse stock view."""
        from app.services.ifrs.inv.balance import inventory_balance_service

        org_id = coerce_uuid(organization_id)
        wh_id = coerce_uuid(warehouse_id)

        # Get warehouse details
        warehouse = db.get(Warehouse, wh_id)
        if not warehouse or warehouse.organization_id != org_id:
            return {"warehouse": None, "balances": [], "summary": {}}

        # Get all inventory in warehouse
        balances = inventory_balance_service.get_warehouse_inventory(
            db=db,
            organization_id=org_id,
            warehouse_id=wh_id,
        )

        # Format for display
        balances_view = []
        total_value = Decimal("0")
        total_items = 0

        for balance in balances:
            total_value += balance.total_value
            total_items += 1
            balances_view.append({
                "item_id": str(balance.item_id),
                "item_code": balance.item_code,
                "item_name": balance.item_name,
                "quantity_on_hand": balance.quantity_on_hand,
                "quantity_reserved": balance.quantity_reserved,
                "quantity_available": balance.quantity_available,
                "average_cost": _format_currency(balance.average_cost),
                "total_value": _format_currency(balance.total_value),
                "total_value_raw": balance.total_value,
            })

        return {
            "warehouse": {
                "warehouse_id": str(warehouse.warehouse_id),
                "warehouse_code": warehouse.warehouse_code,
                "warehouse_name": warehouse.warehouse_name,
            },
            "balances": balances_view,
            "summary": {
                "total_items": total_items,
                "total_value": _format_currency(total_value),
                "total_value_raw": total_value,
            },
        }


def _calculate_urgency(available: Decimal, reorder_point: Decimal) -> str:
    """Calculate urgency level based on available vs reorder point."""
    if available <= 0:
        return "CRITICAL"
    elif available <= reorder_point * Decimal("0.5"):
        return "LOW"
    elif available <= reorder_point:
        return "WARNING"
    return "NORMAL"


inv_web_service = InventoryWebService()
