"""Two-sheet, tenant-scoped AP/AR invoice workbook writer."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any
from uuid import UUID

from fastapi import Response
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from sqlalchemy import select
from sqlalchemy.orm import Session

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LINE_HEADERS = (
    "Invoice ID",
    "Invoice Number",
    "Supplier/Customer",
    "Currency",
    "Line Number",
    "Description",
    "Quantity",
    "Unit Price",
    "Line Amount",
    "Line Tax",
    "Line Total",
)


def _safe_text(value: str) -> str:
    """Keep spreadsheet applications from evaluating user-entered text."""
    value = ILLEGAL_CHARACTERS_RE.sub("", value)
    if value.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    return value


def _cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, int, float)):
        return value
    return _safe_text(str(value))


def build_invoice_xlsx(
    db: Session,
    organization_id: UUID,
    invoices: list[Any],
    invoice_model: Any,
    line_model: Any,
    export_fields: list[tuple[str, str]],
    export_value: Callable[[Any, str], str],
    party_name: Callable[[Any], str],
    filename: str,
    *,
    ar_discounts: bool = False,
    party_header: str = "Supplier/Customer",
) -> Response:
    """Write invoices and only their lines, fetching lines in bounded batches."""
    workbook = Workbook(write_only=True)
    invoice_sheet = workbook.create_sheet("Invoices")
    line_sheet = workbook.create_sheet("Line Items")
    invoice_sheet.append([header for _, header in export_fields] + ["Invoice ID"])
    headers = list(LINE_HEADERS)
    headers[2] = party_header
    if ar_discounts:
        headers += ["Discount Percentage", "Discount Amount"]
    line_sheet.append(headers)

    by_id = {invoice.invoice_id: invoice for invoice in invoices}
    for invoice in invoices:
        row = []
        for field, _ in export_fields:
            raw = getattr(invoice, field, None)
            if isinstance(raw, (Decimal, date, datetime)) or field == "purpose":
                row.append(_cell(raw))
            else:
                row.append(_cell(export_value(invoice, field)))
        invoice_sheet.append(row + [str(invoice.invoice_id)])

    ids = sorted(by_id)
    for start in range(0, len(ids), 500):
        batch = ids[start : start + 500]
        lines: list[Any] = list(
            db.scalars(
                select(line_model)
                .join(invoice_model, line_model.invoice_id == invoice_model.invoice_id)
                .where(
                    invoice_model.organization_id == organization_id,
                    line_model.invoice_id.in_(batch),
                )
                .order_by(line_model.invoice_id, line_model.line_number)
            ).all()
        )
        for line in lines:
            invoice = by_id[line.invoice_id]
            row = [
                str(line.invoice_id),
                invoice.invoice_number,
                party_name(invoice),
                invoice.currency_code,
                line.line_number,
                line.description,
                line.quantity,
                line.unit_price,
                line.line_amount,
                line.tax_amount,
                line.line_amount + line.tax_amount,
            ]
            if ar_discounts:
                row += [line.discount_percentage, line.discount_amount]
            line_sheet.append([_cell(value) for value in row])

    output = BytesIO()
    workbook.save(output)
    return Response(
        content=output.getvalue(),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
