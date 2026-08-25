"""One owner for the customer refund decision (ADR-0008).

Refund used to be a shape stamped onto five aggregates by eleven writers. The
property that arrangement could not have is the one this file asserts: the same
refund, arriving through three different doors — a person calling the service, a
dotmac_sub sync run, a Paystack webhook — must leave the ledger and the
subledger in *identical* state, because all three now ask the same owner.

Unit-level: an in-memory session stands in for the database, and the GL is a
recorder around `ReversalService.create_reversal`. Tenancy correctness is not
tested here (see `tests/` top-level RLS canaries); what is tested is who
decides, and that all three doors reach the same decision.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from app.models.finance.ar.customer_payment import PaymentStatus
from app.models.finance.ar.invoice import InvoiceStatus
from app.models.finance.payments.payment_intent import (
    PaymentDirection,
    PaymentIntentStatus,
)
from app.services.common import ValidationError
from app.services.finance.ar.customer_payment import (
    CustomerPaymentService,
    RefundReversalError,
)

ORG_ID = UUID("11111111-1111-1111-1111-111111111111")
ACTOR_ID = UUID("22222222-2222-2222-2222-222222222222")


# ---------------------------------------------------------------------------
# A settled receipt of 300.00 allocated across two invoices.
# ---------------------------------------------------------------------------


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    """Enough Session to drive the refund owner: `get`, `scalars`, `flush`."""

    def __init__(self, payment, invoices, allocations, journal_reversal=None):
        self.payment = payment
        self.invoices = {inv.invoice_id: inv for inv in invoices}
        self.allocations = allocations
        self.journal_reversal = journal_reversal
        self.added: list[object] = []
        self.flushes = 0

    def get(self, model, ident):
        name = model.__name__
        if name == "CustomerPayment":
            return self.payment if ident == self.payment.payment_id else None
        if name == "Invoice":
            return self.invoices.get(ident)
        if name == "JournalEntry":
            return SimpleNamespace(reversal_journal_id=self.journal_reversal)
        return None

    def scalars(self, _stmt):
        return _Scalars(self.allocations)

    def scalar(self, _stmt):
        # The cash-basis VAT reclass lookup: this receipt has none.
        return None

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        self.flushes += 1


def _scenario(journal_entry_id=None):
    """A CLEARED receipt of 300.00 spread over a PAID and a PARTIALLY_PAID
    invoice — the shape a refund has to unwind on both the ledger and the
    subledger."""
    due = date.today() + timedelta(days=30)
    invoice_a = SimpleNamespace(
        invoice_id=uuid4(),
        total_amount=Decimal("200.00"),
        amount_paid=Decimal("200.00"),
        status=InvoiceStatus.PAID,
        due_date=due,
    )
    invoice_b = SimpleNamespace(
        invoice_id=uuid4(),
        total_amount=Decimal("500.00"),
        amount_paid=Decimal("100.00"),
        status=InvoiceStatus.PARTIALLY_PAID,
        due_date=due,
    )
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=ORG_ID,
        status=PaymentStatus.CLEARED,
        amount=Decimal("300.00"),
        gross_amount=Decimal("300.00"),
        journal_entry_id=journal_entry_id if journal_entry_id else uuid4(),
        posting_batch_id=uuid4(),
        created_by_user_id=ACTOR_ID,
        dotmac_sub_id="sub-pay-1",
    )
    allocations = [
        SimpleNamespace(
            invoice_id=invoice_a.invoice_id, allocated_amount=Decimal("200.00")
        ),
        SimpleNamespace(
            invoice_id=invoice_b.invoice_id, allocated_amount=Decimal("100.00")
        ),
    ]
    db = _FakeSession(payment, [invoice_a, invoice_b], allocations)
    return db, payment, invoice_a, invoice_b


@pytest.fixture
def gl(monkeypatch):
    """Record every reversal the owner asks the GL mechanism for."""
    from app.services.finance.gl import reversal as reversal_module

    calls: list[dict] = []

    def _create_reversal(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            success=True,
            reversal_journal_id=uuid4(),
            reversal_journal_number="REV-1",
            message="",
        )

    monkeypatch.setattr(
        reversal_module.ReversalService,
        "create_reversal",
        staticmethod(_create_reversal),
    )
    return calls


@pytest.fixture(autouse=True)
def _no_tax_side_effects(monkeypatch):
    from app.services.finance.tax import tax_transaction

    monkeypatch.setattr(
        tax_transaction.tax_transaction_service,
        "delete_cash_recognition_for_source",
        lambda *a, **k: 0,
    )


def _observable(db, payment, invoice_a, invoice_b, gl_calls):
    """The state three different doors must agree on."""
    return {
        "payment_status": payment.status,
        "invoice_a": (invoice_a.amount_paid, invoice_a.status),
        "invoice_b": (invoice_b.amount_paid, invoice_b.status),
        "reversals": [
            (call["reason"], call["idempotency_key"], call["organization_id"])
            for call in gl_calls
        ],
    }


# ---------------------------------------------------------------------------
# Door 1: the owner, called directly.
# ---------------------------------------------------------------------------


def test_refund_reverses_the_ledger_once_and_restores_both_invoices(gl):
    db, payment, invoice_a, invoice_b = _scenario()

    CustomerPaymentService.refund_payment(
        db,
        ORG_ID,
        payment.payment_id,
        reason="customer refunded",
        refunded_by_user_id=ACTOR_ID,
    )

    # Exactly ONE reversal journal — the receipt has one, and the refund
    # reverses it once. (The VAT reclass path finds nothing on this receipt.)
    assert len(gl) == 1
    assert gl[0]["reason"] == "customer refunded"
    assert gl[0]["idempotency_key"].endswith(":refund-reversal:v1")
    assert gl[0]["auto_post"] is True
    assert gl[0]["created_by_user_id"] == ACTOR_ID

    # Subledger: the balance goes back to both invoices...
    assert invoice_a.amount_paid == Decimal("0.00")
    assert invoice_b.amount_paid == Decimal("0.00")
    # ...and the verdict is re-derived by the paid-status owner, not by the
    # refund owner: a fully-paid invoice with nothing paid and a future due
    # date is POSTED, which is a rule this file does not restate.
    assert invoice_a.status == InvoiceStatus.POSTED
    assert invoice_b.status == InvoiceStatus.POSTED

    assert payment.status == PaymentStatus.REVERSED


def test_a_second_refund_is_a_no_op(gl):
    db, payment, invoice_a, invoice_b = _scenario()

    CustomerPaymentService.refund_payment(
        db, ORG_ID, payment.payment_id, reason="first", refunded_by_user_id=ACTOR_ID
    )
    after_first = _observable(db, payment, invoice_a, invoice_b, gl)

    returned = CustomerPaymentService.refund_payment(
        db, ORG_ID, payment.payment_id, reason="again", refunded_by_user_id=ACTOR_ID
    )

    assert returned is payment
    # No second ledger row, no second credit to the invoices.
    assert len(gl) == 1
    assert _observable(db, payment, invoice_a, invoice_b, gl) == after_first


def test_a_failed_gl_reversal_changes_nothing(monkeypatch):
    """ADR-0008 §3. `void_payment` used to log the failure and complete the
    void anyway, leaving the invoices credited and the ledger holding cash."""
    from app.services.finance.gl import reversal as reversal_module

    monkeypatch.setattr(
        reversal_module.ReversalService,
        "create_reversal",
        staticmethod(
            lambda **_kw: SimpleNamespace(
                success=False, reversal_journal_id=None, message="period closed"
            )
        ),
    )
    db, payment, invoice_a, invoice_b = _scenario()

    with pytest.raises(RefundReversalError, match="period closed"):
        CustomerPaymentService.refund_payment(
            db, ORG_ID, payment.payment_id, reason="x", refunded_by_user_id=ACTOR_ID
        )

    assert payment.status == PaymentStatus.CLEARED
    assert invoice_a.amount_paid == Decimal("200.00")
    assert invoice_a.status == InvoiceStatus.PAID
    assert invoice_b.amount_paid == Decimal("100.00")


def test_a_partial_refund_is_refused_and_names_the_adr(gl):
    """No Refund aggregate exists yet, so a partial refund cannot be
    represented. Reversing the whole receipt instead would misstate cash."""
    db, payment, invoice_a, invoice_b = _scenario()

    with pytest.raises(ValidationError, match="ADR-0008"):
        CustomerPaymentService.refund_payment(
            db,
            ORG_ID,
            payment.payment_id,
            amount=Decimal("50.00"),
            reason="partial",
            refunded_by_user_id=ACTOR_ID,
        )

    assert gl == []
    assert payment.status == PaymentStatus.CLEARED
    assert invoice_a.amount_paid == Decimal("200.00")


def test_void_and_bounce_are_callers_not_implementations(gl):
    """One behaviour, three reasons — and the two pre-ADR-0008 idempotency keys
    survive byte for byte, so no already-reversed production journal changes
    identity."""
    db, payment, _a, _b = _scenario()
    CustomerPaymentService.void_payment(
        db, ORG_ID, payment.payment_id, voided_by_user_id=ACTOR_ID, reason="keyed twice"
    )
    assert payment.status == PaymentStatus.VOID
    assert gl[0]["reason"] == "Payment voided: keyed twice"
    assert gl[0]["idempotency_key"] == (
        f"{ORG_ID}:AR:PAY:{payment.payment_id}:void-reversal:v1"
    )

    db2, payment2, _a2, _b2 = _scenario()
    CustomerPaymentService.mark_bounced(db2, ORG_ID, payment2.payment_id, reason="nsf")
    assert payment2.status == PaymentStatus.BOUNCED
    assert gl[1]["reason"] == "Payment bounced: nsf"
    assert gl[1]["idempotency_key"] == (
        f"{ORG_ID}:AR:PAY:{payment2.payment_id}:bounce-reversal:v1"
    )
    # The bounce actor falls back to the payment's creator, as it always did.
    assert gl[1]["created_by_user_id"] == ACTOR_ID


def test_bounce_still_refuses_a_status_it_never_accepted(gl):
    db, payment, _a, _b = _scenario()
    payment.status = PaymentStatus.APPROVED

    with pytest.raises(ValidationError, match="APPROVED"):
        CustomerPaymentService.mark_bounced(db, ORG_ID, payment.payment_id, reason="x")

    assert gl == []


# ---------------------------------------------------------------------------
# Doors 2 and 3: the sync adapter and the gateway webhook must reach the SAME
# state as door 1. This is the property eleven writers could not have.
# ---------------------------------------------------------------------------


def _refund_through_the_owner(db, payment, gl):
    CustomerPaymentService.refund_payment(
        db,
        ORG_ID,
        payment.payment_id,
        reason="dotmac_sub payment refunded (sub-pay-1)",
        refunded_by_user_id=ACTOR_ID,
    )


def _refund_through_the_sync(db, payment, gl):
    from app.services.dotmac_sub.sync._payments import PaymentSyncMixin
    from app.services.dotmac_sub.sync._types import SyncResult

    class _Harness(PaymentSyncMixin):
        def __init__(self, session):
            self.db = session
            self.organization_id = ORG_ID
            self.recorded: list[tuple] = []

        def _find_local_payment(self, _external_id):
            return payment

        def _compute_hash(self, _data):
            return "hash"

        def _record_sync(self, *args):
            self.recorded.append(args)

    harness = _Harness(db)
    result = SyncResult(success=True, entity_type="payment")
    harness._handle_unsettled_payment(
        SimpleNamespace(status="refunded", amount=Decimal("300.00")),
        "sub-pay-1",
        result,
        ACTOR_ID,
    )
    assert result.errors == []
    assert result.updated == 1
    assert harness.recorded, "the adapter must still record what it observed"


def _refund_through_the_webhook(db, payment, gl):
    from app.services.finance.payments.webhook_service import WebhookService

    intent = SimpleNamespace(
        intent_id=uuid4(),
        organization_id=ORG_ID,
        direction=PaymentDirection.INBOUND,
        status=PaymentIntentStatus.COMPLETED,
        customer_payment_id=payment.payment_id,
        paystack_reference="REF-REFUND",
        gateway_response=None,
    )
    WebhookService(db)._handle_refund_settled(
        intent,
        "charge.refund",
        {
            "transaction_reference": "REF-REFUND",
            "amount": 30000,  # kobo
            "currency": "NGN",
            "merchant_note": "payment refunded (sub-pay-1)",
            "refunded_at": datetime.now(UTC).isoformat(),
        },
    )
    # ADR-0005 does not bend: the intent is moved by its own owner, and only
    # after the refund owner has settled the money.
    assert intent.status == PaymentIntentStatus.REVERSED


@pytest.mark.parametrize(
    "door",
    [_refund_through_the_owner, _refund_through_the_sync, _refund_through_the_webhook],
    ids=["direct", "dotmac_sub_sync", "paystack_webhook"],
)
def test_every_door_reaches_the_same_ledger_and_subledger(door, gl):
    db, payment, invoice_a, invoice_b = _scenario()

    door(db, payment, gl)

    assert payment.status == PaymentStatus.REVERSED
    assert invoice_a.amount_paid == Decimal("0.00")
    assert invoice_a.status == InvoiceStatus.POSTED
    assert invoice_b.amount_paid == Decimal("0.00")
    assert invoice_b.status == InvoiceStatus.POSTED

    assert len(gl) == 1
    assert gl[0]["organization_id"] == ORG_ID
    assert gl[0]["idempotency_key"].endswith(":refund-reversal:v1")
    assert gl[0]["auto_post"] is True


def test_the_sync_leaves_everything_alone_when_the_refund_is_refused(monkeypatch):
    """The adapter reports and returns; it does not half-apply. This is the
    behaviour the sync had already arrived at alone, now enforced for every
    trigger by the owner."""
    from app.services.dotmac_sub.sync._payments import PaymentSyncMixin
    from app.services.dotmac_sub.sync._types import SyncResult
    from app.services.finance.gl import reversal as reversal_module

    monkeypatch.setattr(
        reversal_module.ReversalService,
        "create_reversal",
        staticmethod(
            lambda **_kw: SimpleNamespace(
                success=False, reversal_journal_id=None, message="period closed"
            )
        ),
    )
    db, payment, invoice_a, invoice_b = _scenario()

    class _Harness(PaymentSyncMixin):
        def __init__(self, session):
            self.db = session
            self.organization_id = ORG_ID
            self.recorded: list[tuple] = []

        def _find_local_payment(self, _external_id):
            return payment

        def _compute_hash(self, _data):
            return "hash"

        def _record_sync(self, *args):
            self.recorded.append(args)

    harness = _Harness(db)
    result = SyncResult(success=True, entity_type="payment")
    harness._handle_unsettled_payment(
        SimpleNamespace(status="refunded", amount=Decimal("300.00")),
        "sub-pay-1",
        result,
        ACTOR_ID,
    )

    assert result.updated == 0
    assert len(result.errors) == 1
    assert "left unchanged" in result.errors[0]
    assert harness.recorded == []
    assert payment.status == PaymentStatus.CLEARED
    assert invoice_a.amount_paid == Decimal("200.00")


def test_the_webhook_refuses_a_refund_with_no_receipt_to_refund(gl):
    """Before ADR-0008 this event matched no branch at all and was logged as
    "Unhandled event type" while the webhook row was marked PROCESSED."""
    from app.services.finance.payments.webhook_service import WebhookService

    db, payment, _a, _b = _scenario()
    intent = SimpleNamespace(
        intent_id=uuid4(),
        organization_id=ORG_ID,
        direction=PaymentDirection.INBOUND,
        status=PaymentIntentStatus.COMPLETED,
        customer_payment_id=None,
        paystack_reference="REF-ORPHAN",
        gateway_response=None,
    )

    with pytest.raises(ValueError, match="no settled customer payment"):
        WebhookService(db)._handle_refund_settled(
            intent, "charge.refund", {"amount": 30000}
        )

    assert gl == []
    assert payment.status == PaymentStatus.CLEARED


def test_an_outbound_intent_is_not_a_customer_refund(gl):
    """The boundary, asserted: company money out is PaymentService's, through
    `transfer.reversed`, not the customer refund owner's."""
    from app.services.finance.payments.webhook_service import WebhookService

    db, payment, _a, _b = _scenario()
    intent = SimpleNamespace(
        intent_id=uuid4(),
        organization_id=ORG_ID,
        direction=PaymentDirection.OUTBOUND,
        status=PaymentIntentStatus.COMPLETED,
        customer_payment_id=payment.payment_id,
        paystack_reference="REF-OUT",
        gateway_response=None,
    )

    with pytest.raises(ValueError, match="OUTBOUND"):
        WebhookService(db)._handle_refund_settled(
            intent, "charge.refund", {"amount": 30000}
        )

    assert gl == []


# ---------------------------------------------------------------------------
# The credit-note void: writer #11, moved behind the lifecycle owner.
# ---------------------------------------------------------------------------


class _InvoiceSession:
    def __init__(self, invoice):
        self.invoice = invoice
        self.added: list[object] = []
        self.flushes = 0

    def get(self, model, ident):
        if model.__name__ == "Invoice" and ident == self.invoice.invoice_id:
            return self.invoice
        return None

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        self.flushes += 1


def _posted_credit_note():
    return SimpleNamespace(
        invoice_id=uuid4(),
        organization_id=ORG_ID,
        status=InvoiceStatus.POSTED,
        voided_by_user_id=None,
        voided_at=None,
        void_reason=None,
    )


def test_an_upstream_void_reaches_the_lifecycle_owner():
    """The sync adapter used to stamp `InvoiceStatus.VOID` itself, so nothing
    recorded who voided the credit note, when, or on whose say-so."""
    from app.services.finance.ar.invoice import ARInvoiceService

    credit_note = _posted_credit_note()
    db = _InvoiceSession(credit_note)

    ARInvoiceService.void_from_external_source(
        db,
        ORG_ID,
        credit_note.invoice_id,
        reason="credit note CN-9 voided at source",
        voided_by_user_id=ACTOR_ID,
        source="dotmac_sub",
    )

    assert credit_note.status == InvoiceStatus.VOID
    assert credit_note.voided_by_user_id == ACTOR_ID
    assert credit_note.voided_at is not None
    # The originating system is on the record, not just in a log line.
    assert credit_note.void_reason.startswith("[dotmac_sub]")


def test_erps_own_void_still_refuses_a_posted_document():
    """The two premises stay separate. `void_invoice` is a person's decision
    and must keep refusing a document that is already in the ledger — the
    external entry point exists precisely because that refusal is correct."""
    from app.services.finance.ar.invoice import ARInvoiceService

    credit_note = _posted_credit_note()
    db = _InvoiceSession(credit_note)

    with pytest.raises(ValidationError, match="POSTED"):
        ARInvoiceService.void_invoice(
            db, ORG_ID, credit_note.invoice_id, ACTOR_ID, "by hand"
        )

    assert credit_note.status == InvoiceStatus.POSTED


def test_an_upstream_void_is_idempotent():
    from app.services.finance.ar.invoice import ARInvoiceService

    credit_note = _posted_credit_note()
    credit_note.status = InvoiceStatus.VOID
    credit_note.void_reason = "[dotmac_sub] first"
    db = _InvoiceSession(credit_note)

    ARInvoiceService.void_from_external_source(
        db,
        ORG_ID,
        credit_note.invoice_id,
        reason="second",
        voided_by_user_id=ACTOR_ID,
        source="dotmac_sub",
    )

    assert credit_note.void_reason == "[dotmac_sub] first"
    assert db.flushes == 0
