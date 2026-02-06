"""
Inventory web view service.

Provides view-focused data for inventory web routes.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.models.finance.core_config.numbering_sequence import (
    NumberingSequence,
    ResetFrequency,
    SequenceType,
)
from app.models.finance.gl.account import Account
from app.models.inventory.inventory_transaction import (
    InventoryTransaction,
    TransactionType,
)
from app.models.inventory.item import CostingMethod, Item, ItemType
from app.models.inventory.item_category import ItemCategory
from app.models.inventory.warehouse import Warehouse
from app.services.common import coerce_uuid
from app.services.finance.common.numbering import SyncNumberingService
from app.services.formatters import format_currency as _format_currency
from app.services.formatters import format_date as _format_date
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.platform.org_context import org_context_service
from app.services.inventory.item import (
    ItemCategoryInput,
    ItemInput,
    item_category_service,
    item_service,
)
from app.services.inventory.transaction import TransactionInput
from app.services.inventory.warehouse import WarehouseInput, warehouse_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


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


def _get_batch_stock_quantities(
    db: Session,
    organization_id: UUID,
    item_ids: list[UUID],
) -> dict[UUID, dict]:
    """
    Batch load stock quantities for multiple items.

    Returns dict mapping item_id to stock data:
    {
        item_id: {
            "on_hand": Decimal,
            "reserved": Decimal,
            "available": Decimal,
        }
    }
    """
    from app.models.inventory.inventory_lot import InventoryLot

    if not item_ids:
        return {}

    # Get on-hand quantities from transactions (grouped by item)
    on_hand_query = (
        db.query(
            InventoryTransaction.item_id,
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
            ).label("on_hand"),
        )
        .filter(
            InventoryTransaction.organization_id == organization_id,
            InventoryTransaction.item_id.in_(item_ids),
        )
        .group_by(InventoryTransaction.item_id)
    )

    on_hand_results = {
        row.item_id: row.on_hand or Decimal("0") for row in on_hand_query.all()
    }

    # Get reserved quantities from lots (grouped by item)
    reserved_query = (
        db.query(
            InventoryLot.item_id,
            func.sum(InventoryLot.quantity_allocated).label("reserved"),
        )
        .filter(
            InventoryLot.organization_id == organization_id,
            InventoryLot.item_id.in_(item_ids),
        )
        .group_by(InventoryLot.item_id)
    )

    reserved_results = {
        row.item_id: row.reserved or Decimal("0") for row in reserved_query.all()
    }

    # Build result dict
    result = {}
    for item_id in item_ids:
        on_hand = on_hand_results.get(item_id, Decimal("0"))
        reserved = reserved_results.get(item_id, Decimal("0"))
        result[item_id] = {
            "on_hand": on_hand,
            "reserved": reserved,
            "available": on_hand - reserved,
        }

    return result


class InventoryWebService:
    """View service for inventory web routes."""

    @staticmethod
    def _sequence_preview(
        sequence: Optional[NumberingSequence],
        reference_date: date,
    ) -> Optional[str]:
        if not sequence:
            return None

        next_number = sequence.current_number + 1
        if sequence.reset_frequency != ResetFrequency.NEVER:
            if sequence.current_year is None:
                next_number = 1
            elif sequence.reset_frequency == ResetFrequency.YEARLY:
                if reference_date.year != sequence.current_year:
                    next_number = 1
            elif sequence.reset_frequency == ResetFrequency.MONTHLY:
                if (
                    reference_date.year != sequence.current_year
                    or reference_date.month != sequence.current_month
                ):
                    next_number = 1

        parts = []
        if sequence.prefix:
            parts.append(sequence.prefix)
        if sequence.include_year:
            if sequence.year_format == 2:
                parts.append(str(reference_date.year)[-2:])
            else:
                parts.append(str(reference_date.year))
        if sequence.include_month:
            parts.append(f"{reference_date.month:02d}")

        date_str = "".join(parts)
        seq_str = str(next_number).zfill(sequence.min_digits)
        result = f"{date_str}{sequence.separator}{seq_str}" if date_str else seq_str
        if sequence.suffix:
            result += sequence.suffix
        return result

    @staticmethod
    def item_form_context(
        db: Session,
        organization_id: str,
        item_id: Optional[str] = None,
    ) -> dict:
        """Build context for item form (create/edit)."""
        org_id = coerce_uuid(organization_id)
        numbering_service = SyncNumberingService(db)
        sequence = numbering_service.get_or_create_sequence(org_id, SequenceType.ITEM)
        today = date.today()
        item_code_preview = InventoryWebService._sequence_preview(sequence, today)

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

        # Get GL accounts for inline category creation modal
        accounts = (
            db.query(Account)
            .filter(
                Account.organization_id == org_id,
                Account.is_active.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )

        # Item types and costing methods for dropdowns
        from app.models.finance.ap.supplier import Supplier
        from app.models.inventory.item import CostingMethod, ItemType

        item_types = [
            {"value": t.value, "label": t.value.replace("_", " ").title()}
            for t in ItemType
        ]
        costing_methods = [
            {"value": c.value, "label": c.value.replace("_", " ").title()}
            for c in CostingMethod
        ]

        # Get suppliers list for INV → AP integration (default supplier)
        suppliers = (
            db.query(Supplier)
            .filter(
                Supplier.organization_id == org_id,
                Supplier.is_active.is_(True),
            )
            .order_by(Supplier.legal_name)
            .all()
        )
        suppliers_list = [
            {
                "supplier_id": str(s.supplier_id),
                "supplier_name": s.trading_name or s.legal_name,
                "supplier_code": s.supplier_code,
            }
            for s in suppliers
        ]

        context = {
            "categories": categories,
            "accounts": accounts,
            "item_types": item_types,
            "costing_methods": costing_methods,
            "suppliers_list": suppliers_list,
            "item": None,
            "item_code_preview": item_code_preview,
            "organization_id": str(org_id),
        }
        context.update(get_currency_context(db, organization_id))

        # Load existing item for edit
        if item_id:
            item_uuid = coerce_uuid(item_id)
            item = (
                db.query(Item)
                .filter(
                    Item.item_id == item_uuid,
                    Item.organization_id == org_id,
                )
                .first()
            )
            context["item"] = item

        return context

    @staticmethod
    def item_detail_context(
        db: Session,
        organization_id: str,
        item_id: str,
    ) -> dict:
        """Build context for item detail view."""
        from app.services.inventory.balance import inventory_balance_service

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
            return {
                "item": None,
                "category": None,
                "stock_summary": None,
                "warehouse_stock": [],
            }

        item_obj, category = item

        # Get warehouse-level stock breakdown
        stock_summary = None
        warehouse_stock = []

        if item_obj.track_inventory:
            try:
                stock_summary_data = inventory_balance_service.get_item_stock_summary(
                    db, org_id, item_uuid
                )
                if stock_summary_data:
                    stock_summary = {
                        "total_on_hand": stock_summary_data.total_on_hand,
                        "total_reserved": stock_summary_data.total_reserved,
                        "total_available": stock_summary_data.total_available,
                        "below_reorder": stock_summary_data.below_reorder,
                        "below_minimum": stock_summary_data.below_minimum,
                        "above_maximum": stock_summary_data.above_maximum,
                    }

                    # Format warehouse breakdown
                    for wh_balance in stock_summary_data.warehouses:
                        warehouse_stock.append(
                            {
                                "warehouse_id": wh_balance.warehouse_id,
                                "warehouse_code": wh_balance.warehouse_code,
                                "quantity_on_hand": wh_balance.quantity_on_hand,
                                "quantity_reserved": wh_balance.quantity_reserved,
                                "quantity_available": wh_balance.quantity_available,
                                "average_cost": wh_balance.average_cost,
                                "total_value": _format_currency(
                                    wh_balance.total_value, item_obj.currency_code
                                ),
                            }
                        )
            except Exception as e:
                logger.exception(
                    "Failed to load stock balances for item %s: %s", item_id, e
                )

        return {
            "item": item_obj,
            "category": category,
            "stock_summary": stock_summary,
            "warehouse_stock": warehouse_stock,
        }

    @staticmethod
    def list_items_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        category: Optional[str],
        status: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        query = (
            db.query(Item, ItemCategory)
            .join(ItemCategory, Item.category_id == ItemCategory.category_id)
            .filter(Item.organization_id == org_id)
        )

        # Category filter
        category_id = _try_uuid(category)
        if category_id:
            query = query.filter(Item.category_id == category_id)
        elif category:
            query = query.filter(ItemCategory.category_code == category)

        # Status filter
        if status == "active":
            query = query.filter(Item.is_active.is_(True))
        elif status == "inactive":
            query = query.filter(Item.is_active.is_(False))

        # Search filter
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
        rows = query.order_by(Item.item_code).limit(limit).offset(offset).all()

        # Batch load inventory quantities for all items on this page
        item_ids = [item.item_id for item, _ in rows]
        stock_quantities = (
            _get_batch_stock_quantities(db, org_id, item_ids) if item_ids else {}
        )

        items_view = []
        for item, category_row in rows:
            stock_data = stock_quantities.get(item.item_id, {})
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
                    "list_price": _format_currency(item.list_price, item.currency_code),
                    "currency_code": item.currency_code,
                    "is_active": item.is_active,
                    # Stock quantities
                    "quantity_on_hand": stock_data.get("on_hand", Decimal("0")),
                    "quantity_reserved": stock_data.get("reserved", Decimal("0")),
                    "quantity_available": stock_data.get("available", Decimal("0")),
                    "track_inventory": item.track_inventory,
                    "below_reorder": (
                        stock_data.get("on_hand", Decimal("0"))
                        < (item.reorder_point or Decimal("0"))
                        if item.track_inventory and item.reorder_point
                        else False
                    ),
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

        # Pre-computed stat-card counts (across ALL items, not just the page)
        active_count = (
            db.query(func.count(Item.item_id))
            .filter(Item.organization_id == org_id, Item.is_active.is_(True))
            .scalar()
        ) or 0
        stock_count = (
            db.query(func.count(Item.item_id))
            .filter(
                Item.organization_id == org_id,
                Item.item_type == ItemType.INVENTORY,
            )
            .scalar()
        ) or 0

        return {
            "items": items_view,
            "categories": categories,
            "search": search or "",
            "category": category or "",
            "status": status or "",
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "active_count": active_count,
            "stock_count": stock_count,
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
            .join(
                Warehouse, InventoryTransaction.warehouse_id == Warehouse.warehouse_id
            )
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
            query.with_entities(
                func.count(InventoryTransaction.transaction_id)
            ).scalar()
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
        from app.services.inventory.balance import inventory_balance_service

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
            unit_cost = (
                db_item.average_cost or db_item.last_purchase_cost or Decimal("0")
                if db_item
                else Decimal("0")
            )
            suggested_value = item.suggested_order_qty * unit_cost
            total_suggested_value += suggested_value

            items_view.append(
                {
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
                    "default_supplier_id": str(item.default_supplier_id)
                    if item.default_supplier_id
                    else None,
                    "lead_time_days": item.lead_time_days,
                    "urgency": _calculate_urgency(
                        item.quantity_available, item.reorder_point
                    ),
                }
            )

        # Sort by urgency (most urgent first)
        items_view.sort(
            key=lambda x: (
                0
                if x["urgency"] == "CRITICAL"
                else 1
                if x["urgency"] == "LOW"
                else 2
                if x["urgency"] == "WARNING"
                else 3
            )
        )

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
        from app.services.inventory.balance import inventory_balance_service

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
            balances_view.append(
                {
                    "item_id": str(balance.item_id),
                    "item_code": balance.item_code,
                    "item_name": balance.item_name,
                    "quantity_on_hand": balance.quantity_on_hand,
                    "quantity_reserved": balance.quantity_reserved,
                    "quantity_available": balance.quantity_available,
                    "average_cost": _format_currency(balance.average_cost),
                    "total_value": _format_currency(balance.total_value),
                    "total_value_raw": balance.total_value,
                }
            )

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

    def list_items_response(
        self,
        request: Request,
        auth: WebAuthContext,
        search: Optional[str],
        category: Optional[str],
        status: Optional[str],
        page: int,
        limit: int,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Inventory Items", "items")
        context.update(
            self.list_items_context(
                db,
                str(auth.organization_id),
                search=search,
                category=category,
                status=status,
                page=page,
                limit=limit,
            )
        )
        return templates.TemplateResponse(request, "inventory/items.html", context)

    def item_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Item", "items")
        context.update(self.item_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(request, "inventory/item_form.html", context)

    def create_item_response(
        self,
        request: Request,
        auth: WebAuthContext,
        item_code: str,
        item_name: str,
        category_id: str,
        item_type: str,
        base_uom: str,
        purchase_uom: Optional[str],
        sales_uom: Optional[str],
        costing_method: str,
        currency_code: Optional[str],
        standard_cost: Optional[str],
        list_price: Optional[str],
        reorder_point: Optional[str],
        reorder_quantity: Optional[str],
        minimum_stock: Optional[str],
        maximum_stock: Optional[str],
        lead_time_days: Optional[str],
        description: Optional[str],
        track_inventory: bool,
        track_lots: bool,
        track_serial_numbers: bool,
        is_purchaseable: bool,
        is_saleable: bool,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        try:
            org_id = auth.organization_id
            assert org_id is not None
            resolved_currency = (
                currency_code
                or org_context_service.get_functional_currency(
                    db,
                    org_id,
                )
            )

            input_data = ItemInput(
                item_code=item_code,
                item_name=item_name,
                category_id=UUID(category_id),
                item_type=ItemType(item_type),
                base_uom=base_uom,
                purchase_uom=purchase_uom,
                sales_uom=sales_uom,
                costing_method=CostingMethod(costing_method),
                currency_code=resolved_currency,
                standard_cost=Decimal(standard_cost) if standard_cost else None,
                list_price=Decimal(list_price) if list_price else None,
                reorder_point=Decimal(reorder_point) if reorder_point else None,
                reorder_quantity=Decimal(reorder_quantity)
                if reorder_quantity
                else None,
                minimum_stock=Decimal(minimum_stock) if minimum_stock else None,
                maximum_stock=Decimal(maximum_stock) if maximum_stock else None,
                lead_time_days=int(lead_time_days) if lead_time_days else None,
                description=description,
                track_inventory=track_inventory,
                track_lots=track_lots,
                track_serial_numbers=track_serial_numbers,
                is_purchaseable=is_purchaseable,
                is_saleable=is_saleable,
            )

            item_service.create_item(db, org_id, input_data)
            return RedirectResponse(url="/inventory/items", status_code=303)

        except Exception as e:
            context = base_context(request, auth, "New Item", "items")
            context.update(self.item_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "inventory/item_form.html", context
            )

    def item_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        item_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Item Details", "items")
        context.update(self.item_detail_context(db, str(auth.organization_id), item_id))
        return templates.TemplateResponse(
            request, "inventory/item_detail.html", context
        )

    def item_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        item_id: str,
    ) -> HTMLResponse | RedirectResponse:
        context = base_context(request, auth, "Edit Item", "items")
        context.update(self.item_form_context(db, str(auth.organization_id), item_id))
        if not context.get("item"):
            return RedirectResponse(url="/inventory/items", status_code=303)
        return templates.TemplateResponse(request, "inventory/item_form.html", context)

    def update_item_response(
        self,
        request: Request,
        auth: WebAuthContext,
        item_id: str,
        item_code: Optional[str],
        item_name: Optional[str],
        category_id: Optional[str],
        item_type: str,
        base_uom: str,
        purchase_uom: Optional[str],
        sales_uom: Optional[str],
        costing_method: str,
        currency_code: Optional[str],
        standard_cost: Optional[str],
        list_price: Optional[str],
        reorder_point: Optional[str],
        reorder_quantity: Optional[str],
        minimum_stock: Optional[str],
        maximum_stock: Optional[str],
        lead_time_days: Optional[str],
        description: Optional[str],
        track_inventory: bool,
        track_lots: bool,
        track_serial_numbers: bool,
        is_purchaseable: bool,
        is_saleable: bool,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        try:
            if not item_name or not category_id:
                raise ValueError("Item name and category are required.")
            org_id = auth.organization_id
            assert org_id is not None
            resolved_currency = (
                currency_code
                or org_context_service.get_functional_currency(
                    db,
                    org_id,
                )
            )

            updates = {
                "item_name": item_name,
                "category_id": UUID(category_id),
                "item_type": ItemType(item_type),
                "base_uom": base_uom,
                "purchase_uom": purchase_uom,
                "sales_uom": sales_uom,
                "costing_method": CostingMethod(costing_method),
                "currency_code": resolved_currency,
                "standard_cost": Decimal(standard_cost) if standard_cost else None,
                "list_price": Decimal(list_price) if list_price else None,
                "reorder_point": Decimal(reorder_point) if reorder_point else None,
                "reorder_quantity": Decimal(reorder_quantity)
                if reorder_quantity
                else None,
                "minimum_stock": Decimal(minimum_stock) if minimum_stock else None,
                "maximum_stock": Decimal(maximum_stock) if maximum_stock else None,
                "lead_time_days": int(lead_time_days) if lead_time_days else None,
                "description": description,
                "track_inventory": track_inventory,
                "track_lots": track_lots,
                "track_serial_numbers": track_serial_numbers,
                "is_purchaseable": is_purchaseable,
                "is_saleable": is_saleable,
            }

            item_service.update_item(db, org_id, UUID(item_id), updates)
            return RedirectResponse(url=f"/inventory/items/{item_id}", status_code=303)

        except Exception as e:
            context = base_context(request, auth, "Edit Item", "items")
            context.update(
                self.item_form_context(db, str(auth.organization_id), item_id)
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "inventory/item_form.html", context
            )

    def list_transactions_response(
        self,
        request: Request,
        auth: WebAuthContext,
        search: Optional[str],
        transaction_type: Optional[str],
        page: int,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Inventory Transactions", "transactions")
        context.update(
            self.list_transactions_context(
                db,
                str(auth.organization_id),
                search=search,
                transaction_type=transaction_type,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "inventory/transactions.html", context
        )

    # ========================================================================
    # Categories
    # ========================================================================

    @staticmethod
    def list_categories_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        status: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        """Build context for categories list."""
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        is_active = None
        if status == "active":
            is_active = True
        elif status == "inactive":
            is_active = False

        categories = item_category_service.list(
            db,
            organization_id=str(org_id),
            is_active=is_active,
            search=search,
            limit=limit,
            offset=offset,
        )

        total_count = item_category_service.count(
            db,
            organization_id=str(org_id),
            is_active=is_active,
            search=search,
        )
        total_pages = max(1, (total_count + limit - 1) // limit)

        # Get item counts for each category
        category_item_counts = {}
        for cat in categories:
            count = (
                db.query(Item)
                .filter(
                    Item.category_id == cat.category_id,
                    Item.is_active.is_(True),
                )
                .count()
            )
            category_item_counts[str(cat.category_id)] = count

        return {
            "categories": categories,
            "category_item_counts": category_item_counts,
            "search": search or "",
            "status": status or "",
            "page": page,
            "limit": limit,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def category_form_context(
        db: Session,
        organization_id: str,
        category_id: Optional[str] = None,
    ) -> dict:
        """Build context for category form (create/edit)."""
        org_id = coerce_uuid(organization_id)

        # Get GL accounts for dropdowns
        accounts = (
            db.query(Account)
            .filter(
                Account.organization_id == org_id,
                Account.is_active.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )

        # Get parent categories
        parent_categories = (
            db.query(ItemCategory)
            .filter(
                ItemCategory.organization_id == org_id,
                ItemCategory.is_active.is_(True),
            )
            .order_by(ItemCategory.category_code)
            .all()
        )

        context = {
            "accounts": accounts,
            "parent_categories": parent_categories,
            "category": None,
        }

        if category_id:
            cat_uuid = coerce_uuid(category_id)
            category = (
                db.query(ItemCategory)
                .filter(
                    ItemCategory.category_id == cat_uuid,
                    ItemCategory.organization_id == org_id,
                )
                .first()
            )
            context["category"] = category
            # Filter out the current category from parent options
            context["parent_categories"] = [
                c for c in parent_categories if c.category_id != cat_uuid
            ]

        return context

    def list_categories_response(
        self,
        request: Request,
        auth: WebAuthContext,
        search: Optional[str],
        status: Optional[str],
        page: int,
        limit: int,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Item Categories", "categories")
        context.update(
            self.list_categories_context(
                db,
                str(auth.organization_id),
                search=search,
                status=status,
                page=page,
                limit=limit,
            )
        )
        return templates.TemplateResponse(request, "inventory/categories.html", context)

    def category_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Category", "categories")
        context.update(self.category_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "inventory/category_form.html", context
        )

    def category_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        category_id: str,
    ) -> HTMLResponse | RedirectResponse:
        context = base_context(request, auth, "Edit Category", "categories")
        context.update(
            self.category_form_context(db, str(auth.organization_id), category_id)
        )
        if not context.get("category"):
            return RedirectResponse(url="/inventory/categories", status_code=303)
        return templates.TemplateResponse(
            request, "inventory/category_form.html", context
        )

    def create_category_response(
        self,
        request: Request,
        auth: WebAuthContext,
        category_code: str,
        category_name: str,
        inventory_account_id: str,
        cogs_account_id: str,
        revenue_account_id: str,
        inventory_adjustment_account_id: str,
        reorder_point: Optional[str],
        minimum_stock: Optional[str],
        description: Optional[str],
        parent_category_id: Optional[str],
        purchase_variance_account_id: Optional[str],
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        try:
            org_id = auth.organization_id
            assert org_id is not None
            input_data = ItemCategoryInput(
                category_code=category_code,
                category_name=category_name,
                inventory_account_id=UUID(inventory_account_id),
                cogs_account_id=UUID(cogs_account_id),
                revenue_account_id=UUID(revenue_account_id),
                inventory_adjustment_account_id=UUID(inventory_adjustment_account_id),
                reorder_point=Decimal(reorder_point) if reorder_point else None,
                minimum_stock=Decimal(minimum_stock) if minimum_stock else None,
                description=description,
                parent_category_id=UUID(parent_category_id)
                if parent_category_id
                else None,
                purchase_variance_account_id=UUID(purchase_variance_account_id)
                if purchase_variance_account_id
                else None,
            )

            item_category_service.create_category(db, org_id, input_data)
            return RedirectResponse(url="/inventory/categories", status_code=303)

        except Exception as e:
            context = base_context(request, auth, "New Category", "categories")
            context.update(self.category_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "inventory/category_form.html", context
            )

    def update_category_response(
        self,
        request: Request,
        auth: WebAuthContext,
        category_id: str,
        category_code: str,
        category_name: str,
        inventory_account_id: str,
        cogs_account_id: str,
        revenue_account_id: str,
        inventory_adjustment_account_id: str,
        reorder_point: Optional[str],
        minimum_stock: Optional[str],
        description: Optional[str],
        parent_category_id: Optional[str],
        purchase_variance_account_id: Optional[str],
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        try:
            org_id = auth.organization_id
            assert org_id is not None
            updates = {
                "category_name": category_name,
                "inventory_account_id": UUID(inventory_account_id),
                "cogs_account_id": UUID(cogs_account_id),
                "revenue_account_id": UUID(revenue_account_id),
                "inventory_adjustment_account_id": UUID(
                    inventory_adjustment_account_id
                ),
                "reorder_point": Decimal(reorder_point) if reorder_point else None,
                "minimum_stock": Decimal(minimum_stock) if minimum_stock else None,
                "description": description,
                "parent_category_id": UUID(parent_category_id)
                if parent_category_id
                else None,
                "purchase_variance_account_id": UUID(purchase_variance_account_id)
                if purchase_variance_account_id
                else None,
            }

            item_category_service.update_category(
                db, org_id, UUID(category_id), updates
            )
            return RedirectResponse(url="/inventory/categories", status_code=303)

        except Exception as e:
            context = base_context(request, auth, "Edit Category", "categories")
            context.update(
                self.category_form_context(db, str(auth.organization_id), category_id)
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "inventory/category_form.html", context
            )

    def toggle_category_status_response(
        self,
        request: Request,
        auth: WebAuthContext,
        category_id: str,
        db: Session,
    ) -> RedirectResponse:
        try:
            org_id = auth.organization_id
            assert org_id is not None
            category = item_category_service.get(db, category_id)
            if category.is_active:
                item_category_service.deactivate_category(db, org_id, UUID(category_id))
            else:
                # Reactivate
                item_category_service.update_category(
                    db, org_id, UUID(category_id), {"is_active": True}
                )
        except Exception as e:
            logger.exception("Failed to toggle category %s: %s", category_id, e)
        return RedirectResponse(url="/inventory/categories", status_code=303)

    # ========================================================================
    # Warehouses
    # ========================================================================

    @staticmethod
    def list_warehouses_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        status: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        """Build context for warehouses list."""
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        is_active = None
        if status == "active":
            is_active = True
        elif status == "inactive":
            is_active = False

        warehouses = warehouse_service.list(
            db,
            organization_id=str(org_id),
            is_active=is_active,
            search=search,
            limit=limit,
            offset=offset,
        )

        total_count = warehouse_service.count(
            db,
            organization_id=str(org_id),
            is_active=is_active,
            search=search,
        )
        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "warehouses": warehouses,
            "search": search or "",
            "status": status or "",
            "page": page,
            "limit": limit,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def warehouse_form_context(
        db: Session,
        organization_id: str,
        warehouse_id: Optional[str] = None,
    ) -> dict:
        """Build context for warehouse form (create/edit)."""
        org_id = coerce_uuid(organization_id)

        context: dict[str, Optional[Warehouse]] = {
            "warehouse": None,
        }

        if warehouse_id:
            wh_uuid = coerce_uuid(warehouse_id)
            warehouse = (
                db.query(Warehouse)
                .filter(
                    Warehouse.warehouse_id == wh_uuid,
                    Warehouse.organization_id == org_id,
                )
                .first()
            )
            context["warehouse"] = warehouse

        return context

    @staticmethod
    def warehouse_detail_context(
        db: Session,
        organization_id: str,
        warehouse_id: str,
    ) -> dict:
        """Build context for warehouse detail view."""
        org_id = coerce_uuid(organization_id)
        wh_uuid = coerce_uuid(warehouse_id)

        warehouse = (
            db.query(Warehouse)
            .filter(
                Warehouse.warehouse_id == wh_uuid,
                Warehouse.organization_id == org_id,
            )
            .first()
        )

        if not warehouse:
            return {"warehouse": None}

        # Get inventory summary for this warehouse
        from app.services.inventory.balance import inventory_balance_service

        inventory = inventory_balance_service.get_warehouse_inventory(
            db=db,
            organization_id=org_id,
            warehouse_id=wh_uuid,
        )

        total_items = len(inventory)
        total_value = sum((b.total_value for b in inventory), Decimal("0"))

        return {
            "warehouse": warehouse,
            "total_items": total_items,
            "total_value": _format_currency(total_value),
            "inventory": inventory[:20],  # Show top 20 items
        }

    def list_warehouses_response(
        self,
        request: Request,
        auth: WebAuthContext,
        search: Optional[str],
        status: Optional[str],
        page: int,
        limit: int,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Warehouses", "warehouses")
        context.update(
            self.list_warehouses_context(
                db,
                str(auth.organization_id),
                search=search,
                status=status,
                page=page,
                limit=limit,
            )
        )
        return templates.TemplateResponse(request, "inventory/warehouses.html", context)

    def warehouse_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Warehouse", "warehouses")
        context.update(self.warehouse_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "inventory/warehouse_form.html", context
        )

    def warehouse_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        warehouse_id: str,
    ) -> HTMLResponse | RedirectResponse:
        context = base_context(request, auth, "Edit Warehouse", "warehouses")
        context.update(
            self.warehouse_form_context(db, str(auth.organization_id), warehouse_id)
        )
        if not context.get("warehouse"):
            return RedirectResponse(url="/inventory/warehouses", status_code=303)
        return templates.TemplateResponse(
            request, "inventory/warehouse_form.html", context
        )

    def warehouse_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        warehouse_id: str,
    ) -> HTMLResponse | RedirectResponse:
        context = base_context(request, auth, "Warehouse Details", "warehouses")
        context.update(
            self.warehouse_detail_context(db, str(auth.organization_id), warehouse_id)
        )
        if not context.get("warehouse"):
            return RedirectResponse(url="/inventory/warehouses", status_code=303)
        return templates.TemplateResponse(
            request, "inventory/warehouse_detail.html", context
        )

    def create_warehouse_response(
        self,
        request: Request,
        auth: WebAuthContext,
        warehouse_code: str,
        warehouse_name: str,
        description: Optional[str],
        contact_name: Optional[str],
        contact_phone: Optional[str],
        contact_email: Optional[str],
        address_line1: Optional[str],
        address_line2: Optional[str],
        address_city: Optional[str],
        address_state: Optional[str],
        address_postal_code: Optional[str],
        address_country: Optional[str],
        is_receiving: bool,
        is_shipping: bool,
        is_consignment: bool,
        is_transit: bool,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        try:
            org_id = auth.organization_id
            assert org_id is not None
            # Build address dict
            address = None
            if any([address_line1, address_city, address_state, address_country]):
                address = {
                    "line1": address_line1,
                    "line2": address_line2,
                    "city": address_city,
                    "state": address_state,
                    "postal_code": address_postal_code,
                    "country": address_country,
                }

            input_data = WarehouseInput(
                warehouse_code=warehouse_code,
                warehouse_name=warehouse_name,
                description=description,
                address=address,
                contact_name=contact_name,
                contact_phone=contact_phone,
                contact_email=contact_email,
                is_receiving=is_receiving,
                is_shipping=is_shipping,
                is_consignment=is_consignment,
                is_transit=is_transit,
            )

            warehouse_service.create_warehouse(db, org_id, input_data)
            return RedirectResponse(url="/inventory/warehouses", status_code=303)

        except Exception as e:
            context = base_context(request, auth, "New Warehouse", "warehouses")
            context.update(self.warehouse_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "inventory/warehouse_form.html", context
            )

    def update_warehouse_response(
        self,
        request: Request,
        auth: WebAuthContext,
        warehouse_id: str,
        warehouse_code: str,
        warehouse_name: str,
        description: Optional[str],
        contact_name: Optional[str],
        contact_phone: Optional[str],
        contact_email: Optional[str],
        address_line1: Optional[str],
        address_line2: Optional[str],
        address_city: Optional[str],
        address_state: Optional[str],
        address_postal_code: Optional[str],
        address_country: Optional[str],
        is_receiving: bool,
        is_shipping: bool,
        is_consignment: bool,
        is_transit: bool,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        try:
            org_id = auth.organization_id
            assert org_id is not None
            # Build address dict
            address = None
            if any([address_line1, address_city, address_state, address_country]):
                address = {
                    "line1": address_line1,
                    "line2": address_line2,
                    "city": address_city,
                    "state": address_state,
                    "postal_code": address_postal_code,
                    "country": address_country,
                }

            updates = {
                "warehouse_name": warehouse_name,
                "description": description,
                "address": address,
                "contact_name": contact_name,
                "contact_phone": contact_phone,
                "contact_email": contact_email,
                "is_receiving": is_receiving,
                "is_shipping": is_shipping,
                "is_consignment": is_consignment,
                "is_transit": is_transit,
            }

            warehouse_service.update_warehouse(db, org_id, UUID(warehouse_id), updates)
            return RedirectResponse(url="/inventory/warehouses", status_code=303)

        except Exception as e:
            context = base_context(request, auth, "Edit Warehouse", "warehouses")
            context.update(
                self.warehouse_form_context(db, str(auth.organization_id), warehouse_id)
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "inventory/warehouse_form.html", context
            )

    def toggle_warehouse_status_response(
        self,
        request: Request,
        auth: WebAuthContext,
        warehouse_id: str,
        db: Session,
    ) -> RedirectResponse:
        try:
            org_id = auth.organization_id
            assert org_id is not None
            warehouse = warehouse_service.get(db, warehouse_id)
            if warehouse.is_active:
                warehouse_service.deactivate_warehouse(db, org_id, UUID(warehouse_id))
            else:
                # Reactivate
                warehouse_service.update_warehouse(
                    db, org_id, UUID(warehouse_id), {"is_active": True}
                )
        except Exception as e:
            logger.exception("Failed to toggle warehouse %s: %s", warehouse_id, e)
        return RedirectResponse(url="/inventory/warehouses", status_code=303)

    @staticmethod
    def transaction_form_response(
        request: Request,
        auth: WebAuthContext,
        transaction_type: str,
        db: Session,
    ) -> HTMLResponse:
        return InventoryTransactionWebService.transaction_form_response(
            request, auth, transaction_type, db
        )

    @staticmethod
    def create_transaction_response(
        request: Request,
        auth: WebAuthContext,
        transaction_type: str,
        item_id: str,
        warehouse_id: str,
        quantity: str,
        unit_cost: str,
        transaction_date: str,
        reference: Optional[str],
        notes: Optional[str],
        lot_number: Optional[str],
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        return InventoryTransactionWebService.create_transaction_response(
            request,
            auth,
            transaction_type,
            item_id,
            warehouse_id,
            quantity,
            unit_cost,
            transaction_date,
            reference,
            notes,
            lot_number,
            db,
        )

    @staticmethod
    def create_transfer_response(
        request: Request,
        auth: WebAuthContext,
        item_id: str,
        from_warehouse_id: str,
        to_warehouse_id: str,
        quantity: str,
        transaction_date: str,
        reference: Optional[str],
        notes: Optional[str],
        lot_number: Optional[str],
        db: Session,
    ) -> RedirectResponse:
        return InventoryTransactionWebService.create_transfer_response(
            request,
            auth,
            item_id,
            from_warehouse_id,
            to_warehouse_id,
            quantity,
            transaction_date,
            reference,
            notes,
            lot_number,
            db,
        )

    @staticmethod
    def create_adjustment_response(
        request: Request,
        auth: WebAuthContext,
        item_id: str,
        warehouse_id: str,
        quantity: str,
        unit_cost: str,
        transaction_date: str,
        adjustment_type: str,
        reason: str,
        reference: Optional[str],
        db: Session,
    ) -> RedirectResponse:
        return InventoryTransactionWebService.create_adjustment_response(
            request,
            auth,
            item_id,
            warehouse_id,
            quantity,
            unit_cost,
            transaction_date,
            adjustment_type,
            reason,
            reference,
            db,
        )


def _calculate_urgency(available: Decimal, reorder_point: Decimal) -> str:
    """Calculate urgency level based on available vs reorder point."""
    if available <= 0:
        return "CRITICAL"
    elif available <= reorder_point * Decimal("0.5"):
        return "LOW"
    elif available <= reorder_point:
        return "WARNING"
    return "NORMAL"


class InventoryTransactionWebService:
    """Web service for manual inventory transactions."""

    @staticmethod
    def transaction_form_context(
        db: Session,
        organization_id: str,
        transaction_type: str,
    ) -> dict:
        """Build context for transaction form."""
        from app.models.inventory.warehouse import Warehouse

        org_id = coerce_uuid(organization_id)

        # Get items
        items = (
            db.query(Item)
            .filter(
                Item.organization_id == org_id,
                Item.is_active.is_(True),
                Item.track_inventory.is_(True),
            )
            .order_by(Item.item_code)
            .all()
        )
        items_list = [
            {
                "item_id": str(i.item_id),
                "item_code": i.item_code,
                "item_name": i.item_name,
                "uom": i.base_uom,
                "last_cost": float(i.last_purchase_cost) if i.last_purchase_cost else 0,
                "average_cost": float(i.average_cost) if i.average_cost else 0,
            }
            for i in items
        ]

        # Get warehouses
        warehouses = (
            db.query(Warehouse)
            .filter(
                Warehouse.organization_id == org_id,
                Warehouse.is_active.is_(True),
            )
            .order_by(Warehouse.warehouse_code)
            .all()
        )
        warehouses_list = [
            {
                "warehouse_id": str(w.warehouse_id),
                "warehouse_code": w.warehouse_code,
                "warehouse_name": w.warehouse_name,
            }
            for w in warehouses
        ]

        context = {
            "items_list": items_list,
            "warehouses_list": warehouses_list,
            "transaction_type": transaction_type,
            "today": date.today().strftime("%Y-%m-%d"),
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def transaction_form_response(
        request: Request,
        auth: WebAuthContext,
        transaction_type: str,
        db: Session,
    ) -> HTMLResponse:
        """Render transaction form page."""
        from app.web.deps import base_context

        title_map = {
            "RECEIPT": "Inventory Receipt",
            "ISSUE": "Inventory Issue",
            "TRANSFER": "Inventory Transfer",
            "ADJUSTMENT": "Inventory Adjustment",
        }
        page_title = title_map.get(transaction_type, "Inventory Transaction")
        context = base_context(request, auth, page_title, "transactions", db=db)
        context.update(
            InventoryTransactionWebService.transaction_form_context(
                db, str(auth.organization_id), transaction_type
            )
        )

        # Map type to template
        template_map = {
            "RECEIPT": "inventory/receipt_form.html",
            "ISSUE": "inventory/issue_form.html",
            "TRANSFER": "inventory/transfer_form.html",
            "ADJUSTMENT": "inventory/adjustment_form.html",
        }
        template = template_map.get(transaction_type, "inventory/receipt_form.html")

        return templates.TemplateResponse(request, template, context)

    @staticmethod
    def create_transaction_response(
        request: Request,
        auth: WebAuthContext,
        transaction_type: str,
        item_id: str,
        warehouse_id: str,
        quantity: str,
        unit_cost: str,
        transaction_date: str,
        reference: Optional[str],
        notes: Optional[str],
        lot_number: Optional[str],
        db: Session,
    ) -> RedirectResponse:
        """Create a manual inventory transaction."""
        from datetime import datetime

        from app.models.finance.gl.fiscal_period import FiscalPeriod
        from app.models.inventory.inventory_transaction import TransactionType
        from app.services.inventory.transaction import (
            InventoryTransactionService,
            TransactionInput,
        )

        org_id = auth.organization_id
        user_id = auth.user_id
        assert org_id is not None
        assert user_id is not None

        try:
            # Parse inputs
            qty = Decimal(quantity)
            cost = Decimal(unit_cost)
            txn_date = datetime.strptime(transaction_date, "%Y-%m-%d")

            # Get fiscal period
            fiscal_period = (
                db.query(FiscalPeriod)
                .filter(
                    FiscalPeriod.organization_id == org_id,
                    FiscalPeriod.start_date <= txn_date.date(),
                    FiscalPeriod.end_date >= txn_date.date(),
                )
                .first()
            )

            if not fiscal_period:
                return RedirectResponse(
                    url="/inventory/transactions?error=no_fiscal_period",
                    status_code=303,
                )

            # Map transaction type
            txn_type = (
                TransactionType.RECEIPT
                if transaction_type == "RECEIPT"
                else TransactionType.ISSUE
            )

            txn_input = TransactionInput(
                transaction_type=txn_type,
                transaction_date=txn_date,
                fiscal_period_id=fiscal_period.fiscal_period_id,
                item_id=UUID(item_id),
                warehouse_id=UUID(warehouse_id),
                quantity=qty,
                unit_cost=cost,
                uom="",  # Will be filled from item
                currency_code="",  # Will be filled from item
                reference=reference,
                source_document_type="MANUAL",
            )

            if transaction_type == "RECEIPT":
                InventoryTransactionService.create_receipt(
                    db, org_id, txn_input, user_id
                )
            else:
                InventoryTransactionService.create_issue(db, org_id, txn_input, user_id)

            return RedirectResponse(url="/inventory/transactions", status_code=303)

        except Exception as e:
            return RedirectResponse(
                url=f"/inventory/transactions?error={str(e)}", status_code=303
            )

    @staticmethod
    def create_transfer_response(
        request: Request,
        auth: WebAuthContext,
        item_id: str,
        from_warehouse_id: str,
        to_warehouse_id: str,
        quantity: str,
        transaction_date: str,
        reference: Optional[str],
        notes: Optional[str],
        lot_number: Optional[str],
        db: Session,
    ) -> RedirectResponse:
        """Create an inventory transfer."""
        from datetime import datetime

        from app.models.finance.gl.fiscal_period import FiscalPeriod
        from app.services.inventory.transaction import InventoryTransactionService

        org_id = auth.organization_id
        user_id = auth.user_id
        assert org_id is not None
        assert user_id is not None

        try:
            qty = Decimal(quantity)
            txn_date = datetime.strptime(transaction_date, "%Y-%m-%d")

            # Get fiscal period
            fiscal_period = (
                db.query(FiscalPeriod)
                .filter(
                    FiscalPeriod.organization_id == org_id,
                    FiscalPeriod.start_date <= txn_date.date(),
                    FiscalPeriod.end_date >= txn_date.date(),
                )
                .first()
            )

            if not fiscal_period:
                return RedirectResponse(
                    url="/inventory/transactions?error=no_fiscal_period",
                    status_code=303,
                )

            item = db.get(Item, UUID(item_id))
            if not item or item.organization_id != org_id:
                return RedirectResponse(
                    url="/inventory/transactions?error=item_not_found", status_code=303
                )

            txn_input = TransactionInput(
                transaction_type=TransactionType.TRANSFER,
                transaction_date=txn_date,
                fiscal_period_id=fiscal_period.fiscal_period_id,
                item_id=UUID(item_id),
                warehouse_id=UUID(from_warehouse_id),
                to_warehouse_id=UUID(to_warehouse_id),
                quantity=qty,
                unit_cost=item.average_cost or Decimal("0"),
                uom=item.base_uom,
                currency_code=item.currency_code,
                reference=reference,
            )

            InventoryTransactionService.create_transfer(
                db=db,
                organization_id=org_id,
                input=txn_input,
                created_by_user_id=user_id,
            )

            return RedirectResponse(url="/inventory/transactions", status_code=303)

        except Exception as e:
            return RedirectResponse(
                url=f"/inventory/transactions?error={str(e)}", status_code=303
            )

    @staticmethod
    def create_adjustment_response(
        request: Request,
        auth: WebAuthContext,
        item_id: str,
        warehouse_id: str,
        quantity: str,
        unit_cost: str,
        transaction_date: str,
        adjustment_type: str,
        reason: str,
        reference: Optional[str],
        db: Session,
    ) -> RedirectResponse:
        """Create an inventory adjustment."""
        from datetime import datetime

        from app.models.finance.gl.fiscal_period import FiscalPeriod
        from app.models.inventory.inventory_transaction import TransactionType
        from app.services.inventory.transaction import (
            InventoryTransactionService,
            TransactionInput,
        )

        org_id = auth.organization_id
        user_id = auth.user_id
        assert org_id is not None
        assert user_id is not None

        try:
            qty = Decimal(quantity)
            cost = Decimal(unit_cost)
            txn_date = datetime.strptime(transaction_date, "%Y-%m-%d")

            # Get fiscal period
            fiscal_period = (
                db.query(FiscalPeriod)
                .filter(
                    FiscalPeriod.organization_id == org_id,
                    FiscalPeriod.start_date <= txn_date.date(),
                    FiscalPeriod.end_date >= txn_date.date(),
                )
                .first()
            )

            if not fiscal_period:
                return RedirectResponse(
                    url="/inventory/transactions?error=no_fiscal_period",
                    status_code=303,
                )

            txn_input = TransactionInput(
                transaction_type=TransactionType.ADJUSTMENT,
                transaction_date=txn_date,
                fiscal_period_id=fiscal_period.fiscal_period_id,
                item_id=UUID(item_id),
                warehouse_id=UUID(warehouse_id),
                quantity=qty if adjustment_type == "INCREASE" else -qty,
                unit_cost=cost,
                uom="",
                currency_code="",
                reference=f"{reason}: {reference}" if reference else reason,
                source_document_type="ADJUSTMENT",
            )

            InventoryTransactionService.create_adjustment(
                db, org_id, txn_input, user_id
            )

            return RedirectResponse(url="/inventory/transactions", status_code=303)

        except Exception as e:
            return RedirectResponse(
                url=f"/inventory/transactions?error={str(e)}", status_code=303
            )


inv_web_service = InventoryWebService()
