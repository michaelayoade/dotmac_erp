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


inv_web_service = InventoryWebService()
