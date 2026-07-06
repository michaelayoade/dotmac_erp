"""Regression tests for invoice/credit-note tax allocation (C-1 / M-3).

ERP used to re-derive each line's tax from the org's single VAT code
(``_extract_tax``), which posted phantom output VAT on a zero-tax invoice (a
CRM installation invoice, where sub sets ``tax_total = 0``) and left the invoice
header's tax disagreeing with its own line subledger. The fix allocates sub's
authoritative ``subtotal``/``tax_total`` across the lines. These tests pin that
the parts reconcile to the document totals and that zero-tax stays zero.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.services.dotmac_sub.sync._invoices import InvoiceSyncMixin

_alloc = InvoiceSyncMixin._allocate_doc_amounts


def _doc(subtotal: str, tax_total: str) -> SimpleNamespace:
    return SimpleNamespace(subtotal=Decimal(subtotal), tax_total=Decimal(tax_total))


def test_zero_tax_invoice_posts_no_line_tax() -> None:
    """The core bug: a zero-tax invoice must not gain phantom VAT."""
    doc = _doc("10000.00", "0")
    splits = _alloc(None, doc, [Decimal("6000"), Decimal("4000")])

    assert [t for _, t in splits] == [Decimal("0.00"), Decimal("0.00")]
    assert sum(s for s, _ in splits) == Decimal("10000.00")


def test_taxed_single_line_carries_subs_tax_exactly() -> None:
    doc = _doc("10000.00", "750.00")
    splits = _alloc(None, doc, [Decimal("10000")])

    assert splits == [(Decimal("10000.00"), Decimal("750.00"))]


def test_multi_line_parts_reconcile_to_header_with_rounding() -> None:
    """Line subtotals/taxes must sum EXACTLY to the document totals even when
    the proportional split rounds — the last line absorbs the remainder."""
    doc = _doc("10.00", "0.75")
    splits = _alloc(None, doc, [Decimal("1"), Decimal("1"), Decimal("1")])

    assert sum(s for s, _ in splits) == Decimal("10.00")
    assert sum(t for _, t in splits) == Decimal("0.75")
    # No line is silently dropped or doubled.
    assert len(splits) == 3


def test_degenerate_all_zero_line_totals_still_reconciles() -> None:
    doc = _doc("500.00", "37.50")
    splits = _alloc(None, doc, [Decimal("0"), Decimal("0")])

    assert sum(s for s, _ in splits) == Decimal("500.00")
    assert sum(t for _, t in splits) == Decimal("37.50")
