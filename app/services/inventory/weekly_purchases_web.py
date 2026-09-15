"""HTML and CSV adapters for the read-only weekly purchases report."""

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

from app.services.inventory.weekly_purchases import (
    MIN_REPORT_DATE,
    PurchaseFilters,
    WeeklyPurchasesService,
    csv_text,
    format_report_number,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

REPORT_URL = "/inventory/reports/weekly-purchases"


def report_url(params: dict[str, str], **changes: str) -> str:
    return f"{REPORT_URL}?{urlencode({**params, **changes})}"


class WeeklyPurchasesWebService:
    def __init__(self, db: Session):
        self.db = db
        self.service = WeeklyPurchasesService(db)

    @staticmethod
    def _organization(auth: WebAuthContext) -> UUID:
        if auth.organization_id is None:
            raise HTTPException(status_code=403, detail="Organization context required")
        return auth.organization_id

    @staticmethod
    def _filters(raw: dict[str, str | None]) -> PurchaseFilters:
        try:
            return PurchaseFilters.parse(
                week_start=raw.get("week_start"),
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
        params = selected.query_params()
        week = selected.week
        context = base_context(request, auth, "Weekly Purchases", "reports", db=self.db)
        context.update(self.service.report(organization_id, selected, page=page))
        choices = self.service.options(organization_id, selected)
        for name, key in (
            ("suppliers", "supplier"),
            ("warehouses", "warehouse"),
            ("categories", "category"),
        ):
            value = params.get(key)
            if value and not any(option["id"] == value for option in choices[name]):
                choices[name].append(
                    {"id": value, "label": "Selected filter (no purchases this week)"}
                )
        context.update(choices)
        previous_week = week.start - timedelta(days=7)
        next_week = week.start + timedelta(days=7)
        context.update(
            {
                "purchase_week": week,
                "purchase_filters": params,
                "format_report_number": format_report_number,
                "can_view_invoice": (
                    auth.has_module_access("finance")
                    and auth.has_permission("ap:invoices:read")
                ),
                "export_url": f"{REPORT_URL}/export?{urlencode(params)}",
                "reset_url": report_url({"week_start": week.start.isoformat()}),
                "current_week_url": report_url(
                    params,
                    week_start=(
                        week.today - timedelta(days=week.today.weekday())
                    ).isoformat(),
                ),
                "previous_week_url": (
                    report_url(params, week_start=previous_week.isoformat())
                    if previous_week >= MIN_REPORT_DATE
                    else None
                ),
                "next_week_url": (
                    report_url(params, week_start=next_week.isoformat())
                    if next_week <= week.today
                    else None
                ),
                "previous_page_url": report_url(params, page=str(context["page"] - 1)),
                "next_page_url": report_url(params, page=str(context["page"] + 1)),
            }
        )
        response = templates.TemplateResponse(
            request, "inventory/report_weekly_purchases.html", context
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def export_response(
        self, auth: WebAuthContext, filters: dict[str, str | None]
    ) -> Response:
        organization_id = self._organization(auth)
        selected = self._filters(filters)
        try:
            rows = self.service.export_rows(organization_id, selected)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "Week start",
                "Through date",
                "Invoice date",
                "Invoice number",
                "Document type",
                "Status",
                "Supplier",
                "Item code",
                "Item name",
                "Category",
                "Receipt warehouse",
                "Recorded invoice quantity",
                "Unit price",
                "Currency",
                "Net amount excluding tax",
                "Net tax",
                "Net amount including tax",
            ]
        )
        for row in rows:
            amount = Decimal(row["net_amount"])
            tax = Decimal(row["net_tax"])
            writer.writerow(
                [
                    selected.week.start.isoformat(),
                    selected.week.through.isoformat(),
                    row["invoice_date"].isoformat(),
                    csv_text(row["invoice_number"]),
                    row["invoice_type"].value,
                    row["status"].value,
                    csv_text(row["supplier_name"]),
                    csv_text(row["item_code"]),
                    csv_text(row["item_name"]),
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
        filename = f"inventory-weekly-purchases-{selected.week.start.isoformat()}.csv"
        return Response(
            content="\ufeff" + output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
