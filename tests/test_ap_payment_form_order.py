from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from app.services.finance.ap.web.payment_web import PaymentWebService


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def all(self):
        return self._rows


def test_payment_form_open_invoices_order_newest_first(monkeypatch):
    """Open invoices on the AP payment form should show recent invoices first."""
    executed = []
    db = Mock()
    db.execute.side_effect = lambda stmt: executed.append(stmt) or _Result()
    db.scalars.return_value.all.return_value = []

    monkeypatch.setattr(
        "app.services.finance.ap.web.payment_web.supplier_service.list",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.payment_web.get_currency_context",
        lambda *args, **kwargs: {},
    )

    PaymentWebService.payment_form_context(
        db,
        "00000000-0000-0000-0000-000000000001",
    )

    order_by = [str(clause) for clause in executed[0]._order_by_clauses]
    assert order_by == [
        "ap.supplier_invoice.invoice_date DESC",
        "ap.supplier_invoice.due_date DESC",
        "ap.supplier_invoice.invoice_number DESC",
    ]


def test_payment_form_context_exposes_subtotal_for_wht_base(monkeypatch):
    """The payment form needs pre-VAT subtotal so WHT is not calculated on gross."""
    supplier_id = uuid4()
    invoice_id = uuid4()
    invoice = SimpleNamespace(
        invoice_id=invoice_id,
        invoice_number="SINV-001",
        supplier_id=supplier_id,
        invoice_date=None,
        due_date=None,
        subtotal=Decimal("1000.00"),
        tax_amount=Decimal("75.00"),
        total_amount=Decimal("1075.00"),
        amount_paid=Decimal("0.00"),
        withholding_tax_amount=Decimal("50.00"),
        withholding_tax_code_id=uuid4(),
        currency_code="NGN",
    )
    supplier = SimpleNamespace(
        supplier_id=supplier_id,
        trading_name=None,
        legal_name="Supplier",
        supplier_code="SUP-001",
        currency_code="NGN",
        payment_terms_days=30,
        withholding_tax_applicable=False,
        withholding_tax_code_id=None,
        default_tax_code_id=None,
    )

    db = Mock()
    db.execute.side_effect = [_Result([(invoice, supplier)]), _Result()]
    db.scalars.return_value.all.return_value = []

    monkeypatch.setattr(
        "app.services.finance.ap.web.payment_web.supplier_service.list",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.payment_web.get_currency_context",
        lambda *args, **kwargs: {},
    )

    context = PaymentWebService.payment_form_context(
        db,
        "00000000-0000-0000-0000-000000000001",
        invoice_id=str(invoice_id),
    )

    assert context["invoice"]["subtotal_raw"] == 1000.0
    assert context["invoice"]["tax_amount_raw"] == 75.0
    assert context["invoice"]["balance_raw"] == 1075.0
    assert context["invoice"]["withholding_tax_amount_raw"] == 50.0
    assert context["invoice"]["withholding_tax_code_id"] == str(
        invoice.withholding_tax_code_id
    )
