"""
AP Payment Posting - Post supplier payments to GL.

Transforms supplier payments into journal entries with:
- Debit: AP Control account (reduce liability)
- Credit: Bank/Cash account
- Credit: WHT Payable (if withholding tax applies)
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.ap_payment_allocation import APPaymentAllocation
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import SupplierInvoice
from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.gl.account import Account
from app.models.finance.gl.journal_entry import JournalType
from app.services.common import coerce_uuid
from app.services.finance.ap.posting.helpers import create_wht_transaction
from app.services.finance.ap.posting.result import APPostingResult
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
)
from app.services.finance.posting.base import BasePostingAdapter


def _resolve_bank_gl_account_id(
    db: Session,
    organization_id: UUID,
    bank_account_id: UUID,
) -> UUID | None:
    """Resolve payment bank account to a GL account ID."""
    gl_account = db.get(Account, bank_account_id)
    if gl_account and gl_account.organization_id == organization_id:
        return bank_account_id

    bank_account = db.get(BankAccount, bank_account_id)
    if (
        bank_account
        and bank_account.organization_id == organization_id
        and bank_account.gl_account_id
    ):
        mapped_gl = db.get(Account, bank_account.gl_account_id)
        if mapped_gl and mapped_gl.organization_id == organization_id:
            return bank_account.gl_account_id

    return None


def post_payment(
    db: Session,
    organization_id: UUID,
    payment_id: UUID,
    posting_date: date,
    posted_by_user_id: UUID,
    idempotency_key: str | None = None,
) -> APPostingResult:
    """
    Post a supplier payment to the general ledger.

    Creates a journal entry with:
    - Debit: AP Control account
    - Credit: Bank/Cash account

    Args:
        db: Database session
        organization_id: Organization scope
        payment_id: Payment to post
        posting_date: Date for the GL posting
        posted_by_user_id: User posting
        idempotency_key: Optional idempotency key

    Returns:
        APPostingResult with outcome
    """
    from app.models.finance.ap.supplier_payment import (
        APPaymentStatus,
        SupplierPayment,
    )

    org_id = coerce_uuid(organization_id)
    pay_id = coerce_uuid(payment_id)
    user_id = coerce_uuid(posted_by_user_id)

    # Load payment
    payment = db.get(SupplierPayment, pay_id)
    if not payment or payment.organization_id != org_id:
        return APPostingResult(success=False, message="Payment not found")

    # Allow posting for APPROVED (normal workflow) and for payments that are
    # already in a posted state but missing GL entries (sync/import backfill).
    postable_statuses = {
        APPaymentStatus.APPROVED,
        APPaymentStatus.SENT,
        # Allow backfill/sync payments that already cleared but lack GL posting.
        APPaymentStatus.CLEARED,
    }
    if payment.status not in postable_statuses:
        return APPostingResult(
            success=False,
            message=f"Payment must be APPROVED, SENT, or CLEARED to post (current: {payment.status.value})",
        )

    # Skip zero-amount payments — nothing meaningful to post to GL
    if payment.amount == Decimal("0"):
        return APPostingResult(
            success=True,
            message="Zero amount payment — no GL posting needed",
        )

    # Load supplier
    supplier = db.get(Supplier, payment.supplier_id)
    if not supplier:
        return APPostingResult(success=False, message="Supplier not found")

    bank_gl_account_id = _resolve_bank_gl_account_id(
        db=db,
        organization_id=org_id,
        bank_account_id=payment.bank_account_id,
    )
    if not bank_gl_account_id:
        return APPostingResult(
            success=False,
            message="Payment bank account is not mapped to a valid GL account",
        )

    exchange_rate = payment.exchange_rate or Decimal("1.0")

    # Determine amounts - handle WHT deduction
    # gross_amount = invoice amount (what we owe)
    # amount = net paid to bank (after WHT deduction)
    # withholding_tax_amount = WHT withheld
    wht_amount = getattr(payment, "withholding_tax_amount", None) or Decimal("0")
    gross_amount = payment.gross_amount or (payment.amount + wht_amount)
    net_amount = payment.amount

    net_functional = net_amount * exchange_rate
    wht_functional = wht_amount * exchange_rate

    # Resolve AP control account from allocated invoices so the debit
    # matches the account originally credited when each invoice was posted.
    # If multiple invoices use different AP control accounts we create one
    # debit line per distinct account, weighted by allocated amount.
    allocations = list(
        db.scalars(
            select(APPaymentAllocation).where(
                APPaymentAllocation.payment_id == pay_id,
            )
        ).all()
    )

    # Map each distinct AP control account to its share of the gross amount.
    ap_account_amounts: dict[UUID, Decimal] = {}
    if allocations:
        for alloc in allocations:
            inv = db.get(SupplierInvoice, alloc.invoice_id)
            if not inv or inv.organization_id != org_id:
                continue
            acct_id = inv.ap_control_account_id
            ap_account_amounts[acct_id] = (
                ap_account_amounts.get(acct_id, Decimal("0")) + alloc.allocated_amount
            )

    # Fall back to the supplier's current default when there are no
    # allocations (e.g. on-account / advance payments).
    if not ap_account_amounts:
        ap_account_amounts[supplier.ap_control_account_id] = gross_amount

    # Scale allocation totals so they sum to the payment gross amount,
    # which may differ from the sum of allocations when WHT is involved.
    alloc_total = sum(ap_account_amounts.values())
    if alloc_total and alloc_total != gross_amount:
        scale = gross_amount / alloc_total
        ap_account_amounts = {
            acct: (amt * scale).quantize(Decimal("0.000001"))
            for acct, amt in ap_account_amounts.items()
        }
        # Adjust rounding on the last entry so the sum matches exactly.
        rounding_diff = gross_amount - sum(ap_account_amounts.values())
        if rounding_diff:
            last_key = list(ap_account_amounts.keys())[-1]
            ap_account_amounts[last_key] += rounding_diff

    # Build journal lines — one AP debit line per distinct control account
    journal_lines: list[JournalLineInput] = []
    for acct_id, acct_amount in ap_account_amounts.items():
        acct_functional = acct_amount * exchange_rate
        journal_lines.append(
            # Debit AP Control (reduce liability)
            JournalLineInput(
                account_id=acct_id,
                debit_amount=acct_amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=acct_functional,
                credit_amount_functional=Decimal("0"),
                description=f"Payment to {supplier.legal_name}",
            ),
        )

    # Credit Bank/Cash - NET amount (what we actually pay)
    journal_lines.append(
        JournalLineInput(
            account_id=bank_gl_account_id,
            debit_amount=Decimal("0"),
            credit_amount=net_amount,
            debit_amount_functional=Decimal("0"),
            credit_amount_functional=net_functional,
            description=f"AP Payment: {payment.payment_number}",
        ),
    )

    # Add WHT Payable line if WHT is withheld
    # WHT we withhold goes to tax_collected_account (liability to remit to tax authority)
    if wht_amount > Decimal("0"):
        from app.models.finance.tax.tax_code import TaxCode, TaxType

        if not payment.withholding_tax_code_id:
            return APPostingResult(
                success=False,
                message="WHT tax code is required when withholding amount is specified",
            )
        wht_code = db.get(TaxCode, payment.withholding_tax_code_id)
        if not wht_code or wht_code.organization_id != org_id:
            return APPostingResult(success=False, message="WHT tax code not found")
        if wht_code.tax_type != TaxType.WITHHOLDING:
            return APPostingResult(
                success=False,
                message="Selected tax code is not a WITHHOLDING tax code",
            )
        # Use tax_collected_account_id for WHT payable (what we owe to tax authority)
        wht_account_id = wht_code.tax_collected_account_id

        if not wht_account_id:
            return APPostingResult(
                success=False,
                message="WHT payable account is not configured on the WHT tax code",
            )
        journal_lines.append(
            JournalLineInput(
                account_id=wht_account_id,
                debit_amount=Decimal("0"),
                credit_amount=wht_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=wht_functional,
                description=f"WHT withheld: {payment.payment_number}",
            )
        )

    # Create journal entry
    journal_input = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=payment.payment_date,
        posting_date=posting_date,
        description=f"AP Payment {payment.payment_number} - {supplier.legal_name}",
        reference=payment.payment_number,
        currency_code=payment.currency_code,
        exchange_rate=exchange_rate,
        lines=journal_lines,
        source_module="AP",
        source_document_type="SUPPLIER_PAYMENT",
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
        return APPostingResult(success=False, message=error.message)

    # Post to ledger
    if not idempotency_key:
        idempotency_key = BasePostingAdapter.make_idempotency_key(
            org_id, "AP:PAY", pay_id, action="post"
        )

    posting_result = BasePostingAdapter.post_to_ledger(
        db,
        organization_id=org_id,
        journal_entry_id=journal.journal_entry_id,
        posting_date=posting_date,
        idempotency_key=idempotency_key,
        source_module="AP",
        correlation_id=payment.correlation_id,
        posted_by_user_id=user_id,
        success_message="Payment posted successfully",
    )
    if not posting_result.success:
        return APPostingResult(
            success=False,
            journal_entry_id=journal.journal_entry_id,
            message=posting_result.message,
        )

    # Create WHT tax transaction for reporting
    if wht_amount > Decimal("0") and payment.withholding_tax_code_id:
        create_wht_transaction(
            db=db,
            organization_id=org_id,
            payment=payment,
            supplier=supplier,
            wht_amount=wht_amount,
            exchange_rate=exchange_rate,
        )

    return APPostingResult(
        success=True,
        journal_entry_id=journal.journal_entry_id,
        posting_batch_id=posting_result.posting_batch_id,
        message=posting_result.message,
    )
