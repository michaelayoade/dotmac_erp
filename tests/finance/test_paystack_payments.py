from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.finance.ar.invoice import InvoiceStatus
from app.models.finance.payments.payment_intent import PaymentIntentStatus
from app.services.finance.payments.payment_service import PaymentService
from app.services.finance.payments.paystack_client import PaystackConfig
from app.services.finance.payments.webhook_service import WebhookService


def test_verify_payment_rejects_amount_mismatch():
    db = MagicMock()
    org_id = uuid.uuid4()
    svc = PaymentService(db, org_id)

    intent = SimpleNamespace(
        intent_id=uuid.uuid4(),
        organization_id=org_id,
        paystack_reference="REF-1",
        amount=Decimal("100.00"),
        currency_code="NGN",
        status=PaymentIntentStatus.PENDING,
        customer_payment_id=None,
        gateway_response=None,
    )

    result = SimpleNamespace(
        status="success",
        reference="REF-1",
        amount=5000,  # 50.00 NGN, mismatch
        currency="NGN",
        transaction_id="trx_1",
        paid_at=None,
        channel="card",
        gateway_response="Approved",
    )

    client_cm = MagicMock()
    client_cm.__enter__.return_value.verify_transaction.return_value = result
    client_cm.__exit__.return_value = False

    with (
        patch.object(PaymentService, "get_intent_by_reference", return_value=intent),
        patch(
            "app.services.finance.payments.payment_service.PaystackClient",
            return_value=client_cm,
        ),
        pytest.raises(HTTPException) as excinfo,
    ):
        svc.verify_payment_by_reference("REF-1", PaystackConfig("sk", "pk", "wh"))

    assert excinfo.value.status_code == 400
    assert intent.status == PaymentIntentStatus.FAILED


def test_webhook_rejects_invalid_amount_payload():
    svc = WebhookService(MagicMock())
    intent = SimpleNamespace(
        intent_id=uuid.uuid4(),
        paystack_reference="REF-2",
        amount=Decimal("10.00"),
        currency_code="NGN",
    )

    with pytest.raises(ValueError):
        svc._validate_amount_and_currency(
            intent=intent,
            data={"amount": "bad", "currency": "NGN"},
            event_type="charge.success",
        )


def test_expired_invoice_intent_allows_new_payment():
    db = MagicMock()
    org_id = uuid.uuid4()
    svc = PaymentService(db, org_id)

    invoice_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    invoice = SimpleNamespace(
        invoice_id=invoice_id,
        organization_id=org_id,
        status=InvoiceStatus.POSTED,
        balance_due=Decimal("100.00"),
        invoice_number="INV-100",
        currency_code="NGN",
        customer_id=customer_id,
    )
    customer = SimpleNamespace(
        customer_id=customer_id,
        primary_contact={"email": "payer@example.com"},
        legal_name=None,
        trading_name="ACME",
    )

    expired_intent = SimpleNamespace(
        intent_id=uuid.uuid4(),
        status=PaymentIntentStatus.PENDING,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = expired_intent
    db.query.return_value = query

    def _get(model, _id):
        if model.__name__ == "Invoice":
            return invoice
        if model.__name__ == "Customer":
            return customer
        return None

    db.get.side_effect = _get

    init_result = SimpleNamespace(
        access_code="access",
        authorization_url="https://paystack/redirect",
        reference="REF-3",
    )
    client_cm = MagicMock()
    client_cm.__enter__.return_value.initialize_transaction.return_value = init_result
    client_cm.__exit__.return_value = False

    with (
        patch(
            "app.services.finance.payments.payment_service.resolve_value",
            return_value=None,
        ),
        patch(
            "app.services.finance.payments.payment_service.PaystackClient",
            return_value=client_cm,
        ),
    ):
        intent = svc.create_invoice_payment_intent(
            invoice_id=invoice_id,
            callback_url="https://example.com/callback",
            paystack_config=PaystackConfig("sk", "pk", "wh"),
        )

    assert expired_intent.status == PaymentIntentStatus.EXPIRED
    assert intent.authorization_url == "https://paystack/redirect"
