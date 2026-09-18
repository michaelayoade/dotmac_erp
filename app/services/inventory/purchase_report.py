"""Read-only supplier purchases, all invoice lines and calendar reporting periods.

An item master is optional enrichment, never the authority for billed amounts.
Reconciliation counts each matched invoice once and uses ALL its saved lines,
independently of line filters or pagination. No accounting or stock writes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.orm import Session

REPORT_TIMEZONE = ZoneInfo("Africa/Lagos")
PERIODS = ("weekly", "monthly", "quarterly", "yearly")
PAGE_SIZE = 50
MAX_EXPORT_ROWS = 50_000
MIN_REPORT_DATE = date(1900, 1, 1)


@dataclass(frozen=True)
class PurchasePeriod:
    kind: str
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

    @property
    def label(self) -> str:
        if self.kind == "yearly":
            return str(self.start.year)
        if self.kind == "quarterly":
            return f"Q{(self.start.month - 1) // 3 + 1} {self.start.year}"
        if self.kind == "monthly":
            return self.start.strftime("%B %Y")
        return f"{self.start:%d %b %Y} – {self.end:%d %b %Y}"

    @property
    def unit(self) -> str:
        return dict(zip(PERIODS, ("week", "month", "quarter", "year"), strict=True))[
            self.kind
        ]


def resolve_purchase_period(
    kind: str = "weekly", value: str | None = None, *, today: date | None = None
) -> PurchasePeriod:
    if kind not in PERIODS:
        raise ValueError("Choose weekly, monthly, quarterly, or yearly reporting.")
    today = today or datetime.now(REPORT_TIMEZONE).date()
    try:
        selected = date.fromisoformat(value) if value else today
        if value and selected.isoformat() != value:
            raise ValueError("Non-canonical date")
    except ValueError as exc:
        raise ValueError("Choose a valid date in YYYY-MM-DD format.") from exc
    if selected < MIN_REPORT_DATE or selected.year >= 9999:
        raise ValueError("Choose a date between 1900 and 9998.")
    if kind == "weekly":
        start = selected - timedelta(days=selected.weekday())
        end = start + timedelta(days=6)
    else:
        month = selected.month
        if kind == "quarterly":
            month = (month - 1) // 3 * 3 + 1
        elif kind == "yearly":
            month = 1
        start = date(selected.year, month, 1)
        months = {"monthly": 1, "quarterly": 3, "yearly": 12}[kind]
        next_month = month - 1 + months
        end = date(
            selected.year + next_month // 12, next_month % 12 + 1, 1
        ) - timedelta(days=1)
    if start > today:
        raise ValueError("Future reporting periods are not available.")
    return PurchasePeriod(kind, start, end, min(end, today), today)


def _identifier(value: str | None, label: str, *, unspecified: bool = False) -> str:
    if not value:
        return ""
    if unspecified and value == "unspecified":
        return value
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Choose a valid {label}.") from exc


def _year(value: str) -> int:
    if not value.isdecimal() or len(value) != 4 or not 1900 <= int(value) < 9999:
        raise ValueError("Choose a year between 1900 and 9998.")
    return int(value)


@dataclass(frozen=True)
class PurchaseReportFilters:
    period: PurchasePeriod
    supplier: str = ""
    warehouse: str = ""
    category: str = ""
    search: str = ""

    @classmethod
    def parse(
        cls,
        *,
        period: str | None = None,
        period_start: str | None = None,
        week_start: str | None = None,
        month: str | None = None,
        quarter: str | None = None,
        year: str | None = None,
        supplier: str | None = None,
        warehouse: str | None = None,
        category: str | None = None,
        search: str | None = None,
        today: date | None = None,
    ) -> PurchaseReportFilters:
        kind = period or "weekly"
        today = today or datetime.now(REPORT_TIMEZONE).date()
        if week_start and (
            kind != "weekly" or (period_start and period_start != week_start)
        ):
            raise ValueError("The legacy week_start parameter is only for weekly reports.")
        anchor = period_start or week_start
        # Form-specific selectors override the hidden anchor; selectors belonging
        # to a previous period type are ignored when the user switches the type.
        if kind == "monthly" and month:
            if len(month) != 7 or month[4] != "-":
                raise ValueError("Choose a month in YYYY-MM format.")
            anchor = month + "-01"
        elif kind == "quarterly" and quarter:
            if quarter not in {"1", "2", "3", "4"}:
                raise ValueError("Choose a quarter between 1 and 4.")
            selected_year = _year(year or str(today.year))
            anchor = date(selected_year, (int(quarter) - 1) * 3 + 1, 1).isoformat()
        elif kind == "yearly" and year:
            anchor = date(_year(year), 1, 1).isoformat()
        search = (search or "").strip()
        if len(search) > 100:
            raise ValueError("Search must not exceed 100 characters.")
        return cls(
            resolve_purchase_period(kind, anchor, today=today),
            _identifier(supplier, "supplier"),
            _identifier(warehouse, "warehouse", unspecified=True),
            _identifier(category, "category", unspecified=True),
            search,
        )

    def query_params(self) -> dict[str, str]:
        params = {
            "period": self.period.kind,
            "period_start": self.period.start.isoformat(),
        }
        for key in ("supplier", "warehouse", "category", "search"):
            value = getattr(self, key)
            if value:
                params[key] = value
        return params


def _signed(kind: Any, amount: Any) -> Any:
    return case((kind == "CREDIT_NOTE", -func.abs(amount)), else_=amount)


class PurchaseReportService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def statement(organization_id: UUID, filters: PurchaseReportFilters) -> Select[Any]:
        from app.models.finance.ap.goods_receipt import GoodsReceipt
        from app.models.finance.ap.goods_receipt_line import GoodsReceiptLine
        from app.models.finance.ap.supplier import Supplier
        from app.models.finance.ap.supplier_invoice import (
            PostingStatus,
            SupplierInvoice,
            SupplierInvoiceStatus,
        )
        from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
        from app.models.inventory.item import Item
        from app.models.inventory.item_category import ItemCategory
        from app.models.inventory.warehouse import Warehouse

        if not isinstance(organization_id, UUID):
            raise ValueError("An organization is required for purchase reporting.")
        invoice, line = SupplierInvoice, SupplierInvoiceLine
        warehouse_id = func.coalesce(
            line.receipt_warehouse_id, GoodsReceipt.warehouse_id
        )
        stmt = (
            select(
                invoice.organization_id,
                invoice.invoice_id,
                invoice.invoice_number,
                invoice.invoice_date,
                invoice.invoice_type,
                invoice.status,
                invoice.currency_code,
                line.line_id,
                line.line_number,
                line.description,
                line.quantity,
                line.unit_price,
                _signed(invoice.invoice_type, line.line_amount).label("net_amount"),
                _signed(invoice.invoice_type, line.tax_amount).label("net_tax"),
                Supplier.supplier_id,
                Supplier.legal_name.label("supplier_name"),
                Item.item_id,
                Item.item_code,
                Item.item_name,
                case(
                    (line.item_id.is_(None), "Not linked to an item"),
                    (Item.item_id.is_(None), "Linked item unavailable"),
                    else_="Registered item",
                ).label("item_link_status"),
                ItemCategory.category_id,
                ItemCategory.category_name,
                Warehouse.warehouse_id,
                Warehouse.warehouse_name,
            )
            .select_from(line)
            .join(invoice, invoice.invoice_id == line.invoice_id)
            .outerjoin(
                Item,
                and_(
                    Item.item_id == line.item_id,
                    Item.organization_id == organization_id,
                ),
            )
            .outerjoin(
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
                invoice.invoice_date >= filters.period.start,
                invoice.invoice_date < filters.period.end_exclusive,
                invoice.is_prepayment.is_(False),
                or_(
                    invoice.status.in_(SupplierInvoiceStatus.gl_impacting()),
                    and_(
                        invoice.status.in_(
                            [SupplierInvoiceStatus.ON_HOLD, SupplierInvoiceStatus.DISPUTED]
                        ),
                        invoice.posting_status == PostingStatus.POSTED,
                    ),
                ),
            )
        )
        if filters.supplier:
            stmt = stmt.where(Supplier.supplier_id == UUID(filters.supplier))
        for value, column in (
            (filters.category, ItemCategory.category_id),
            (filters.warehouse, Warehouse.warehouse_id),
        ):
            if value == "unspecified":
                stmt = stmt.where(column.is_(None))
            elif value:
                stmt = stmt.where(column == UUID(value))
        if filters.search:
            stmt = stmt.where(
                or_(
                    *(
                        column.icontains(filters.search, autoescape=True)
                        for column in (
                            line.description,
                            Item.item_code,
                            Item.item_name,
                            Supplier.legal_name,
                            invoice.invoice_number,
                        )
                    )
                )
            )
        return stmt

    @staticmethod
    def _ordered(stmt: Select[Any]) -> Select[Any]:
        c = stmt.selected_columns
        return stmt.order_by(
            c.invoice_date.desc(), c.invoice_number, c.line_number, c.line_id
        )

    def reconciliation_statement(
        self, organization_id: UUID, filters: PurchaseReportFilters
    ) -> Select[Any]:
        from app.models.finance.ap.supplier_invoice import SupplierInvoice
        from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine

        invoice, line = SupplierInvoice, SupplierInvoiceLine
        source = self.statement(organization_id, filters).subquery()
        matched = (
            select(
                source.c.invoice_id,
                func.count().label("matching_line_count"),
                func.sum(source.c.net_amount + source.c.net_tax).label("matching_total"),
            )
            .where(source.c.organization_id == organization_id)
            .group_by(source.c.invoice_id)
            .subquery()
        )
        # Aggregate the entire invoice BEFORE joining its matched-line summary.
        # SUM(DISTINCT invoice.total_amount) is NOT valid: different invoices can
        # legitimately have equal totals. The one-row-per-invoice join is key.
        full = (
            select(
                line.invoice_id,
                func.count().label("full_line_count"),
                func.sum(
                    _signed(invoice.invoice_type, line.line_amount)
                    + _signed(invoice.invoice_type, line.tax_amount)
                ).label("full_line_total"),
            )
            .select_from(line)
            .join(invoice, invoice.invoice_id == line.invoice_id)
            .join(matched, matched.c.invoice_id == invoice.invoice_id)
            .where(invoice.organization_id == organization_id)
            .group_by(line.invoice_id)
            .subquery()
        )
        header_total = _signed(invoice.invoice_type, invoice.total_amount)
        return (
            select(
                invoice.organization_id,
                invoice.invoice_id,
                invoice.invoice_number,
                invoice.invoice_date,
                invoice.currency_code,
                matched.c.matching_line_count,
                matched.c.matching_total,
                full.c.full_line_count,
                full.c.full_line_total,
                header_total.label("invoice_total"),
                (header_total - full.c.full_line_total).label("difference"),
            )
            .select_from(invoice)
            .join(matched, matched.c.invoice_id == invoice.invoice_id)
            .join(full, full.c.invoice_id == invoice.invoice_id)
            .where(invoice.organization_id == organization_id)
        )

    def report(
        self, organization_id: UUID, filters: PurchaseReportFilters, *, page: int = 1
    ) -> dict[str, Any]:
        if page < 1:
            raise ValueError("Page must be at least 1.")
        stmt = self.statement(organization_id, filters)
        s = stmt.subquery()
        summary = dict(
            self.db.execute(
                select(
                    func.count().label("line_count"),
                    func.count(func.distinct(s.c.invoice_id)).label("document_count"),
                    func.count(func.distinct(s.c.item_id)).label("item_count"),
                    func.count(func.distinct(s.c.supplier_id)).label("supplier_count"),
                    func.count(case((s.c.item_id.is_(None), 1))).label("unlinked_count"),
                ).where(s.c.organization_id == organization_id)
            ).mappings().one()
        )
        pages = max(1, (summary["line_count"] + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        rows = list(
            self.db.execute(
                self._ordered(stmt).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
            ).mappings().all()
        )
        credit = s.c.invoice_type == "CREDIT_NOTE"
        totals = [
            dict(row)
            for row in self.db.execute(
                select(
                    s.c.currency_code,
                    func.sum(case((credit, 0), else_=s.c.net_amount)).label("purchases"),
                    func.sum(case((credit, -s.c.net_amount), else_=0)).label("credits"),
                    func.sum(s.c.net_amount).label("net"),
                    func.sum(s.c.net_tax).label("tax"),
                    func.sum(s.c.net_amount + s.c.net_tax).label("matching_total"),
                )
                .where(s.c.organization_id == organization_id)
                .group_by(s.c.currency_code)
                .order_by(s.c.currency_code)
            ).mappings().all()
        ]
        reconciliation = self.reconciliation_statement(organization_id, filters)
        r = reconciliation.subquery()
        invoice_totals = {
            row["currency_code"]: dict(row)
            for row in self.db.execute(
                select(
                    r.c.currency_code,
                    func.sum(r.c.full_line_total).label("full_line_total"),
                    func.sum(r.c.invoice_total).label("invoice_total"),
                    func.sum(r.c.difference).label("difference"),
                    func.count(case((r.c.difference != 0, 1))).label("mismatch_count"),
                    func.count(
                        case((r.c.matching_line_count < r.c.full_line_count, 1))
                    ).label("partial_count"),
                )
                .where(r.c.organization_id == organization_id)
                .group_by(r.c.currency_code)
            ).mappings().all()
        }
        for total in totals:
            total.update(invoice_totals[total["currency_code"]])
        visible_ids = {row["invoice_id"] for row in rows}
        invoice_rows = []
        if visible_ids:
            from app.models.finance.ap.supplier_invoice import SupplierInvoice

            invoice_rows = list(
                self.db.execute(
                    reconciliation.where(SupplierInvoice.invoice_id.in_(visible_ids))
                    .order_by(
                        SupplierInvoice.invoice_date.desc(),
                        SupplierInvoice.invoice_number,
                        SupplierInvoice.invoice_id,
                    )
                ).mappings().all()
            )
        return {
            "purchase_rows": rows,
            "invoice_rows": invoice_rows,
            "summary": summary,
            "currency_totals": totals,
            "total_count": summary["line_count"],
            "page": page,
            "total_pages": pages,
        }

    def options(
        self, organization_id: UUID, filters: PurchaseReportFilters
    ) -> dict[str, list[dict[str, str]]]:
        source = self.statement(
            organization_id,
            replace(filters, supplier="", warehouse="", category="", search=""),
        ).subquery()
        choices = {}
        for name, identifier, label in (
            ("suppliers", source.c.supplier_id, source.c.supplier_name),
            ("warehouses", source.c.warehouse_id, source.c.warehouse_name),
            ("categories", source.c.category_id, source.c.category_name),
        ):
            rows = self.db.execute(
                select(identifier.label("id"), label.label("label"))
                .where(
                    source.c.organization_id == organization_id,
                    identifier.is_not(None),
                )
                .distinct()
                .order_by(label, identifier)
            ).mappings().all()
            choices[name] = [
                {"id": str(row["id"]), "label": str(row["label"])} for row in rows
            ]
        return choices

    def export_rows(
        self, organization_id: UUID, filters: PurchaseReportFilters, *, view: str = "lines"
    ) -> list[Any]:
        if view == "lines":
            stmt = self._ordered(self.statement(organization_id, filters))
        elif view == "invoices":
            stmt = self.reconciliation_statement(organization_id, filters)
            c = stmt.selected_columns
            stmt = stmt.order_by(c.invoice_date.desc(), c.invoice_number, c.invoice_id)
        else:
            raise ValueError("Choose a lines or invoices export.")
        rows = list(self.db.execute(stmt.limit(MAX_EXPORT_ROWS + 1)).mappings().all())
        if len(rows) > MAX_EXPORT_ROWS:
            raise ValueError(
                f"This export exceeds {MAX_EXPORT_ROWS:,} rows. Narrow the filters."
            )
        return rows
