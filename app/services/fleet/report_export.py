"""Excel and PDF downloads for filtered single-vehicle Fleet reports."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any, cast

from fastapi import Response
from jinja2 import Environment, FileSystemLoader
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from app.services.formatters import format_currency_compact, format_date_display

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _safe_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, int, float)):
        return value
    text = ILLEGAL_CHARACTERS_RE.sub("", str(value))
    if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + text
    return text


def _report_data(context: dict[str, Any], kind: str) -> dict[str, Any]:
    is_expense = kind == "expense"
    rows = context["claim_rows"] if is_expense else context["invoice_rows"]
    return {
        "title": "Vehicle Expense Report" if is_expense else "Vehicle Invoice Report",
        "count_label": "Total Claims" if is_expense else "Total Invoices",
        "breakdown_label": "Category Breakdown"
        if is_expense
        else "Expense Account Breakdown",
        "reference_label": "Claim" if is_expense else "Invoice",
        "count": context["claim_count"] if is_expense else context["invoice_count"],
        "rows": [
            {
                "reference": row["claim_number"]
                if is_expense
                else row["invoice_number"],
                "date": row["claim_date"] if is_expense else row["invoice_date"],
                "status": row["status"],
                "amount": row["total_amount"],
            }
            for row in rows
        ],
        **context,
    }


def _build_xlsx(data: dict[str, Any]) -> bytes:
    workbook = Workbook(write_only=True)
    summary = workbook.create_sheet("Summary")
    summary.append(["Field", "Value"])
    for label, value in (
        ("Report", data["title"]),
        ("Vehicle Plate Number", data["vehicle"].registration_number),
        ("Vehicle Name", data["vehicle_name"]),
        ("Assigned To", data["assigned_to"]),
        ("Start Date", data["filter_start_date"]),
        ("End Date", data["filter_end_date"]),
        ("Year", data["filter_year"]),
        ("Month", data["filter_month"]),
        (data["count_label"], data["count"]),
        ("Total Amount", data["total_amount"]),
    ):
        summary.append([label, _safe_cell(value)])

    breakdown = workbook.create_sheet("Breakdown")
    breakdown.append(
        [
            "Category" if data["reference_label"] == "Claim" else "Account",
            "Total Amount",
        ]
    )
    for row in data["category_rows"]:
        breakdown.append(
            [_safe_cell(row["category_name"]), _safe_cell(row["total_amount"])]
        )

    details = workbook.create_sheet(
        "Claims" if data["reference_label"] == "Claim" else "Invoices"
    )
    details.append([data["reference_label"], "Date", "Status", "Total Amount"])
    for row in data["rows"]:
        details.append(
            [
                _safe_cell(row["reference"]),
                _safe_cell(row["date"]),
                _safe_cell(row["status"]),
                _safe_cell(row["amount"]),
            ]
        )

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _render_pdf_html(data: dict[str, Any]) -> str:
    template_dir = Path(__file__).resolve().parents[3] / "templates"
    env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
    env.filters["report_money"] = lambda value: format_currency_compact(
        value, none_value="0.00", decimal_places=2
    )
    env.filters["report_date"] = format_date_display
    return env.get_template("fleet/reports/vehicle_export_pdf.html").render(**data)


def _build_pdf(data: dict[str, Any]) -> bytes:
    from weasyprint import HTML

    return cast(bytes, HTML(string=_render_pdf_html(data)).write_pdf())


def vehicle_report_export_response(
    context: dict[str, Any], kind: str, format: str
) -> Response:
    """Return a download for the full filtered report context."""
    data = _report_data(context, kind)
    plate = re.sub(r"[^A-Za-z0-9_-]+", "-", data["vehicle"].registration_number).strip(
        "-"
    )[:64] or str(data["vehicle"].vehicle_id)
    filename = f"fleet-{kind}-report-{plate}.{format}"
    content = _build_xlsx(data) if format == "xlsx" else _build_pdf(data)
    media_type = XLSX_MEDIA_TYPE if format == "xlsx" else "application/pdf"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
