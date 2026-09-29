from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.finance.ar.invoice import InvoiceStatus
from app.models.finance.payments.payment_intent import PaymentIntentStatus
from app.services.finance.payments.payment_service import PaymentService
from app.services.finance.payments.paystack_client import (
    PaystackConfig,
    PaystackError,
    PaystackUnreachable,
)
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


@pytest.mark.parametrize(
    "status", [PaymentIntentStatus.PENDING, PaymentIntentStatus.PROCESSING]
)
@pytest.mark.parametrize("authorization_url", [None, "https://paystack/redirect"])
def test_expired_unresolved_invoice_intent_blocks_new_payment(
    status, authorization_url
):
    db = MagicMock()
    org_id = uuid.uuid4()
    svc = PaymentService(db, org_id)

    invoice_id = uuid.uuid4()
    invoice = SimpleNamespace(
        invoice_id=invoice_id,
        organization_id=org_id,
        status=InvoiceStatus.POSTED,
        balance_due=Decimal("100.00"),
        invoice_number="INV-100",
        currency_code="NGN",
        customer_id=uuid.uuid4(),
    )

    expired_intent = SimpleNamespace(
        intent_id=uuid.uuid4(),
        status=status,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        authorization_url=authorization_url,
    )

    db.scalar.side_effect = [invoice, expired_intent]
    client_cm = MagicMock()
    with (
        patch(
            "app.services.finance.payments.payment_service.PaystackClient",
            return_value=client_cm,
        ),
        pytest.raises(HTTPException) as excinfo,
    ):
        svc.create_invoice_payment_intent(
            invoice_id=invoice_id,
            callback_url="https://example.com/callback",
            paystack_config=PaystackConfig("sk", "pk", "wh"),
        )

    assert excinfo.value.status_code == 409
    assert expired_intent.status == status
    db.add.assert_not_called()
    db.commit.assert_not_called()
    client_cm.assert_not_called()


def _invoice_initialization(
    db: MagicMock, org_id: uuid.UUID
) -> tuple[PaymentService, uuid.UUID, SimpleNamespace]:
    invoice_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    invoice = SimpleNamespace(
        organization_id=org_id,
        status=InvoiceStatus.POSTED,
        balance_due=Decimal("100.00"),
        invoice_number="INV-200",
        currency_code="NGN",
        customer_id=customer_id,
    )
    customer = SimpleNamespace(
        customer_id=customer_id,
        primary_contact={"email": "payer@example.com"},
        legal_name="ACME",
        trading_name=None,
    )
    db.get.return_value = customer
    db.scalar.side_effect = [invoice, None]
    return PaymentService(db, org_id), invoice_id, invoice


def _initialize(svc: PaymentService, invoice_id: uuid.UUID):
    return svc.create_invoice_payment_intent(
        invoice_id=invoice_id,
        callback_url="https://example.com/callback",
        paystack_config=PaystackConfig("sk", "pk", "wh"),
    )


def test_invoice_intent_is_committed_before_paystack_initialization():
    db = MagicMock()
    svc, invoice_id, _ = _invoice_initialization(db, uuid.uuid4())
    observations = []
    committed_intents = []
    transaction = {"open": True}

    def commit():
        if not committed_intents:
            committed_intent = db.add.call_args.args[0]
            committed_intents.append(
                (
                    committed_intent.status,
                    committed_intent.paystack_reference,
                    committed_intent.authorization_url,
                )
            )
        transaction["open"] = False

    db.commit.side_effect = commit
    db.in_transaction.side_effect = lambda: transaction["open"]

    def initialize(**kwargs):
        assert db.in_transaction() is False
        db.refresh.assert_not_called()
        scalar_calls = db.scalar.call_args_list
        assert "FOR UPDATE" in str(scalar_calls[0].args[0])
        assert "organization_id" in str(scalar_calls[0].args[0])
        assert "FOR UPDATE" not in str(scalar_calls[1].args[0])
        calls_before_provider = [name for name, _, _ in db.mock_calls]
        assert (
            calls_before_provider[: calls_before_provider.index("commit")].count(
                "scalar"
            )
            == 2
        )
        assert calls_before_provider.index("add") < calls_before_provider.index(
            "commit"
        )
        observations.append(
            (
                db.commit.call_count,
                kwargs["reference"],
                kwargs["currency"],
            )
        )
        return SimpleNamespace(
            access_code="access", authorization_url="https://paystack/redirect"
        )

    client_cm = MagicMock()
    client_cm.__enter__.return_value.initialize_transaction.side_effect = initialize
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
        intent = _initialize(svc, invoice_id)

    assert committed_intents == [
        (PaymentIntentStatus.PENDING, intent.paystack_reference, None)
    ]
    assert observations == [(1, intent.paystack_reference, "NGN")]
    assert db.commit.call_count == 2
    db.refresh.assert_called_once_with(intent)
    assert intent.authorization_url == "https://paystack/redirect"
    assert intent.paystack_access_code == "access"


def test_invoice_intent_commit_failure_prevents_provider_call():
    db = MagicMock()
    svc, invoice_id, _ = _invoice_initialization(db, uuid.uuid4())
    db.commit.side_effect = RuntimeError("intent commit failed")
    client_cm = MagicMock()
    with (
        patch(
            "app.services.finance.payments.payment_service.resolve_value",
            return_value=None,
        ),
        patch(
            "app.services.finance.payments.payment_service.PaystackClient",
            return_value=client_cm,
        ),
        pytest.raises(RuntimeError, match="intent commit failed"),
    ):
        _initialize(svc, invoice_id)

    db.commit.assert_called_once()
    client_cm.assert_not_called()


def test_invoice_settlement_failure_leaves_committed_pending_intent():
    db = MagicMock()
    svc, invoice_id, _ = _invoice_initialization(db, uuid.uuid4())
    committed = []

    def commit():
        intent = db.add.call_args.args[0]
        if not committed:
            committed.append(
                (intent.status, intent.paystack_reference, intent.authorization_url)
            )
        else:
            raise RuntimeError("settlement commit failed")

    db.commit.side_effect = commit
    client_cm = MagicMock()
    client_cm.__enter__.return_value.initialize_transaction.return_value = (
        SimpleNamespace(
            access_code="access", authorization_url="https://paystack/redirect"
        )
    )
    with (
        patch(
            "app.services.finance.payments.payment_service.resolve_value",
            return_value=None,
        ),
        patch(
            "app.services.finance.payments.payment_service.PaystackClient",
            return_value=client_cm,
        ),
        pytest.raises(RuntimeError, match="settlement commit failed"),
    ):
        _initialize(svc, invoice_id)

    assert committed[0][0] == PaymentIntentStatus.PENDING
    assert committed[0][1]
    assert committed[0][2] is None
    assert db.commit.call_count == 2
    client_cm.__enter__.return_value.initialize_transaction.assert_called_once()


@pytest.mark.parametrize(
    "provider_error",
    [PaystackUnreachable("read timeout"), PaystackError("duplicate reference")],
)
def test_ambiguous_invoice_initialization_keeps_reference_and_blocks_retry(
    provider_error,
):
    db = MagicMock()
    svc, invoice_id, invoice = _invoice_initialization(db, uuid.uuid4())
    client_cm = MagicMock()
    client_cm.__enter__.return_value.initialize_transaction.side_effect = provider_error
    with (
        patch(
            "app.services.finance.payments.payment_service.resolve_value",
            return_value=None,
        ),
        patch(
            "app.services.finance.payments.payment_service.PaystackClient",
            return_value=client_cm,
        ),
        pytest.raises(type(provider_error)),
    ):
        _initialize(svc, invoice_id)

    intent = db.add.call_args.args[0]
    assert db.commit.call_count == 1
    assert intent.status == PaymentIntentStatus.PENDING
    assert intent.paystack_reference
    assert intent.authorization_url is None
    intent.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.scalar.side_effect = [invoice, intent]
    with pytest.raises(HTTPException) as excinfo:
        _initialize(svc, invoice_id)
    assert excinfo.value.status_code == 409
    assert intent.status == PaymentIntentStatus.PENDING
    client_cm.__enter__.return_value.initialize_transaction.assert_called_once()
