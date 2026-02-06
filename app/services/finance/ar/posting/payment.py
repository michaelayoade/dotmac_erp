"""
AR Payment Posting - Post customer payments to GL.

Transforms customer payments into journal entries with:
- Debit: Bank/Cash account
- Credit: AR Control account (reduce receivable)
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.gl.journal_entry import JournalType
from app.services.common import coerce_uuid
from app.services.finance.ar.posting.result import ARPostingResult
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
)
from app.services.finance.posting.base import BasePostingAdapter


def post_payment(
    db: Session,
    organization_id: UUID,
    payment_id: UUID,
    posting_date: date,
    posted_by_user_id: UUID,
    idempotency_key: Optional[str] = None,
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

    if payment.status != PaymentStatus.APPROVED:
        return ARPostingResult(
            success=False,
            message=f"Payment must be APPROVED to post (current: {payment.status.value})",
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

    # Build journal lines
    journal_lines = [
        # Debit Bank/Cash
        JournalLineInput(
            account_id=payment.bank_account_id,
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

    journal, error = BasePostingAdapter.create_and_approve_journal(
        db,
        org_id,
        journal_input,
        user_id,
        error_prefix="Journal creation failed",
    )
    if error:
        return ARPostingResult(success=False, message=error.message)

    # Post to ledger
    if not idempotency_key:
        idempotency_key = BasePostingAdapter.make_idempotency_key(
            org_id, "AR:PAY", pay_id, action="post"
        )

    posting_result = BasePostingAdapter.post_to_ledger(
        db,
        organization_id=org_id,
        journal_entry_id=journal.journal_entry_id,
        posting_date=posting_date,
        idempotency_key=idempotency_key,
        source_module="AR",
        correlation_id=payment.correlation_id,
        posted_by_user_id=user_id,
        success_message="Payment posted successfully",
    )
    if not posting_result.success:
        return ARPostingResult(
            success=False,
            journal_entry_id=journal.journal_entry_id,
            message=posting_result.message,
        )

    return ARPostingResult(
        success=True,
        journal_entry_id=journal.journal_entry_id,
        posting_batch_id=posting_result.posting_batch_id,
        message=posting_result.message,
    )
