from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.models.finance.ap.supplier_invoice import (
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.services.finance.ap.web.invoice_web import InvoiceWebService


class _ScalarResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def all(self):
        return self._rows


def test_invoice_edit_context_includes_invoice_type(monkeypatch):
    """Draft invoice edit form needs invoice_type for Alpine initialization."""
    org_id = uuid4()
    invoice_id = uuid4()
    invoice = SimpleNamespace(
        invoice_id=invoice_id,
        invoice_number="AP-001",
        invoice_type=SupplierInvoiceType.STANDARD,
        organization_id=org_id,
        supplier_id=uuid4(),
        status=SupplierInvoiceStatus.DRAFT,
        invoice_date=date(2026, 6, 19),
        received_date=date(2026, 6, 19),
        due_date=date(2026, 6, 26),
        vehicle_id=None,
        currency_code="NGN",
        purpose="",
        exchange_rate=Decimal("1"),
        auto_create_inventory_receipt=False,
        inventory_receipt_mode=None,
    )
    db = SimpleNamespace(
        get=lambda model, pk: invoice,
        scalars=lambda stmt: _ScalarResult(),
    )
    auth = SimpleNamespace(organization_id=org_id)
    captured = {}

    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.base_context",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        InvoiceWebService,
        "invoice_form_context",
        lambda *args, **kwargs: {},
    )

    def fake_template_response(request, template_name, context):
        captured["template_name"] = template_name
        captured["context"] = context
        return context

    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.templates.TemplateResponse",
        fake_template_response,
    )

    response = InvoiceWebService().invoice_edit_form_response(
        request=SimpleNamespace(),
        auth=auth,
        db=db,
        invoice_id=str(invoice_id),
    )

    assert response is captured["context"]
    assert captured["template_name"] == "finance/ap/invoice_form.html"
    assert (
        captured["context"]["invoice"]["invoice_type"] == SupplierInvoiceType.STANDARD
    )
