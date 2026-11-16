from __future__ import annotations

import csv
from io import StringIO
from io import BytesIO
from datetime import date
from decimal import Decimal
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4
import sys

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.models.finance.ap.supplier_invoice import SupplierInvoice, SupplierInvoiceType
from app.services.finance.ap.invoice_bulk import APInvoiceBulkService


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_only", [False, True])
async def test_ap_invoice_export_includes_purpose(
    selected_only: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoice_id = uuid4()
    supplier_id = uuid4()
    invoice = SimpleNamespace(
        invoice_id=invoice_id,
        supplier_id=supplier_id,
        invoice_number="INV-001",
        purpose="Office supplies, November\nSecond delivery",
        status=None,
        balance_due=0,
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = [invoice]
    service = APInvoiceBulkService(db, uuid4())
    service._supplier_names = {str(supplier_id): "Example Supplier"}
    if not selected_only:
        query_module = ModuleType("app.services.finance.ap.invoice_query")
        query_module.build_invoice_query = lambda **kwargs: object()  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, query_module.__name__, query_module)

    response = (
        await service.bulk_export([invoice_id], "csv")
        if selected_only
        else await service.export_all(format="csv")
    )

    headers, values = list(csv.reader(StringIO(response.body.decode("utf-8"))))
    purpose_index = headers.index("Purpose")
    assert values[purpose_index] == invoice.purpose
    assert values[headers.index("Invoice Number")] == "INV-001"


@pytest.mark.parametrize(
    ("purpose", "expected"),
    [(None, ""), ("=1+1", "'=1+1")],
)
def test_ap_invoice_export_handles_empty_and_formula_purpose(
    purpose: str | None, expected: str
) -> None:
    service = APInvoiceBulkService(MagicMock(), uuid4())
    invoice = SimpleNamespace(purpose=purpose)

    assert service._get_export_value(invoice, "purpose") == expected


@pytest.mark.asyncio
async def test_selected_ap_invoice_xlsx_is_scoped_and_has_its_lines() -> None:
    organization_id = uuid4()
    invoice_id = uuid4()
    supplier_id = uuid4()
    invoice = SimpleNamespace(
        invoice_id=invoice_id,
        supplier_id=supplier_id,
        invoice_number="AP-1",
        purpose="=unsafe",
        invoice_date=date(2026, 11, 1),
        currency_code="NGN",
        total_amount=Decimal("12"),
        status=None,
        balance_due=Decimal("12"),
    )
    line = SimpleNamespace(
        invoice_id=invoice_id,
        line_number=1,
        description="Materials",
        quantity=Decimal("1"),
        unit_price=Decimal("20"),
        line_amount=Decimal("10"),
        tax_amount=Decimal("2"),
    )
    db = MagicMock()
    db.scalars.side_effect = [
        MagicMock(all=lambda: [invoice]),
        MagicMock(all=lambda: [line]),
    ]
    service = APInvoiceBulkService(db, organization_id)
    service._supplier_names = {str(supplier_id): "Supplier A"}
    response = await service.bulk_export([invoice_id])
    workbook = load_workbook(BytesIO(response.body), read_only=True)
    assert workbook.sheetnames == ["Invoices", "Line Items"]
    assert len(list(workbook["Invoices"].values)) == 2
    assert len(list(workbook["Line Items"].values)) == 2
    assert workbook["Invoices"]["C2"].value == "'=unsafe"
    assert workbook["Line Items"]["K2"].value == 12
    first_query, line_query = [call.args[0] for call in db.scalars.call_args_list]
    assert organization_id in first_query.compile().params.values()
    assert organization_id in line_query.compile().params.values()
    assert [invoice_id] in line_query.compile().params.values()


@pytest.mark.asyncio
async def test_ap_credit_note_export_keeps_fixed_invoice_type(monkeypatch):
    query_module = ModuleType("app.services.finance.ap.invoice_query")
    query_module.build_invoice_query = lambda **kwargs: select(SupplierInvoice)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, query_module.__name__, query_module)
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    service = APInvoiceBulkService(db, uuid4())
    await service.export_all(extra_filters={"invoice_type": "CREDIT_NOTE"})
    statement = db.scalars.call_args.args[0]
    assert SupplierInvoiceType.CREDIT_NOTE in statement.compile().params.values()
