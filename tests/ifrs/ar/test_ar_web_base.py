from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.models.finance.ar.customer import CustomerType
from app.models.finance.ar.customer_payment import PaymentStatus
from app.models.finance.ar.invoice import InvoiceStatus
from app.services.finance.ar.web import base as ar_base


def test_parse_customer_type_defaults_and_parses():
    assert ar_base.parse_customer_type(None) == CustomerType.COMPANY
    assert ar_base.parse_customer_type("UNKNOWN") == CustomerType.COMPANY
    assert ar_base.parse_customer_type("INDIVIDUAL") == CustomerType.INDIVIDUAL


def test_parse_invoice_status_handles_partial_and_invalid():
    assert ar_base.parse_invoice_status(None) is None
    assert ar_base.parse_invoice_status("PARTIAL") == InvoiceStatus.PARTIALLY_PAID
    assert ar_base.parse_invoice_status("APPLIED") == InvoiceStatus.PAID
    assert ar_base.parse_invoice_status("VOIDED") == InvoiceStatus.VOID
    assert ar_base.parse_invoice_status("POSTED") == InvoiceStatus.POSTED
    assert ar_base.parse_invoice_status("BAD") is None


def test_parse_receipt_status_maps_known_values():
    assert ar_base.parse_receipt_status(None) is None
    assert ar_base.parse_receipt_status("DRAFT") == PaymentStatus.PENDING
    assert ar_base.parse_receipt_status("POSTED") == PaymentStatus.CLEARED
    assert ar_base.parse_receipt_status("VOIDED") == PaymentStatus.VOID
    assert ar_base.parse_receipt_status("CANCELLED") == PaymentStatus.VOID
    assert ar_base.parse_receipt_status("BAD") is None


def test_normalize_date_range_filters_uses_aliases():
    start_date, end_date = ar_base.normalize_date_range_filters(
        None,
        None,
        {"from_date": "2026-01-01", "to_date": "2026-01-31"},
    )
    assert start_date == "2026-01-01"
    assert end_date == "2026-01-31"


def test_normalize_date_range_filters_prefers_explicit_values():
    start_date, end_date = ar_base.normalize_date_range_filters(
        "2026-02-01",
        "2026-02-28",
        {"from_date": "2026-01-01", "to_date": "2026-01-31"},
    )
    assert start_date == "2026-02-01"
    assert end_date == "2026-02-28"


def test_status_label_helpers():
    assert ar_base.invoice_status_label(InvoiceStatus.PARTIALLY_PAID) == "PARTIAL"
    assert ar_base.invoice_status_label(InvoiceStatus.POSTED) == "POSTED"

    assert ar_base.receipt_status_label(PaymentStatus.CLEARED) == "POSTED"
    assert ar_base.receipt_status_label(PaymentStatus.PENDING) == "DRAFT"
    assert ar_base.receipt_status_label(PaymentStatus.REVERSED) == "VOIDED"


def test_customer_display_name_prefers_trading_name():
    customer = SimpleNamespace(trading_name="Acme", legal_name="Acme Limited")
    assert ar_base.customer_display_name(customer) == "Acme"

    customer2 = SimpleNamespace(trading_name=None, legal_name="Acme Limited")
    assert ar_base.customer_display_name(customer2) == "Acme Limited"


def test_format_quantity_strips_trailing_zeroes():
    assert ar_base._format_quantity(Decimal("10.0000")) == "10"
    assert ar_base._format_quantity(Decimal("10.2500")) == "10.25"


def test_invoice_detail_view_overdue_flag(monkeypatch):
    monkeypatch.setattr(ar_base, "format_date", lambda d: d.isoformat() if d else None)
    monkeypatch.setattr(
        ar_base,
        "format_currency",
        lambda amount, currency=None: f"{amount}:{currency}",
    )

    today = date.today()
    invoice = SimpleNamespace(
        invoice_id=uuid4(),
        invoice_number="INV-001",
        invoice_type=SimpleNamespace(value="STANDARD"),
        customer_id=uuid4(),
        invoice_date=today - timedelta(days=10),
        due_date=today - timedelta(days=1),
        currency_code="USD",
        subtotal=Decimal("100"),
        tax_amount=Decimal("10"),
        total_amount=Decimal("110"),
        amount_paid=Decimal("0"),
        status=InvoiceStatus.POSTED,
        notes="n",
        internal_notes="i",
        created_at=None,
        updated_at=None,
        submitted_at=None,
        approved_at=None,
        posted_at=None,
    )

    view = ar_base.invoice_detail_view(invoice, customer=None)
    assert view["status"] == "POSTED"
    assert view["is_overdue"] is True
    assert view["balance_due"] == Decimal("110")


def test_receipt_detail_view_has_wht_flag(monkeypatch):
    monkeypatch.setattr(ar_base, "format_date", lambda d: d.isoformat() if d else None)
    monkeypatch.setattr(
        ar_base,
        "format_currency",
        lambda amount, currency=None: f"{amount}:{currency}",
    )

    payment = SimpleNamespace(
        payment_id=uuid4(),
        payment_number="RCP-1",
        customer_id=uuid4(),
        payment_date=date(2026, 2, 1),
        payment_method=SimpleNamespace(value="bank_transfer"),
        reference="REF-1",
        description="Desc",
        amount=Decimal("100"),
        gross_amount=Decimal("110"),
        wht_amount=Decimal("10"),
        wht_code_id=uuid4(),
        wht_certificate_number="WHT-1",
        status=PaymentStatus.CLEARED,
        currency_code="USD",
        bank_account_id=uuid4(),
    )

    view = ar_base.receipt_detail_view(payment, customer=None)
    assert view["status"] == "POSTED"
    assert view["has_wht"] is True


def test_allocation_view_formats_values(monkeypatch):
    monkeypatch.setattr(ar_base, "format_date", lambda d: d.isoformat() if d else None)
    monkeypatch.setattr(
        ar_base,
        "format_currency",
        lambda amount, currency=None: f"{amount}:{currency}",
    )

    allocation = SimpleNamespace(
        allocation_id=uuid4(),
        invoice_id=uuid4(),
        allocated_amount=Decimal("80"),
        discount_taken=Decimal("5"),
        write_off_amount=Decimal("0"),
        exchange_difference=Decimal("0"),
        allocation_date=date(2026, 2, 1),
    )
    invoice = SimpleNamespace(invoice_number="INV-123")

    view = ar_base.allocation_view(allocation, invoice, "USD")
    assert view["invoice_number"] == "INV-123"
    assert view["allocated_amount"] == "80:USD"
