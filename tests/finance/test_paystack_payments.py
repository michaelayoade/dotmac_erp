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
from app.models.finance.payments.payment_intent import (
    PaymentDirection,
    PaymentIntentStatus,
)
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

    db.scalar.return_value = expired_intent

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


# ---------------------------------------------------------------------------
# Refund events (ADR-0008). `charge.refund` and `refund.processed` used to
# match no branch in `process_webhook`: they fell into
# `logger.info("Unhandled event type: ...")` while the webhook row was marked
# PROCESSED. The cash left the bank and ERP's books never moved —
# `docs/paystack_chargebacks_investigation.md` is eight instances of it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type", ["charge.refund", "refund.processed", "refund.failed"]
)
def test_refund_events_are_dispatched_not_swallowed(event_type):
    """The dispatch table must name them. A branch that does not exist cannot
    be tested behaviourally, so the absence is asserted where it lived."""
    import inspect

    from app.services.finance.payments import webhook_service as module

    source = inspect.getsource(module.WebhookService.process_webhook)
    assert "REFUND_SETTLED_EVENTS" in source or event_type in source, (
        f"{event_type} still falls through to 'Unhandled event type'"
    )
    assert event_type in (module.REFUND_SETTLED_EVENTS | {"refund.failed"})


def test_a_refund_payload_is_identified_by_its_transaction_reference():
    """Refund payloads carry `transaction_reference`, not `reference`. Reading
    only `reference` leaves every refund event reference-less, which breaks the
    intent lookup."""
    svc = WebhookService(MagicMock())

    assert (
        svc._extract_reference("charge.refund", {"transaction_reference": "REF-9"})
        == "REF-9"
    )
    assert (
        svc._extract_reference(
            "refund.processed", {"transaction": {"reference": "REF-10"}}
        )
        == "REF-10"
    )
    # A normal charge is unaffected.
    assert svc._extract_reference("charge.success", {"reference": "REF-11"}) == "REF-11"
    # ...and an unrelated event does not get to borrow the alternate key.
    assert (
        svc._extract_reference("charge.success", {"transaction_reference": "X"}) == ""
    )


def test_two_refunds_do_not_collapse_onto_one_event_id():
    """The other half of the same fix. Without it, every refund in a deployment
    hashes to the event id `refund.processed:` and the second one is silently
    marked DUPLICATE — a refund dropped by the idempotency guard itself."""
    svc = WebhookService(MagicMock())

    first = svc._build_event_id(
        "refund.processed", {"transaction_reference": "REF-A", "amount": 1000}
    )
    second = svc._build_event_id(
        "refund.processed", {"transaction_reference": "REF-B", "amount": 2000}
    )

    assert first != second
    assert first.endswith("REF-A")


def test_refund_amount_is_read_in_major_units():
    """Paystack reports money in kobo, the same convention
    `_validate_amount_and_currency` assumes on the way in."""
    svc = WebhookService(MagicMock())

    assert svc._refund_amount({"amount": 30000}) == Decimal("300.00")
    assert svc._refund_amount({"refunded_amount": 1550}) == Decimal("15.50")
    assert svc._refund_amount({}) is None
    with pytest.raises(ValueError, match="unusable amount"):
        svc._refund_amount({"amount": "not-a-number"})


def test_a_failed_refund_changes_no_erp_state():
    """The money did not move, so there is nothing to refund. It is dispatched
    rather than ignored so the payload lands on a PROCESSED webhook row."""
    db = MagicMock()
    intent = SimpleNamespace(
        intent_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        direction=PaymentDirection.INBOUND,
        status=PaymentIntentStatus.COMPLETED,
        customer_payment_id=uuid.uuid4(),
        paystack_reference="REF-F",
        gateway_response=None,
    )

    WebhookService(db)._handle_refund_failed(intent, {"reason": "insufficient funds"})

    assert intent.status == PaymentIntentStatus.COMPLETED
    db.get.assert_not_called()


def test_only_payment_service_moves_the_intent_on_an_inbound_refund():
    """ADR-0005 does not bend for a refund. The refund owner settles the money;
    the intent — the transport's receipt — is moved by its own owner, and only
    from COMPLETED."""
    db = MagicMock()
    org_id = uuid.uuid4()
    svc = PaymentService(db, org_id)
    intent = SimpleNamespace(
        intent_id=uuid.uuid4(),
        organization_id=org_id,
        direction=PaymentDirection.INBOUND,
        status=PaymentIntentStatus.COMPLETED,
        customer_payment_id=uuid.uuid4(),
        gateway_response={"original": True},
    )

    svc.record_inbound_refund(
        intent=intent,
        refunded_at=datetime.now(UTC),
        gateway_response={"amount": 30000},
        reason="duplicate charge",
    )
    assert intent.status == PaymentIntentStatus.REVERSED
    assert intent.gateway_response["original"] is True
    assert intent.gateway_response["refund_reason"] == "duplicate charge"

    # Idempotent, and never applied to an outbound payout.
    svc.record_inbound_refund(
        intent=intent,
        refunded_at=datetime.now(UTC),
        gateway_response={},
        reason="again",
    )
    assert intent.gateway_response["refund_reason"] == "duplicate charge"

    outbound = SimpleNamespace(
        intent_id=uuid.uuid4(),
        organization_id=org_id,
        direction=PaymentDirection.OUTBOUND,
        status=PaymentIntentStatus.COMPLETED,
        customer_payment_id=None,
        gateway_response=None,
    )
    with pytest.raises(ValueError, match="not an inbound collection"):
        svc.record_inbound_refund(
            intent=outbound,
            refunded_at=datetime.now(UTC),
            gateway_response={},
        )
