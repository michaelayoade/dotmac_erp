"""
AR Payment Posting - Post customer payments to GL.

Transforms customer payments into journal entries with:
- Debit: Bank/Cash account
- Credit: AR Control account (reduce receivable)
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.models.finance.gl.journal_entry import JournalType
from app.services.common import coerce_uuid
from app.services.finance.ar.posting.helpers import build_cash_vat_reclass_entries
from app.services.finance.ar.posting.result import ARPostingResult
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
)
from app.services.finance.posting.accounts import (
    resolve_bank_gl_account_id as _resolve_bank_gl_account_id,
)
from app.services.finance.posting.base import BasePostingAdapter
from app.services.finance.posting.idempotency import PostingIdempotencyService
from app.services.finance.posting.tax import create_payment_tax_recognitions
from app.services.finance.tax.tax_transaction import tax_transaction_service


def post_vat_reclass_for_payment(
    db: Session,
    *,
    organization_id: UUID,
    payment,
    customer: Customer,
    posting_date: date,
    posted_by_user_id: UUID,
) -> ARPostingResult | None:
    """Post deferred-VAT reclass and cash-basis tax rows for an AR payment."""
    org_id = coerce_uuid(organization_id)
    pay_id = payment.payment_id
    user_id = coerce_uuid(posted_by_user_id)
    exchange_rate = payment.exchange_rate or Decimal("1.0")

    allocations = list(
        db.scalars(
            select(PaymentAllocation).where(PaymentAllocation.payment_id == pay_id)
        ).all()
    )
    reclass_entries, tax_payloads = build_cash_vat_reclass_entries(
        db, org_id, allocations
    )
    if not reclass_entries:
        return None

    if PostingIdempotencyService.source_journal_exists(
        db,
        source_module="AR",
        source_document_type="CUSTOMER_PAYMENT_VAT_RECLASS",
        source_document_id=pay_id,
    ):
        return None

    grouped: dict[tuple[UUID, UUID], Decimal] = {}
    for row in reclass_entries:
        key = (
            row["deferred_account_id"],
            row["current_account_id"],
        )
        grouped[key] = grouped.get(key, Decimal("0")) + row["tax_amount"]

    reclass_lines: list[JournalLineInput] = []
    for (deferred_account_id, current_account_id), tax_amount in grouped.items():
        functional_tax = tax_amount * exchange_rate
        reclass_lines.append(
            JournalLineInput(
                account_id=deferred_account_id,
                debit_amount=tax_amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=functional_tax,
                credit_amount_functional=Decimal("0"),
                description=f"Deferred VAT recognized on receipt {payment.payment_number}",
            )
        )
        reclass_lines.append(
            JournalLineInput(
                account_id=current_account_id,
                debit_amount=Decimal("0"),
                credit_amount=tax_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=functional_tax,
                description=f"VAT payable recognized on receipt {payment.payment_number}",
            )
        )

    reclass_input = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=payment.payment_date,
        posting_date=posting_date,
        description=f"AR VAT reclass {payment.payment_number} - {customer.legal_name}",
        reference=payment.reference or payment.payment_number,
        currency_code=payment.currency_code,
        exchange_rate=exchange_rate,
        lines=reclass_lines,
        source_module="AR",
        source_document_type="CUSTOMER_PAYMENT_VAT_RECLASS",
        source_document_id=pay_id,
        correlation_id=payment.correlation_id,
    )
    reclass_journal, reclass_result = BasePostingAdapter.create_approve_and_post_journal(
        db,
        org_id,
        reclass_input,
        user_id,
        posting_date=posting_date,
        idempotency_key=BasePostingAdapter.make_idempotency_key(
            org_id, "AR:PAY:VAT", pay_id, action="post"
        ),
        source_module="AR",
        correlation_id=payment.correlation_id,
        success_message="VAT reclass posted successfully",
        creation_error_prefix="VAT reclass journal creation failed",
    )
    if not reclass_result.success:
        return ARPostingResult(
            success=False,
            journal_entry_id=reclass_journal.journal_entry_id
            if reclass_journal
            else None,
            message=reclass_result.message,
        )

    create_payment_tax_recognitions(
        db,
        org_id,
        payment=payment,
        tax_payloads=tax_payloads,
        source_document_type="CUSTOMER_PAYMENT",
        is_purchase=False,
        exchange_rate=exchange_rate,
        counterparty_name=customer.legal_name,
        counterparty_tax_id=customer.tax_identification_number,
        tax_service=tax_transaction_service,
    )

    return None


def post_payment(
    db: Session,
    organization_id: UUID,
    payment_id: UUID,
    posting_date: date,
    posted_by_user_id: UUID,
    idempotency_key: str | None = None,
) -> ARPostingResult:
    """
    Post a customer payment to the general ledger.

    Creates a journal entry with:
    - Debit: Bank/Cash account
    - Credit: AR Control account

    Args:
        db: Database session
        organization_id: Organization scope
        payment_id: Payment to post
        posting_date: Date for the GL posting
        posted_by_user_id: User posting
        idempotency_key: Optional idempotency key

    Returns:
        ARPostingResult with outcome
    """
    from app.models.finance.ar.customer_payment import CustomerPayment, PaymentStatus

    org_id = coerce_uuid(organization_id)
    pay_id = coerce_uuid(payment_id)
    user_id = coerce_uuid(posted_by_user_id)

    # Load payment
    payment = db.get(CustomerPayment, pay_id)
    if not payment or payment.organization_id != org_id:
        return ARPostingResult(success=False, message="Payment not found")

    # Allow posting for APPROVED (normal workflow) and for payments that are
    # already in a posted state but missing GL entries (sync/import backfill).
    postable_statuses = {
        PaymentStatus.APPROVED,
        PaymentStatus.CLEARED,
    }
    if payment.status not in postable_statuses:
        return ARPostingResult(
            success=False,
            message=f"Payment must be APPROVED or CLEARED to post (current: {payment.status.value})",
        )

    # Skip zero-amount payments — nothing meaningful to post to GL
    if payment.amount == Decimal("0"):
        return ARPostingResult(
            success=True,
            message="Zero amount payment — no GL posting needed",
        )

    # Load customer
    customer = db.get(Customer, payment.customer_id)
    if not customer:
        return ARPostingResult(success=False, message="Customer not found")

    exchange_rate = payment.exchange_rate or Decimal("1.0")
    functional_amount = payment.amount * exchange_rate

    if not payment.bank_account_id:
        return ARPostingResult(
            success=False, message="Payment has no bank account linked"
        )

    bank_gl_account_id = _resolve_bank_gl_account_id(
        db,
        org_id,
        payment.bank_account_id,
    )
    if not bank_gl_account_id:
        return ARPostingResult(
            success=False,
            message="Payment bank account is not mapped to a valid GL account",
        )

    # Build journal lines
    journal_lines = [
        # Debit Bank/Cash
        JournalLineInput(
            account_id=bank_gl_account_id,
            debit_amount=payment.amount,
            credit_amount=Decimal("0"),
            debit_amount_functional=functional_amount,
            credit_amount_functional=Decimal("0"),
            description=f"AR Payment: {payment.reference}",
        ),
        # Credit AR Control
        JournalLineInput(
            account_id=customer.ar_control_account_id,
            debit_amount=Decimal("0"),
            credit_amount=payment.amount,
            debit_amount_functional=Decimal("0"),
            credit_amount_functional=functional_amount,
            description=f"Payment from {customer.legal_name}",
        ),
    ]

    # Create journal entry
    journal_input = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=payment.payment_date,
        posting_date=posting_date,
        description=f"AR Payment {payment.payment_number} - {customer.legal_name}",
        reference=payment.reference,
        currency_code=payment.currency_code,
        exchange_rate=exchange_rate,
        lines=journal_lines,
        source_module="AR",
        source_document_type="CUSTOMER_PAYMENT",
        source_document_id=pay_id,
        correlation_id=payment.correlation_id,
    )

    if not idempotency_key:
        idempotency_key = BasePostingAdapter.make_idempotency_key(
            org_id, "AR:PAY", pay_id, action="post"
        )

    journal, posting_result = BasePostingAdapter.create_approve_and_post_journal(
        db,
        org_id,
        journal_input,
        user_id,
        posting_date=posting_date,
        idempotency_key=idempotency_key,
        source_module="AR",
        correlation_id=payment.correlation_id,
        success_message="Payment posted successfully",
    )
    if not posting_result.success:
        return ARPostingResult(
            success=False,
            journal_entry_id=journal.journal_entry_id if journal else None,
            message=posting_result.message,
        )
    assert journal is not None

    vat_reclass_result = post_vat_reclass_for_payment(
        db,
        organization_id=org_id,
        payment=payment,
        customer=customer,
        posting_date=posting_date,
        posted_by_user_id=user_id,
    )
    if vat_reclass_result is not None and not vat_reclass_result.success:
        return vat_reclass_result

    return ARPostingResult(
        success=True,
        journal_entry_id=journal.journal_entry_id,
        posting_batch_id=posting_result.posting_batch_id,
        message=posting_result.message,
    )
