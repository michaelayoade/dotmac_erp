from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from tests._helpers.document_stubs import invoice_stub
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.finance.ap.supplier_invoice import (
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.finance.ap.supplier_payment import APPaymentMethod
from app.services.finance.ap.web.invoice_web import InvoiceWebService


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_create_invoice_response_commits_on_success(monkeypatch):
    request = MagicMock()
    request.headers = {"content-type": "application/json"}
    request.json = AsyncMock(return_value={"supplier_id": str(uuid4()), "lines": []})
    auth = SimpleNamespace(organization_id=uuid4(), person_id=uuid4())
    db = MagicMock()
    invoice_id = uuid4()

    class ExpiringInvoice:
        expired = False

        @property
        def invoice_id(self):
            if self.expired:
                raise AssertionError("invoice_id was read after commit")
            return invoice_id

    invoice = ExpiringInvoice()
    db.commit.side_effect = lambda: setattr(invoice, "expired", True)

    monkeypatch.setattr(
        InvoiceWebService,
        "build_invoice_input",
        staticmethod(lambda _db, _data, _org_id: object()),
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.supplier_invoice_service.create_invoice",
        lambda **_kwargs: invoice,
    )

    response = await InvoiceWebService().create_invoice_response(request, auth, db)

    assert response["success"] is True
    assert response["invoice_id"] == str(invoice_id)
    assert response["redirect_url"].startswith("/finance/ap/invoices/")
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


def test_invoice_detail_context_includes_linked_payments(monkeypatch):
    org_id = uuid4()
    supplier_id = uuid4()
    invoice_id = uuid4()
    payment_id = uuid4()

    invoice = invoice_stub(
        invoice_id=invoice_id,
        organization_id=org_id,
        supplier_id=supplier_id,
        invoice_number="SINV202603-1439",
        supplier_invoice_number=None,
        invoice_type=SupplierInvoiceType.STANDARD,
        invoice_date=date(2026, 3, 20),
        created_at=None,
        received_date=date(2026, 3, 20),
        due_date=date(2026, 4, 20),
        currency_code="NGN",
        purpose=None,
        subtotal=Decimal("1000.00"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("1000.00"),
        amount_paid=Decimal("250.00"),
        withholding_tax_amount=Decimal("0.00"),
        status=SupplierInvoiceStatus.PARTIALLY_PAID,
        comments=None,
    )
    payment = SimpleNamespace(
        payment_id=payment_id,
        payment_number="PAY-001",
        payment_date=date(2026, 3, 25),
        payment_method=APPaymentMethod.BANK_TRANSFER,
        currency_code="NGN",
    )
    allocation = SimpleNamespace(allocated_amount=Decimal("250.00"))

    db = MagicMock()
    db.execute.return_value = _Rows([(payment, allocation)])
    db.get.return_value = None

    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.supplier_invoice_service.get",
        lambda *_args, **_kwargs: invoice,
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.supplier_service.get",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.supplier_invoice_service.get_invoice_lines",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.attachment_service.list_for_entity",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.recent_activity_view",
        lambda *_args, **_kwargs: [],
    )

    context = InvoiceWebService.invoice_detail_context(
        db,
        str(org_id),
        str(invoice_id),
    )

    assert context["payments"][0]["payment_id"] == payment_id
    assert context["payments"][0]["payment_number"] == "PAY-001"
    assert context["payments"][0]["payment_method"] == "BANK_TRANSFER"
    assert "250.00" in context["payments"][0]["amount"]

    payment_stmt = db.execute.call_args.args[0]
    assert "payment_allocation.invoice_id" in str(payment_stmt)
    assert "supplier_payment.organization_id" in str(payment_stmt)


@pytest.mark.asyncio
async def test_create_invoice_response_rolls_back_on_failure(monkeypatch):
    request = MagicMock()
    request.headers = {"content-type": "application/json"}
    request.json = AsyncMock(return_value={"supplier_id": str(uuid4()), "lines": []})
    auth = SimpleNamespace(organization_id=uuid4(), person_id=uuid4())
    db = MagicMock()

    monkeypatch.setattr(
        InvoiceWebService,
        "build_invoice_input",
        staticmethod(lambda _db, _data, _org_id: object()),
    )

    def _raise(**_kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.supplier_invoice_service.create_invoice",
        _raise,
    )

    response = await InvoiceWebService().create_invoice_response(request, auth, db)

    assert response.status_code == 400
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_update_invoice_response_commits_on_success(monkeypatch):
    request = MagicMock()
    request.headers = {"content-type": "application/json"}
    request.json = AsyncMock(return_value={"supplier_id": str(uuid4()), "lines": []})
    auth = SimpleNamespace(organization_id=uuid4())
    db = MagicMock()

    monkeypatch.setattr(
        InvoiceWebService,
        "build_invoice_input",
        staticmethod(lambda _db, _data, _org_id: object()),
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.supplier_invoice_service.update_invoice",
        lambda **_kwargs: SimpleNamespace(invoice_id=uuid4()),
    )

    response = await InvoiceWebService().update_invoice_response(
        request, auth, db, str(uuid4())
    )

    payload = json.loads(response.body)
    assert payload["success"] is True
    assert payload["invoice_id"]
    assert payload["redirect_url"].startswith("/finance/ap/invoices/")
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_update_invoice_response_does_not_read_invoice_after_commit(monkeypatch):
    request = MagicMock()
    request.headers = {"content-type": "application/json"}
    request.json = AsyncMock(return_value={"supplier_id": str(uuid4()), "lines": []})
    auth = SimpleNamespace(organization_id=uuid4())
    db = MagicMock()

    class ExpiringInvoice:
        def __init__(self):
            self._invoice_id = uuid4()
            self.expired = False

        @property
        def invoice_id(self):
            if self.expired:
                raise RuntimeError("invoice was read after commit")
            return self._invoice_id

    invoice = ExpiringInvoice()
    db.commit.side_effect = lambda: setattr(invoice, "expired", True)

    monkeypatch.setattr(
        InvoiceWebService,
        "build_invoice_input",
        staticmethod(lambda _db, _data, _org_id: object()),
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.supplier_invoice_service.update_invoice",
        lambda **_kwargs: invoice,
    )

    response = await InvoiceWebService().update_invoice_response(
        request, auth, db, str(uuid4())
    )

    payload = json.loads(response.body)
    assert payload["success"] is True
    assert payload["invoice_id"] == str(invoice._invoice_id)
