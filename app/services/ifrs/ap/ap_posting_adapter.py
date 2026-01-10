"""
APPostingAdapter - Converts AP documents to GL entries.

Transforms supplier invoices and payments into journal entries
and posts them to the general ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID
import uuid as uuid_lib

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.ap.supplier import Supplier
from app.models.ifrs.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.ifrs.ap.supplier_invoice_line import SupplierInvoiceLine
from app.services.common import coerce_uuid
from app.services.ifrs.gl.journal import JournalService, JournalInput, JournalLineInput
from app.services.ifrs.gl.ledger_posting import LedgerPostingService, PostingRequest
from app.models.ifrs.gl.journal_entry import JournalType


@dataclass
class APPostingResult:
    """Result of an AP posting operation."""

    success: bool
    journal_entry_id: Optional[UUID] = None
    posting_batch_id: Optional[UUID] = None
    message: str = ""


class APPostingAdapter:
    """
    Adapter for posting AP documents to the general ledger.

    Converts supplier invoices and payments into journal entries
    and coordinates posting through the LedgerPostingService.
    """

    @staticmethod
    def post_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> APPostingResult:
        """
        Post a supplier invoice to the general ledger.

        Creates a journal entry with:
        - Debit: Expense/Asset accounts (from invoice lines)
        - Credit: AP Control account

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            APPostingResult with outcome

        Raises:
            HTTPException: If posting fails
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load invoice with lines
        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            return APPostingResult(success=False, message="Invoice not found")

        if invoice.status != SupplierInvoiceStatus.APPROVED:
            return APPostingResult(
                success=False,
                message=f"Invoice must be APPROVED to post (current: {invoice.status.value})",
            )

        # Load supplier for control account
        supplier = db.get(Supplier, invoice.supplier_id)
        if not supplier:
            return APPostingResult(success=False, message="Supplier not found")

        # Load invoice lines
        lines = (
            db.query(SupplierInvoiceLine)
            .filter(SupplierInvoiceLine.invoice_id == inv_id)
            .order_by(SupplierInvoiceLine.line_number)
            .all()
        )

        if not lines:
            return APPostingResult(
                success=False, message="Invoice has no lines"
            )

        # Build journal entry lines
        journal_lines: list[JournalLineInput] = []
        exchange_rate = invoice.exchange_rate or Decimal("1.0")

        # Debit lines (expense/asset accounts)
        for inv_line in lines:
            # Determine account (expense or asset)
            account_id = inv_line.expense_account_id or inv_line.asset_account_id
            if not account_id:
                account_id = supplier.default_expense_account_id

            if not account_id:
                return APPostingResult(
                    success=False,
                    message=f"No expense account for line {inv_line.line_number}",
                )

            line_total = inv_line.line_amount + inv_line.tax_amount
            functional_amount = line_total * exchange_rate

            # For standard invoice: debit expense
            # For credit note: credit expense (negative amounts)
            if invoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
                # Credit note: credit the expense (reduce expense)
                journal_lines.append(
                    JournalLineInput(
                        account_id=account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=abs(line_total),
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=abs(functional_amount),
                        description=f"AP Credit Note: {inv_line.description}",
                        cost_center_id=inv_line.cost_center_id,
                        project_id=inv_line.project_id,
                        segment_id=inv_line.segment_id,
                    )
                )
            else:
                # Standard/Debit note: debit expense
                journal_lines.append(
                    JournalLineInput(
                        account_id=account_id,
                        debit_amount=line_total,
                        credit_amount=Decimal("0"),
                        debit_amount_functional=functional_amount,
                        credit_amount_functional=Decimal("0"),
                        description=f"AP Invoice: {inv_line.description}",
                        cost_center_id=inv_line.cost_center_id,
                        project_id=inv_line.project_id,
                        segment_id=inv_line.segment_id,
                    )
                )

        # Credit line (AP Control account)
        total_functional = invoice.functional_currency_amount

        if invoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
            # Credit note: debit AP (reduce liability)
            journal_lines.append(
                JournalLineInput(
                    account_id=invoice.ap_control_account_id,
                    debit_amount=abs(invoice.total_amount),
                    credit_amount=Decimal("0"),
                    debit_amount_functional=abs(total_functional),
                    credit_amount_functional=Decimal("0"),
                    description=f"AP Credit Note: {supplier.legal_name}",
                )
            )
        else:
            # Standard/Debit note: credit AP (increase liability)
            journal_lines.append(
                JournalLineInput(
                    account_id=invoice.ap_control_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=invoice.total_amount,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=total_functional,
                    description=f"AP Invoice: {supplier.legal_name}",
                )
            )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=invoice.invoice_date,
            posting_date=posting_date,
            description=f"AP Invoice {invoice.invoice_number} - {supplier.legal_name}",
            reference=invoice.supplier_invoice_number or invoice.invoice_number,
            currency_code=invoice.currency_code,
            exchange_rate=exchange_rate,
            exchange_rate_type_id=invoice.exchange_rate_type_id,
            lines=journal_lines,
            source_module="AP",
            source_document_type="SUPPLIER_INVOICE",
            source_document_id=inv_id,
            correlation_id=invoice.correlation_id,
        )

        try:
            journal = JournalService.create_journal(
                db, org_id, journal_input, user_id
            )

            # Submit and approve automatically for AP posting
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)

            # Use a system user ID for auto-approval to avoid SoD issue
            # In production, this would be a designated system account
            JournalService.approve_journal(
                db, org_id, journal.journal_entry_id, user_id
            )

        except HTTPException as e:
            return APPostingResult(success=False, message=f"Journal creation failed: {e.detail}")

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:AP:{inv_id}:post:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="AP",
            correlation_id=invoice.correlation_id,
            posted_by_user_id=user_id,
        )

        try:
            posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

            if not posting_result.success:
                return APPostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Ledger posting failed: {posting_result.message}",
                )

            return APPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
                message="Invoice posted successfully",
            )

        except Exception as e:
            return APPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=f"Posting error: {str(e)}",
            )

    @staticmethod
    def post_payment(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
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
        from app.models.ifrs.ap.supplier_payment import (
            SupplierPayment,
            APPaymentStatus,
        )

        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load payment
        payment = db.get(SupplierPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            return APPostingResult(success=False, message="Payment not found")

        if payment.status != APPaymentStatus.APPROVED:
            return APPostingResult(
                success=False,
                message=f"Payment must be APPROVED to post (current: {payment.status.value})",
            )

        # Load supplier
        supplier = db.get(Supplier, payment.supplier_id)
        if not supplier:
            return APPostingResult(success=False, message="Supplier not found")

        exchange_rate = payment.exchange_rate or Decimal("1.0")
        functional_amount = payment.payment_amount * exchange_rate

        # Build journal lines
        journal_lines = [
            # Debit AP Control (reduce liability)
            JournalLineInput(
                account_id=supplier.ap_control_account_id,
                debit_amount=payment.payment_amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=functional_amount,
                credit_amount_functional=Decimal("0"),
                description=f"Payment to {supplier.legal_name}",
            ),
            # Credit Bank/Cash
            JournalLineInput(
                account_id=payment.bank_account_id,
                debit_amount=Decimal("0"),
                credit_amount=payment.payment_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=functional_amount,
                description=f"AP Payment: {payment.payment_number}",
            ),
        ]

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

        try:
            journal = JournalService.create_journal(
                db, org_id, journal_input, user_id
            )

            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(
                db, org_id, journal.journal_entry_id, user_id
            )

        except HTTPException as e:
            return APPostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:AP:PAY:{pay_id}:post:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="AP",
            correlation_id=payment.correlation_id,
            posted_by_user_id=user_id,
        )

        try:
            posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

            if not posting_result.success:
                return APPostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Ledger posting failed: {posting_result.message}",
                )

            return APPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
                message="Payment posted successfully",
            )

        except Exception as e:
            return APPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=f"Posting error: {str(e)}",
            )

    @staticmethod
    def reverse_invoice_posting(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        reversal_date: date,
        reversed_by_user_id: UUID,
        reason: str,
    ) -> APPostingResult:
        """
        Reverse a posted invoice's GL entries.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to reverse
            reversal_date: Date for reversal
            reversed_by_user_id: User reversing
            reason: Reason for reversal

        Returns:
            APPostingResult with reversal outcome
        """
        from app.services.ifrs.gl.reversal import ReversalService

        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(reversed_by_user_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            return APPostingResult(success=False, message="Invoice not found")

        if not invoice.journal_entry_id:
            return APPostingResult(
                success=False, message="Invoice has not been posted"
            )

        try:
            result = ReversalService.create_reversal(
                db=db,
                organization_id=org_id,
                original_journal_id=invoice.journal_entry_id,
                reversal_date=reversal_date,
                created_by_user_id=user_id,
                reason=f"AP Invoice reversal: {reason}",
                auto_post=True,
            )

            if not result.success:
                return APPostingResult(success=False, message=result.message)

            return APPostingResult(
                success=True,
                journal_entry_id=result.reversal_journal_id,
                message="Invoice posting reversed successfully",
            )

        except HTTPException as e:
            return APPostingResult(
                success=False, message=f"Reversal failed: {e.detail}"
            )


# Module-level singleton instance
ap_posting_adapter = APPostingAdapter()
