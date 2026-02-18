from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.services.finance.ap.web import base as ap_base


def test_invoice_line_view_includes_tax_metadata_fields(monkeypatch):
    monkeypatch.setattr(
        ap_base,
        "format_currency",
        lambda amount, currency=None: f"{amount}:{currency}",
    )

    line = SimpleNamespace(
        line_id=uuid4(),
        line_number=1,
        description="Line",
        quantity=Decimal("2"),
        unit_price=Decimal("50"),
        tax_amount=Decimal("7.5"),
        tax_code_id=uuid4(),
        line_amount=Decimal("100"),
        expense_account_id=uuid4(),
        asset_account_id=None,
        cost_center_id=None,
        project_id=None,
    )

    view = ap_base.invoice_line_view(line, "USD")
    assert view["tax_amount"] == "7.5:USD"
    assert view["tax_amount_raw"] == 7.5
    assert view["tax_code_id"] == line.tax_code_id
