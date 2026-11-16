"""Invoice workbook shape, stored amounts, and line scoping."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.models.finance.ap.supplier_invoice import SupplierInvoice
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.ar.invoice import Invoice
from app.models.finance.ar.invoice_line import InvoiceLine
from app.services.finance.invoice_xlsx import XLSX_MEDIA_TYPE, build_invoice_xlsx


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _LineDB:
    def __init__(self, lines):
        self.lines = lines
        self.queries = []

    def scalars(self, statement):
        self.queries.append(statement)
        params = statement.compile().params
        ids = next(value for value in params.values() if isinstance(value, list))
        return _Rows(
            sorted(
                (line for line in self.lines if line.invoice_id in ids),
                key=lambda line: (line.invoice_id, line.line_number),
            )
        )


@pytest.mark.parametrize("ar", [False, True])
def test_invoice_workbook_has_matching_lines_and_typed_values(ar):
    org_id = uuid4()
    invoices = [
        SimpleNamespace(
            invoice_id=uuid4(),
            invoice_number=f"INV-{index}",
            invoice_date=date(2026, 11, index + 1),
            total_amount=Decimal("13.50") if index == 0 else Decimal("0"),
            currency_code="NGN",
            party="=unsafe",
        )
        for index in range(2)
    ]
    # The second invoice is legacy data without lines. A third invoice is
    # outside the exported set and must not contribute a line.
    unrelated_id = uuid4()
    lines = [
        SimpleNamespace(
            invoice_id=invoices[0].invoice_id,
            line_number=number,
            description="\x00=formula" if number == 1 else "Service",
            quantity=Decimal("1"),
            unit_price=Decimal("5"),
            line_amount=Decimal("2"),
            tax_amount=Decimal("0.25"),
            discount_percentage=Decimal("10") if ar else None,
            discount_amount=Decimal("1") if ar else None,
        )
        for number in range(6, 0, -1)
    ]
    lines.append(SimpleNamespace(invoice_id=unrelated_id, line_number=1))
    db = _LineDB(lines)
    fields = [
        ("invoice_number", "Invoice Number"),
        ("invoice_date", "Invoice Date"),
        ("total_amount", "Total Amount"),
    ]
    response = build_invoice_xlsx(
        db,
        org_id,
        invoices,
        Invoice if ar else SupplierInvoice,
        InvoiceLine if ar else SupplierInvoiceLine,
        fields,
        lambda invoice, field: str(getattr(invoice, field)),
        lambda invoice: invoice.party,
        "invoices.xlsx",
        ar_discounts=ar,
    )
    assert response.media_type == XLSX_MEDIA_TYPE
    assert response.body.startswith(b"PK")
    workbook = load_workbook(BytesIO(response.body), read_only=True)
    assert workbook.sheetnames == ["Invoices", "Line Items"]
    invoice_rows = list(workbook["Invoices"].values)
    line_rows = list(workbook["Line Items"].values)
    assert invoice_rows[0] == (
        "Invoice Number",
        "Invoice Date",
        "Total Amount",
        "Invoice ID",
    )
    assert len(invoice_rows) == 3
    assert isinstance(invoice_rows[1][1], date)
    assert isinstance(invoice_rows[1][2], (int, float))
    assert len(line_rows) == 7
    assert {row[0] for row in line_rows[1:]} == {str(invoices[0].invoice_id)}
    assert [row[4] for row in line_rows[1:]] == list(range(1, 7))
    assert line_rows[1][2] == "'=unsafe"
    assert line_rows[1][5] == "'=formula"
    assert sum(row[10] for row in line_rows[1:]) == invoice_rows[1][2]
    assert ("Discount Percentage" in line_rows[0]) == ar
    assert (
        str(org_id) in str(db.queries[0].compile())
        or org_id in db.queries[0].compile().params.values()
    )


def test_empty_invoice_workbook_has_both_headers_and_no_line_query():
    db = _LineDB([])
    response = build_invoice_xlsx(
        db,
        uuid4(),
        [],
        SupplierInvoice,
        SupplierInvoiceLine,
        [("invoice_number", "Invoice Number")],
        lambda invoice, field: "",
        lambda invoice: "",
        "invoices.xlsx",
    )
    workbook = load_workbook(BytesIO(response.body), read_only=True)
    assert list(workbook["Invoices"].values) == [("Invoice Number", "Invoice ID")]
    assert len(list(workbook["Line Items"].values)) == 1
    assert not db.queries
