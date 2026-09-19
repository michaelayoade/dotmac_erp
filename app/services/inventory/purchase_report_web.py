"""HTML/CSV adapters for calendar-period purchase and invoice reconciliation reports."""

from __future__ import annotations

import csv
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from urllib.parse import urlencode
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.services.inventory.purchase_report import (
    MIN_REPORT_DATE,
    PERIODS,
    PurchaseReportFilters,
    PurchaseReportService,
    resolve_purchase_period,
)
from app.services.inventory.weekly_purchases import csv_text, format_report_number
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

REPORT_URL = "/inventory/reports/purchases"


def report_url(params: dict[str, str], **changes: str) -> str:
    return f"{REPORT_URL}?{urlencode({**params, **changes})}"


class PurchaseReportWebService:
    def __init__(self, db: Session):
        self.db = db
        self.service = PurchaseReportService(db)

    @staticmethod
    def _organization(auth: WebAuthContext) -> UUID:
        # All-line reporting includes supplier expenses, not only stock costs.
        # Apply the AP gate to HTML, both CSV views, and the legacy weekly aliases.
        if auth.organization_id is None:
            raise HTTPException(status_code=403, detail="Organization context required")
        if not auth.has_permission("ap:invoices:read"):
            raise HTTPException(
                status_code=403,
                detail="AP invoice read permission is required for the Purchase Report",
            )
        return auth.organization_id

    @staticmethod
    def _filters(raw: dict[str, str | None]) -> PurchaseReportFilters:
        try:
            return PurchaseReportFilters.parse(
                period=raw.get("period"),
                period_start=raw.get("period_start"),
                week_start=raw.get("week_start"),
                month=raw.get("month"),
                quarter=raw.get("quarter"),
                year=raw.get("year"),
                supplier=raw.get("supplier"),
                warehouse=raw.get("warehouse"),
                category=raw.get("category"),
                search=raw.get("search"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        filters: dict[str, str | None],
        page: int = 1,
    ) -> HTMLResponse:
        organization_id = self._organization(auth)
        selected = self._filters(filters)
        params, period = selected.query_params(), selected.period
        context = base_context(request, auth, "Purchase Report", "reports", db=self.db)
        context.update(self.service.report(organization_id, selected, page=page))
        choices = self.service.options(organization_id, selected)
        for name, key in (
            ("suppliers", "supplier"),
            ("warehouses", "warehouse"),
            ("categories", "category"),
        ):
            value = params.get(key)
            if (
                value
                and value != "unspecified"
                and not any(option["id"] == value for option in choices[name])
            ):
                choices[name].append(
                    {
                        "id": value,
                        "label": "Selected filter (no purchases in this period)",
                    }
                )
        context.update(choices)
        previous_url = None
        if period.start > MIN_REPORT_DATE:
            previous = resolve_purchase_period(
                period.kind,
                (period.start - timedelta(days=1)).isoformat(),
                today=period.today,
            )
            previous_url = report_url(params, period_start=previous.start.isoformat())
        next_start = period.end + timedelta(days=1)
        current = resolve_purchase_period(period.kind, today=period.today)
        context.update(
            {
                "purchase_period": period,
                "period_options": PERIODS,
                "purchase_filters": params,
                "format_report_number": format_report_number,
                "can_view_invoice": auth.has_module_access("finance"),
                "export_url": f"{REPORT_URL}/export?{urlencode(params)}",
                "invoice_export_url": (
                    f"{REPORT_URL}/export?{urlencode({**params, 'view': 'invoices'})}"
                ),
                "reset_url": report_url(
                    {"period": period.kind, "period_start": period.start.isoformat()}
                ),
                "current_period_url": report_url(
                    params, period_start=current.start.isoformat()
                ),
                "previous_period_url": previous_url,
                "next_period_url": (
                    report_url(params, period_start=next_start.isoformat())
                    if next_start <= period.today
                    else None
                ),
                "previous_page_url": report_url(params, page=str(context["page"] - 1)),
                "next_page_url": report_url(params, page=str(context["page"] + 1)),
            }
        )
        response = templates.TemplateResponse(
            request, "inventory/report_purchases.html", context
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def export_response(
        self,
        auth: WebAuthContext,
        filters: dict[str, str | None],
        view: str = "lines",
    ) -> Response:
        organization_id = self._organization(auth)
        selected = self._filters(filters)
        try:
            rows = self.service.export_rows(organization_id, selected, view=view)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        period = selected.period
        output = StringIO(newline="")
        writer = csv.writer(output)
        period_headers = ["Period", "Period start", "Period end", "Through date"]
        period_values = [
            period.kind,
            period.start.isoformat(),
            period.end.isoformat(),
            period.through.isoformat(),
        ]
        if view == "invoices":
            writer.writerow(
                period_headers
                + [
                    "Invoice date",
                    "Invoice number",
                    "Currency",
                    "Matching line count",
                    "Full invoice line count",
                    "Matching lines including tax",
                    "All invoice lines including tax",
                    "Stored invoice total",
                    "Reconciliation difference",
                ]
            )
            for row in rows:
                writer.writerow(
                    period_values
                    + [
                        row["invoice_date"].isoformat(),
                        csv_text(row["invoice_number"]),
                        csv_text(row["currency_code"]),
                        row["matching_line_count"],
                        row["full_line_count"],
                        *(
                            format(Decimal(row[key]), "f")
                            for key in (
                                "matching_total",
                                "full_line_total",
                                "invoice_total",
                                "difference",
                            )
                        ),
                    ]
                )
        else:
            writer.writerow(
                period_headers
                + [
                    "Invoice date",
                    "Invoice number",
                    "Document type",
                    "Status",
                    "Supplier",
                    "Invoice line",
                    "Invoice description",
                    "Item code",
                    "Registered item name",
                    "Item link status",
                    "Category",
                    "Receipt warehouse",
                    "Recorded quantity",
                    "Unit price",
                    "Currency",
                    "Net amount excluding tax",
                    "Net tax",
                    "Net amount including tax",
                ]
            )
            for row in rows:
                amount, tax = Decimal(row["net_amount"]), Decimal(row["net_tax"])
                writer.writerow(
                    period_values
                    + [
                        row["invoice_date"].isoformat(),
                        csv_text(row["invoice_number"]),
                        row["invoice_type"].value,
                        row["status"].value,
                        csv_text(row["supplier_name"]),
                        row["line_number"],
                        csv_text(row["description"]),
                        csv_text(row["item_code"]),
                        csv_text(row["item_name"]),
                        csv_text(row["item_link_status"]),
                        csv_text(row["category_name"]),
                        csv_text(row["warehouse_name"]),
                        format(Decimal(row["quantity"]), "f"),
                        format(Decimal(row["unit_price"]), "f"),
                        csv_text(row["currency_code"]),
                        format(amount, "f"),
                        format(tax, "f"),
                        format(amount + tax, "f"),
                    ]
                )
        filename = f"inventory-purchases-{period.kind}-{period.start}-{view}.csv"
        return Response(
            content="\ufeff" + output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
