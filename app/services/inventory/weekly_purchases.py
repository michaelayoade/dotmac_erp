"""Read-only, invoice-date reporting of posted purchases of stocked items.

Supplier invoices are counted once at line level; orders and stock movements
are not unioned into the report. Credits are financial adjustments, not proof
of a physical return. All amounts remain in their original invoice currency.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.orm import Session

REPORT_TIMEZONE = ZoneInfo("Africa/Lagos")
PAGE_SIZE = 50
MAX_EXPORT_ROWS = 50_000
MIN_REPORT_DATE = date(1900, 1, 1)


@dataclass(frozen=True)
class PurchaseWeek:
    """Monday-Sunday accounting dates, with a current-week cutoff."""

    start: date
    end: date
    through: date
    today: date

    @property
    def end_exclusive(self) -> date:
        return self.through + timedelta(days=1)

    @property
    def is_current(self) -> bool:
        return self.start <= self.today <= self.end


def resolve_purchase_week(
    value: str | None = None, *, today: date | None = None
) -> PurchaseWeek:
    """Normalize any selected day to its Monday; reject invalid/future weeks."""
    today = today or datetime.now(REPORT_TIMEZONE).date()
    try:
        selected = date.fromisoformat(value) if value else today
        if value and selected.isoformat() != value:
            raise ValueError("Non-canonical date")
    except ValueError as exc:
        raise ValueError("Choose a valid date in YYYY-MM-DD format.") from exc
    if selected < MIN_REPORT_DATE:
        raise ValueError("Choose a week on or after 1 January 1900.")
    start = selected - timedelta(days=selected.weekday())
    if start > today:
        raise ValueError("Future weeks are not available for purchase reporting.")
    end = start + timedelta(days=6)
    return PurchaseWeek(start=start, end=end, through=min(end, today), today=today)


def _optional_uuid(value: str | None, label: str) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Choose a valid {label}.") from exc


@dataclass(frozen=True)
class PurchaseFilters:
    week: PurchaseWeek
    supplier_id: UUID | None = None
    warehouse_id: UUID | None = None
    category_id: UUID | None = None
    search: str = ""

    @classmethod
    def parse(
        cls,
        *,
        week_start: str | None = None,
        supplier: str | None = None,
        warehouse: str | None = None,
        category: str | None = None,
        search: str | None = None,
        today: date | None = None,
    ) -> PurchaseFilters:
        search = (search or "").strip()
        if len(search) > 100:
            raise ValueError("Search must not exceed 100 characters.")
        return cls(
            week=resolve_purchase_week(week_start, today=today),
            supplier_id=_optional_uuid(supplier, "supplier"),
            warehouse_id=_optional_uuid(warehouse, "warehouse"),
            category_id=_optional_uuid(category, "category"),
            search=search,
        )

    def query_params(self) -> dict[str, str]:
        params = {"week_start": self.week.start.isoformat()}
        for key, value in (
            ("supplier", self.supplier_id),
            ("warehouse", self.warehouse_id),
            ("category", self.category_id),
            ("search", self.search),
        ):
            if value:
                params[key] = str(value)
        return params


def format_report_number(value: Decimal | int, places: int = 2) -> str:
    """Use accounting parentheses, never convert financial values to float."""
    number = Decimal(value)
    formatted = f"{abs(number):,.{places}f}"
    return f"({formatted})" if number < 0 else formatted


def csv_text(value: object) -> str:
    """Keep untrusted labels/references as text when opened in a spreadsheet."""
    text = "" if value is None else str(value)
    probe = text.lstrip(" \t\r\n\ufeff")
    if text.startswith(("\t", "\r", "\n")) or probe.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


class WeeklyPurchasesService:
    """Tenant-scoped queries shared by the HTML report and complete CSV export."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def statement(organization_id: UUID, filters: PurchaseFilters) -> Select[Any]:
        # Import models at the query boundary, keeping date/export helpers usable
        # without initializing the ERP's complete model graph.
        from app.models.finance.ap.goods_receipt import GoodsReceipt
        from app.models.finance.ap.goods_receipt_line import GoodsReceiptLine
        from app.models.finance.ap.supplier import Supplier
        from app.models.finance.ap.supplier_invoice import (
            PostingStatus,
            SupplierInvoice,
            SupplierInvoiceStatus,
            SupplierInvoiceType,
        )
        from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
        from app.models.inventory.item import Item, ItemType
        from app.models.inventory.item_category import ItemCategory
        from app.models.inventory.warehouse import Warehouse

        if not isinstance(organization_id, UUID):
            raise ValueError("An organization is required for purchase reporting.")
        invoice = SupplierInvoice
        line = SupplierInvoiceLine
        is_credit = invoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE
        signed_amount = case(
            (is_credit, -func.abs(line.line_amount)), else_=line.line_amount
        )
        signed_tax = case((is_credit, -func.abs(line.tax_amount)), else_=line.tax_amount)
        warehouse_id = func.coalesce(line.receipt_warehouse_id, GoodsReceipt.warehouse_id)
        posted = or_(
            invoice.status.in_(SupplierInvoiceStatus.gl_impacting()),
            and_(
                invoice.status.in_(
                    [SupplierInvoiceStatus.ON_HOLD, SupplierInvoiceStatus.DISPUTED]
                ),
                invoice.posting_status == PostingStatus.POSTED,
            ),
        )
        stmt = (
            select(
                invoice.organization_id,
                line.line_id,
                invoice.invoice_id,
                invoice.invoice_number,
                invoice.invoice_date,
                invoice.invoice_type,
                invoice.status,
                invoice.currency_code,
                Supplier.supplier_id,
                Supplier.legal_name.label("supplier_name"),
                Item.item_id,
                Item.item_code,
                Item.item_name,
                ItemCategory.category_id,
                ItemCategory.category_name,
                Warehouse.warehouse_id,
                Warehouse.warehouse_name,
                line.quantity,
                line.unit_price,
                line.line_amount,
                signed_amount.label("net_amount"),
                signed_tax.label("net_tax"),
            )
            .select_from(line)
            .join(invoice, line.invoice_id == invoice.invoice_id)
            .join(
                Item,
                and_(Item.item_id == line.item_id, Item.organization_id == organization_id),
            )
            .join(
                Supplier,
                and_(
                    Supplier.supplier_id == invoice.supplier_id,
                    Supplier.organization_id == organization_id,
                ),
            )
            .outerjoin(
                ItemCategory,
                and_(
                    ItemCategory.category_id == Item.category_id,
                    ItemCategory.organization_id == organization_id,
                ),
            )
            .outerjoin(
                GoodsReceiptLine,
                GoodsReceiptLine.line_id == line.goods_receipt_line_id,
            )
            .outerjoin(
                GoodsReceipt,
                and_(
                    GoodsReceipt.receipt_id == GoodsReceiptLine.receipt_id,
                    GoodsReceipt.organization_id == organization_id,
                ),
            )
            .outerjoin(
                Warehouse,
                and_(
                    Warehouse.warehouse_id == warehouse_id,
                    Warehouse.organization_id == organization_id,
                ),
            )
            .where(
                invoice.organization_id == organization_id,
                invoice.invoice_date >= filters.week.start,
                invoice.invoice_date < filters.week.end_exclusive,
                posted,
                invoice.is_prepayment.is_(False),
                Item.track_inventory.is_(True),
                Item.item_type.in_([ItemType.INVENTORY, ItemType.KIT]),
            )
        )
        if filters.supplier_id:
            stmt = stmt.where(Supplier.supplier_id == filters.supplier_id)
        if filters.warehouse_id:
            stmt = stmt.where(Warehouse.warehouse_id == filters.warehouse_id)
        if filters.category_id:
            stmt = stmt.where(ItemCategory.category_id == filters.category_id)
        if filters.search:
            # Autoescape LIKE metacharacters: user-entered % and _ are literals.
            stmt = stmt.where(
                or_(
                    Item.item_code.icontains(filters.search, autoescape=True),
                    Item.item_name.icontains(filters.search, autoescape=True),
                    Supplier.legal_name.icontains(filters.search, autoescape=True),
                    invoice.invoice_number.icontains(filters.search, autoescape=True),
                )
            )
        return stmt

    @staticmethod
    def _ordered(stmt: Select[Any]) -> Select[Any]:
        columns = stmt.selected_columns
        return stmt.order_by(
            columns.invoice_date.desc(), columns.invoice_number, columns.line_id
        )

    def report(
        self, organization_id: UUID, filters: PurchaseFilters, *, page: int = 1
    ) -> dict[str, Any]:
        if page < 1:
            raise ValueError("Page must be at least 1.")
        stmt = self.statement(organization_id, filters)
        source = stmt.subquery()
        summary = dict(
            self.db.execute(
                select(
                    func.count().label("line_count"),
                    func.count(func.distinct(source.c.invoice_id)).label("document_count"),
                    func.count(func.distinct(source.c.item_id)).label("item_count"),
                    func.count(func.distinct(source.c.supplier_id)).label(
                        "supplier_count"
                    ),
                ).where(source.c.organization_id == organization_id)
            )
            .mappings()
            .one()
        )
        pages = max(1, (summary["line_count"] + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        rows = list(
            self.db.execute(
                self._ordered(stmt).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
            )
            .mappings()
            .all()
        )
        is_credit = source.c.invoice_type == "CREDIT_NOTE"
        totals = list(
            self.db.execute(
                select(
                    source.c.currency_code,
                    func.sum(case((is_credit, 0), else_=source.c.net_amount)).label(
                        "purchases"
                    ),
                    func.sum(case((is_credit, -source.c.net_amount), else_=0)).label(
                        "credits"
                    ),
                    func.sum(source.c.net_amount).label("net"),
                    func.sum(source.c.net_tax).label("tax"),
                )
                .where(source.c.organization_id == organization_id)
                .group_by(source.c.currency_code)
                .order_by(source.c.currency_code)
            )
            .mappings()
            .all()
        )
        return {
            "purchase_rows": rows,
            "summary": summary,
            "currency_totals": totals,
            "page": page,
            "total_pages": pages,
            "page_size": PAGE_SIZE,
            "total_count": summary["line_count"],
        }

    def options(
        self, organization_id: UUID, filters: PurchaseFilters
    ) -> dict[str, list[dict[str, str]]]:
        # Only choices represented in this week's posted stock purchases; inactive
        # master records remain selectable so historical reports stay accessible.
        unfiltered = replace(
            filters, supplier_id=None, warehouse_id=None, category_id=None, search=""
        )
        source = self.statement(organization_id, unfiltered).subquery()
        choices: dict[str, list[dict[str, str]]] = {}
        for name, identifier, label in (
            ("suppliers", source.c.supplier_id, source.c.supplier_name),
            ("warehouses", source.c.warehouse_id, source.c.warehouse_name),
            ("categories", source.c.category_id, source.c.category_name),
        ):
            rows = (
                self.db.execute(
                    select(identifier.label("id"), label.label("label"))
                    .where(
                        source.c.organization_id == organization_id,
                        identifier.is_not(None),
                    )
                    .distinct()
                    .order_by(label, identifier)
                )
                .mappings()
                .all()
            )
            choices[name] = [{"id": str(row["id"]), "label": row["label"]} for row in rows]
        return choices

    def export_rows(self, organization_id: UUID, filters: PurchaseFilters) -> list[Any]:
        rows = list(
            self.db.execute(
                self._ordered(self.statement(organization_id, filters)).limit(
                    MAX_EXPORT_ROWS + 1
                )
            )
            .mappings()
            .all()
        )
        if len(rows) > MAX_EXPORT_ROWS:
            raise ValueError(
                f"This export exceeds {MAX_EXPORT_ROWS:,} lines. "
                "Narrow the filters and try again."
            )
        return rows
