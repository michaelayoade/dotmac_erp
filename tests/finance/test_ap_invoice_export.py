from __future__ import annotations

import csv
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.finance.ap.invoice_bulk import APInvoiceBulkService


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_only", [False, True])
async def test_ap_invoice_export_includes_purpose(selected_only: bool) -> None:
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

    response = (
        await service.bulk_export([invoice_id])
        if selected_only
        else await service.export_all()
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
