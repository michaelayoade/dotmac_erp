from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.finance.ar.customer_payment import (
    CustomerPayment,
    PaymentMethod,
    PaymentStatus,
)
from app.models.finance.ar.invoice import InvoiceStatus
from app.models.finance.tax.tax_code import TaxType
from app.services.common import ValidationError
from app.services.finance.ar.customer_payment import (
    CustomerPaymentInput,
    CustomerPaymentService,
    PaymentAllocationInput,
)


def _make_customer(org_id, active=True):
    return SimpleNamespace(
        customer_id=uuid4(),
        organization_id=org_id,
        is_active=active,
        legal_name="ACME",
        ar_control_account_id=uuid4(),
        tax_identification_number="TIN",
    )


def test_create_payment_allocation_exceeds_amount():
    db = MagicMock()
    org_id = uuid4()
    customer = _make_customer(org_id)
    db.get.return_value = customer

    with pytest.raises(ValidationError, match="exceeds"):
        CustomerPaymentService.create_payment(
            db,
            org_id,
            CustomerPaymentInput(
                customer_id=customer.customer_id,
                payment_date=date.today(),
                payment_method=PaymentMethod.CARD,
                currency_code="NGN",
                amount=Decimal("50.00"),
                allocations=[
                    PaymentAllocationInput(invoice_id=uuid4(), amount=Decimal("60.00"))
                ],
            ),
            created_by_user_id=uuid4(),
        )


def test_create_payment_wht_mismatch():
    db = MagicMock()
    org_id = uuid4()
    customer = _make_customer(org_id)
    db.get.return_value = customer

    with pytest.raises(ValidationError):
        CustomerPaymentService.create_payment(
            db,
            org_id,
            CustomerPaymentInput(
                customer_id=customer.customer_id,
                payment_date=date.today(),
                payment_method=PaymentMethod.CARD,
                currency_code="NGN",
                amount=Decimal("90.00"),
                gross_amount=Decimal("100.00"),
                wht_amount=Decimal("5.00"),
            ),
            created_by_user_id=uuid4(),
        )


def test_create_payment_calculates_gross_from_wht():
    db = MagicMock()
    org_id = uuid4()
    customer = _make_customer(org_id)
    wht_code_id = uuid4()
    wht_code = SimpleNamespace(
        tax_code_id=wht_code_id,
        organization_id=org_id,
        tax_type=TaxType.WITHHOLDING,
    )

    def _get(model, _id):
        if model.__name__ == "Customer":
            return customer
        if model.__name__ == "TaxCode":
            return wht_code
        return None

    db.get.side_effect = _get

    with (
        patch(
            "app.services.finance.ar.customer_payment.SequenceService.get_next_number",
            return_value="RCPT-1",
        ),
    ):
        payment = CustomerPaymentService.create_payment(
            db,
            org_id,
            CustomerPaymentInput(
                customer_id=customer.customer_id,
                payment_date=date.today(),
                payment_method=PaymentMethod.CARD,
                currency_code="NGN",
                amount=Decimal("90.00"),
                wht_amount=Decimal("10.00"),
                wht_code_id=wht_code_id,
            ),
            created_by_user_id=uuid4(),
        )

    assert payment.gross_amount == Decimal("100.00")
    assert payment.amount == Decimal("90.00")


def test_create_wht_payment_allows_allocation_up_to_gross_settlement():
    db = MagicMock()
    org_id = uuid4()
    customer = _make_customer(org_id)
    invoice_id = uuid4()
    invoice = SimpleNamespace(
        invoice_id=invoice_id,
        organization_id=org_id,
        customer_id=customer.customer_id,
        invoice_number="INV-WHT",
        status=InvoiceStatus.POSTED,
        balance_due=Decimal("100.00"),
    )
    wht_code_id = uuid4()
    wht_code = SimpleNamespace(
        tax_code_id=wht_code_id,
        organization_id=org_id,
        tax_type=TaxType.WITHHOLDING,
    )

    def _get(model, record_id):
        if model.__name__ == "Customer":
            return customer
        if model.__name__ == "Invoice" and record_id == invoice_id:
            return invoice
        if model.__name__ == "TaxCode":
            return wht_code
        return None

    db.get.side_effect = _get
    with patch(
        "app.services.finance.ar.customer_payment.SequenceService.get_next_number",
        return_value="RCPT-WHT",
    ):
        payment = CustomerPaymentService.create_payment(
            db,
            org_id,
            CustomerPaymentInput(
                customer_id=customer.customer_id,
                payment_date=date.today(),
                payment_method=PaymentMethod.BANK_TRANSFER,
                currency_code="NGN",
                amount=Decimal("95.00"),
                gross_amount=Decimal("100.00"),
                wht_amount=Decimal("5.00"),
                wht_code_id=wht_code_id,
                allocations=[
                    PaymentAllocationInput(
                        invoice_id=invoice_id, amount=Decimal("100.00")
                    )
                ],
            ),
            created_by_user_id=uuid4(),
        )

    assert payment.gross_amount == Decimal("100.00")
    assert db.add.call_count == 2


def test_unallocated_amount_uses_gross_settlement() -> None:
    payment = SimpleNamespace(
        gross_amount=Decimal("100.00"),
        amount=Decimal("95.00"),
        allocations=[SimpleNamespace(allocated_amount=Decimal("100.00"))],
    )

    assert CustomerPayment.unallocated_amount.fget(payment) == Decimal("0.00")


def test_create_payment_rejects_non_withholding_tax_code():
    db = MagicMock()
    org_id = uuid4()
    customer = _make_customer(org_id)
    non_wht_code = SimpleNamespace(
        tax_code_id=uuid4(),
        organization_id=org_id,
        tax_type=TaxType.VAT,
    )

    def _get(model, _id):
        if model.__name__ == "Customer":
            return customer
        if model.__name__ == "TaxCode":
            return non_wht_code
        return None

    db.get.side_effect = _get

    with pytest.raises(ValidationError, match="WITHHOLDING"):
        CustomerPaymentService.create_payment(
            db,
            org_id,
            CustomerPaymentInput(
                customer_id=customer.customer_id,
                payment_date=date.today(),
                payment_method=PaymentMethod.CARD,
                currency_code="NGN",
                amount=Decimal("90.00"),
                wht_amount=Decimal("10.00"),
                wht_code_id=non_wht_code.tax_code_id,
            ),
            created_by_user_id=uuid4(),
        )


def test_post_payment_requires_bank_account():
    db = MagicMock()
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=PaymentStatus.PENDING,
        bank_account_id=None,
    )
    db.get.return_value = payment

    with pytest.raises(ValidationError, match="[Bb]ank account"):
        CustomerPaymentService.post_payment(
            db, org_id, payment.payment_id, posted_by_user_id=uuid4()
        )


def test_post_payment_wht_requires_receivable_account():
    db = MagicMock()
    db.scalar.return_value = None
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=PaymentStatus.PENDING,
        bank_account_id=uuid4(),
        customer_id=uuid4(),
        exchange_rate=Decimal("1.0"),
        amount=Decimal("90.00"),
        gross_amount=Decimal("100.00"),
        wht_amount=Decimal("10.00"),
        wht_code_id=uuid4(),
        wht_certificate_number=None,
        payment_date=date.today(),
        reference=None,
        payment_number="RCPT-1",
        currency_code="NGN",
        correlation_id="c",
    )
    customer = _make_customer(org_id)
    customer.customer_id = payment.customer_id
    mapped_bank_gl = SimpleNamespace(
        account_id=payment.bank_account_id,
        organization_id=org_id,
    )

    def _get(model, _id):
        if model.__name__ == "CustomerPayment":
            return payment
        if model.__name__ == "Account":
            return mapped_bank_gl
        if model.__name__ == "Customer":
            return customer
        if model.__name__ == "TaxCode":
            return None
        return None

    db.get.side_effect = _get

    with pytest.raises(ValidationError, match="WHT"):
        CustomerPaymentService.post_payment(
            db, org_id, payment.payment_id, posted_by_user_id=uuid4()
        )


def test_post_payment_success_without_wht():
    db = MagicMock()
    db.scalar.return_value = None
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=PaymentStatus.PENDING,
        bank_account_id=uuid4(),
        customer_id=uuid4(),
        exchange_rate=Decimal("1.0"),
        amount=Decimal("100.00"),
        gross_amount=Decimal("100.00"),
        wht_amount=Decimal("0"),
        wht_code_id=None,
        wht_certificate_number=None,
        payment_date=date.today(),
        reference=None,
        payment_number="RCPT-1",
        currency_code="NGN",
        correlation_id="c",
    )
    customer = _make_customer(org_id)
    customer.customer_id = payment.customer_id

    mapped_bank_gl = SimpleNamespace(
        account_id=payment.bank_account_id,
        organization_id=org_id,
    )

    def _get(model, _id):
        if model.__name__ == "CustomerPayment":
            return payment
        if model.__name__ == "Account":
            return mapped_bank_gl
        if model.__name__ == "Customer":
            return customer
        return None

    db.get.side_effect = _get
    db.scalars.return_value.all.return_value = []

    journal = SimpleNamespace(journal_entry_id=uuid4())
    posting_result = SimpleNamespace(
        success=True, posting_batch_id=uuid4(), message=None
    )

    with (
        patch(
            "app.services.finance.gl.journal.JournalService.create_journal",
            return_value=journal,
        ),
        patch(
            "app.services.finance.gl.journal.JournalService.submit_journal",
            return_value=None,
        ),
        patch(
            "app.services.finance.gl.journal.JournalService.approve_journal",
            return_value=None,
        ),
        patch(
            "app.services.finance.gl.ledger_posting.LedgerPostingService.post_journal_entry",
            return_value=posting_result,
        ),
    ):
        result = CustomerPaymentService.post_payment(
            db,
            org_id,
            payment.payment_id,
            posted_by_user_id=uuid4(),
        )

    assert result.status == PaymentStatus.CLEARED
    assert result.journal_entry_id == journal.journal_entry_id


def test_post_payment_rejects_unmapped_bank_account():
    db = MagicMock()
    db.scalar.return_value = None
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=PaymentStatus.PENDING,
        bank_account_id=uuid4(),
        customer_id=uuid4(),
        exchange_rate=Decimal("1.0"),
        amount=Decimal("100.00"),
        gross_amount=Decimal("100.00"),
        wht_amount=Decimal("0"),
        wht_code_id=None,
        wht_certificate_number=None,
        payment_date=date.today(),
        reference=None,
        payment_number="RCPT-2",
        currency_code="NGN",
        correlation_id="c2",
    )
    customer = _make_customer(org_id)
    customer.customer_id = payment.customer_id

    def _get(model, _id):
        if model.__name__ == "CustomerPayment":
            return payment
        if model.__name__ == "Customer":
            return customer
        if model.__name__ in {"Account", "BankAccount"}:
            return None
        return None

    db.get.side_effect = _get

    with pytest.raises(ValidationError, match="not mapped to a valid GL account"):
        CustomerPaymentService.post_payment(
            db, org_id, payment.payment_id, posted_by_user_id=uuid4()
        )


def test_void_and_bounce_reverse_allocations():
    """Void and bounce are now thin callers of `refund_payment` (ADR-0008):
    one behaviour, three reasons. The outcome they always had is unchanged —
    the allocations come back and the paid-status owner re-derives the
    invoice."""
    db = MagicMock()
    org_id = uuid4()
    actor_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=PaymentStatus.CLEARED,
        amount=Decimal("50.00"),
        gross_amount=Decimal("50.00"),
        journal_entry_id=None,
        created_by_user_id=actor_id,
    )
    invoice = SimpleNamespace(
        invoice_id=uuid4(),
        amount_paid=Decimal("50.00"),
        total_amount=Decimal("100.00"),
        status=InvoiceStatus.PAID,
        due_date=date(2099, 12, 31),
    )
    allocation = SimpleNamespace(invoice_id=uuid4(), allocated_amount=Decimal("50.00"))

    def _get(model, _id):
        if model.__name__ == "CustomerPayment":
            return payment
        if model.__name__ == "Invoice":
            return invoice
        return None

    db.get.side_effect = _get
    db.scalars.return_value.all.return_value = [allocation]

    voided = CustomerPaymentService.void_payment(
        db, org_id, payment.payment_id, voided_by_user_id=uuid4(), reason="err"
    )
    assert voided.status == PaymentStatus.VOID
    assert invoice.amount_paid == Decimal("0")
    assert invoice.status == InvoiceStatus.POSTED

    payment.status = PaymentStatus.CLEARED
    invoice.amount_paid = Decimal("50.00")
    invoice.status = InvoiceStatus.PAID
    bounced = CustomerPaymentService.mark_bounced(
        db, org_id, payment.payment_id, reason="nsf"
    )
    assert bounced.status == PaymentStatus.BOUNCED
    assert invoice.amount_paid == Decimal("0")


def test_void_is_idempotent():
    """It used to raise `ValidationError("Payment is already voided")`. A
    second void is now a no-op, because the owner is idempotent and the sync
    adapter and the webhook both re-present the same decision."""
    db = MagicMock()
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=PaymentStatus.VOID,
        amount=Decimal("50.00"),
        gross_amount=Decimal("50.00"),
        journal_entry_id=None,
        created_by_user_id=uuid4(),
    )
    db.get.side_effect = lambda model, _id: (
        payment if model.__name__ == "CustomerPayment" else None
    )

    returned = CustomerPaymentService.void_payment(
        db, org_id, payment.payment_id, voided_by_user_id=uuid4(), reason="again"
    )

    assert returned is payment
    assert payment.status == PaymentStatus.VOID
    db.scalars.assert_not_called()


def test_a_voided_payment_cannot_then_be_bounced():
    """Terminal is terminal: `refund_payment` refuses a second, different
    verdict on the same receipt rather than overwriting the first."""
    db = MagicMock()
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=PaymentStatus.VOID,
        amount=Decimal("50.00"),
        gross_amount=Decimal("50.00"),
        journal_entry_id=None,
        created_by_user_id=uuid4(),
    )
    db.get.side_effect = lambda model, _id: (
        payment if model.__name__ == "CustomerPayment" else None
    )

    with pytest.raises(ValidationError):
        CustomerPaymentService.mark_bounced(db, org_id, payment.payment_id, reason="x")

    assert payment.status == PaymentStatus.VOID
