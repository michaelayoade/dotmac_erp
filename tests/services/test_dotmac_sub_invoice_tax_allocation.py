"""Tax-contract tests for Sub invoice and credit-note projection.

Sub owns the line-level taxable fact (rate id and inclusive/exclusive/exempt
treatment). ERP owns the matching TaxCode and its GL accounts. The importer
must reproduce Sub's arithmetic exactly and fail closed on a mismatch.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.dotmac_sub.client import (
    InvoiceLineRecord,
    InvoiceRecord,
    TaxApplication,
)
from app.services.dotmac_sub.sync._invoices import InvoiceSyncMixin


class _Harness(InvoiceSyncMixin):
    def __init__(self) -> None:
        self.default_revenue_account_id = uuid4()
        self.tax_code = SimpleNamespace(
            tax_code_id=uuid4(), tax_rate=Decimal("0.075"), is_inclusive=False
        )

    @staticmethod
    def _get_source_tax_rate(_source_id: str) -> SimpleNamespace:
        return SimpleNamespace(rate=Decimal("7.5"), name="VAT 7.5%")

    def _resolve_source_sales_tax_code(self, **_kwargs: object) -> object:
        return self.tax_code


def _line(
    amount: str,
    *,
    application: TaxApplication = TaxApplication.EXCLUSIVE,
    tax_rate_id: str | None = "vat-75",
) -> InvoiceLineRecord:
    return InvoiceLineRecord(
        id=str(uuid4()),
        description="Service",
        quantity=Decimal("1"),
        unit_price=Decimal(amount),
        amount=Decimal(amount),
        tax_rate_id=tax_rate_id,
        tax_application=application,
    )


def _invoice(
    *, subtotal: str, tax: str, total: str, lines: tuple[InvoiceLineRecord, ...]
) -> InvoiceRecord:
    return InvoiceRecord(
        id="inv-1",
        account_id="sub-1",
        invoice_number="INV-1",
        status="issued",
        currency="NGN",
        subtotal=Decimal(subtotal),
        tax_total=Decimal(tax),
        total=Decimal(total),
        balance_due=Decimal(total),
        issued_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        lines=lines,
    )


def test_exempt_source_line_never_gains_phantom_vat() -> None:
    harness = _Harness()
    doc = _invoice(
        subtotal="10000.00",
        tax="0",
        total="10000.00",
        lines=(_line("10000", application=TaxApplication.EXEMPT, tax_rate_id=None),),
    )

    projected = harness._project_source_lines(doc, is_credit_note=False)

    assert projected[0][1:3] == (Decimal("10000.00"), Decimal("0"))
    assert projected[0][3] is None


def test_exclusive_source_tax_is_reproduced_exactly() -> None:
    harness = _Harness()
    doc = _invoice(subtotal="100.00", tax="7.50", total="107.50", lines=(_line("100"),))

    projected = harness._project_source_lines(doc, is_credit_note=False)

    assert projected[0][1:3] == (Decimal("100.00"), Decimal("7.50"))
    assert projected[0][3] is harness.tax_code


def test_inclusive_source_tax_is_split_into_base_and_tax() -> None:
    harness = _Harness()
    doc = _invoice(
        subtotal="100.00",
        tax="7.50",
        total="107.50",
        lines=(_line("107.50", application=TaxApplication.INCLUSIVE),),
    )

    projected = harness._project_source_lines(doc, is_credit_note=False)

    assert projected[0][1:3] == (Decimal("100.00"), Decimal("7.50"))


def test_mixed_tax_lines_must_reconcile_to_source_header() -> None:
    harness = _Harness()
    doc = _invoice(
        subtotal="150.00",
        tax="7.50",
        total="157.50",
        lines=(
            _line("100"),
            _line("50", application=TaxApplication.EXEMPT, tax_rate_id=None),
        ),
    )

    projected = harness._project_source_lines(doc, is_credit_note=False)

    assert sum(item[1] for item in projected) == Decimal("150.00")
    assert sum(item[2] for item in projected) == Decimal("7.50")


def test_tax_header_mismatch_fails_closed() -> None:
    harness = _Harness()
    doc = _invoice(subtotal="100.00", tax="8.00", total="108.00", lines=(_line("100"),))

    with pytest.raises(ValueError, match="do not reconcile"):
        harness._project_source_lines(doc, is_credit_note=False)
