"""
ARPostingAdapter - Converts AR documents to GL entries.

Transforms invoices and payments into journal entries
and posts them to the general ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.ar.customer import Customer
from app.models.ifrs.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.ifrs.ar.invoice_line import InvoiceLine
from app.models.ifrs.gl.fiscal_period import FiscalPeriod
from app.services.common import coerce_uuid
from app.services.ifrs.ar.ar_inventory_integration import ARInventoryIntegration
from app.services.ifrs.gl.journal import JournalService, JournalInput, JournalLineInput
from app.services.ifrs.gl.ledger_posting import LedgerPostingService, PostingRequest
from app.services.ifrs.tax.tax_transaction import tax_transaction_service
from app.models.ifrs.gl.journal_entry import JournalType


@dataclass
class ARPostingResult:
    """Result of an AR posting operation."""

    success: bool
    journal_entry_id: Optional[UUID] = None
    posting_batch_id: Optional[UUID] = None
    message: str = ""


class ARPostingAdapter:
    """
    Adapter for posting AR documents to the general ledger.

    Converts invoices and payments into journal entries
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
    ) -> ARPostingResult:
        """
        Post an AR invoice to the general ledger.

        Creates a journal entry with:
        - Debit: AR Control account
        - Credit: Revenue accounts (from invoice lines)

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            ARPostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load invoice
        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            return ARPostingResult(success=False, message="Invoice not found")

        if invoice.status != InvoiceStatus.APPROVED:
            return ARPostingResult(
                success=False,
                message=f"Invoice must be APPROVED to post (current: {invoice.status.value})",
            )

        # Load customer
        customer = db.get(Customer, invoice.customer_id)
        if not customer:
            return ARPostingResult(success=False, message="Customer not found")

        # Load invoice lines
        lines = (
            db.query(InvoiceLine)
            .filter(InvoiceLine.invoice_id == inv_id)
            .order_by(InvoiceLine.line_number)
            .all()
        )

        if not lines:
            return ARPostingResult(success=False, message="Invoice has no lines")

        # Get fiscal period for inventory transactions
        fiscal_period = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= invoice.invoice_date,
                FiscalPeriod.end_date >= invoice.invoice_date,
            )
            .first()
        )

        # Check if there are inventory lines
        inventory_lines = [line for line in lines if line.item_id]
        is_credit_note = invoice.invoice_type == InvoiceType.CREDIT_NOTE

        # Validate inventory availability for standard invoices (not credit notes)
        if inventory_lines and not is_credit_note:
            is_valid, validation_errors = ARInventoryIntegration.validate_inventory_availability(
                db=db,
                organization_id=org_id,
                lines=inventory_lines,
            )
            if not is_valid:
                return ARPostingResult(
                    success=False,
                    message=f"Insufficient inventory: {'; '.join(validation_errors)}",
                )

        # Build journal entry lines
        journal_lines: list[JournalLineInput] = []
        exchange_rate = invoice.exchange_rate or Decimal("1.0")

        # Debit line (AR Control account)
        total_functional = invoice.functional_currency_amount

        if invoice.invoice_type == InvoiceType.CREDIT_NOTE:
            # Credit note: credit AR (reduce receivable)
            journal_lines.append(
                JournalLineInput(
                    account_id=invoice.ar_control_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=abs(invoice.total_amount),
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=abs(total_functional),
                    description=f"AR Credit Note: {customer.legal_name}",
                )
            )
        else:
            # Standard: debit AR (increase receivable)
            journal_lines.append(
                JournalLineInput(
                    account_id=invoice.ar_control_account_id,
                    debit_amount=invoice.total_amount,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=total_functional,
                    credit_amount_functional=Decimal("0"),
                    description=f"AR Invoice: {customer.legal_name}",
                )
            )

        # Credit lines (revenue accounts)
        for inv_line in lines:
            account_id = inv_line.revenue_account_id
            if not account_id:
                account_id = customer.default_revenue_account_id

            if not account_id:
                return ARPostingResult(
                    success=False,
                    message=f"No revenue account for line {inv_line.line_number}",
                )

            line_total = inv_line.line_amount + inv_line.tax_amount
            functional_amount = line_total * exchange_rate

            if invoice.invoice_type == InvoiceType.CREDIT_NOTE:
                # Credit note: debit revenue (reduce revenue)
                journal_lines.append(
                    JournalLineInput(
                        account_id=account_id,
                        debit_amount=abs(line_total),
                        credit_amount=Decimal("0"),
                        debit_amount_functional=abs(functional_amount),
                        credit_amount_functional=Decimal("0"),
                        description=f"AR Credit Note: {inv_line.description}",
                        cost_center_id=inv_line.cost_center_id,
                        project_id=inv_line.project_id,
                        segment_id=inv_line.segment_id,
                    )
                )
            else:
                # Standard: credit revenue
                journal_lines.append(
                    JournalLineInput(
                        account_id=account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=line_total,
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=functional_amount,
                        description=f"AR Invoice: {inv_line.description}",
                        cost_center_id=inv_line.cost_center_id,
                        project_id=inv_line.project_id,
                        segment_id=inv_line.segment_id,
                    )
                )

        # Process inventory lines and get COGS journal entries
        inventory_result = None
        if inventory_lines and fiscal_period:
            inventory_result = ARInventoryIntegration.process_invoice_inventory(
                db=db,
                organization_id=org_id,
                invoice=invoice,
                lines=inventory_lines,
                fiscal_period_id=fiscal_period.fiscal_period_id,
                user_id=user_id,
                is_credit_note=is_credit_note,
            )

            # Add COGS journal lines to the entry
            if inventory_result.cogs_journal_lines:
                journal_lines.extend(inventory_result.cogs_journal_lines)

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=invoice.invoice_date,
            posting_date=posting_date,
            description=f"AR Invoice {invoice.invoice_number} - {customer.legal_name}",
            reference=invoice.invoice_number,
            currency_code=invoice.currency_code,
            exchange_rate=exchange_rate,
            exchange_rate_type_id=invoice.exchange_rate_type_id,
            lines=journal_lines,
            source_module="AR",
            source_document_type="INVOICE",
            source_document_id=inv_id,
            correlation_id=invoice.correlation_id,
        )

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)

        except HTTPException as e:
            return ARPostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:AR:{inv_id}:post:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="AR",
            correlation_id=invoice.correlation_id,
            posted_by_user_id=user_id,
        )

        try:
            posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

            if not posting_result.success:
                return ARPostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Ledger posting failed: {posting_result.message}",
                )

            # Create tax transactions for taxable invoice lines
            ARPostingAdapter._create_tax_transactions(
                db=db,
                organization_id=org_id,
                invoice=invoice,
                lines=lines,
                customer=customer,
                exchange_rate=exchange_rate,
                is_credit_note=invoice.invoice_type == InvoiceType.CREDIT_NOTE,
            )

            return ARPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
                message="Invoice posted successfully",
            )

        except Exception as e:
            return ARPostingResult(
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
    ) -> ARPostingResult:
        """
        Post a customer payment to the general ledger.

        Creates a journal entry with:
        - Debit: Bank/Cash account
        - Credit: AR Control account
        """
        from app.models.ifrs.ar.customer_payment import CustomerPayment, PaymentStatus

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

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)

        except HTTPException as e:
            return ARPostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:AR:PAY:{pay_id}:post:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="AR",
            correlation_id=payment.correlation_id,
            posted_by_user_id=user_id,
        )

        try:
            posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

            if not posting_result.success:
                return ARPostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Ledger posting failed: {posting_result.message}",
                )

            return ARPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
                message="Payment posted successfully",
            )

        except Exception as e:
            return ARPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=f"Posting error: {str(e)}",
            )

    @staticmethod
    def _create_tax_transactions(
        db: Session,
        organization_id: UUID,
        invoice: Invoice,
        lines: list[InvoiceLine],
        customer: Customer,
        exchange_rate: Decimal,
        is_credit_note: bool = False,
    ) -> list[UUID]:
        """
        Create tax transactions for invoice lines with tax codes.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice: The invoice being posted
            lines: Invoice lines
            customer: Customer for counterparty info
            exchange_rate: Exchange rate to functional currency
            is_credit_note: Whether this is a credit note (negative amounts)

        Returns:
            List of created tax transaction IDs
        """
        from app.models.ifrs.gl.fiscal_period import FiscalPeriod

        tax_transaction_ids = []

        # Get fiscal period from invoice date
        fiscal_period = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.organization_id == organization_id,
                FiscalPeriod.start_date <= invoice.invoice_date,
                FiscalPeriod.end_date >= invoice.invoice_date,
            )
            .first()
        )

        if not fiscal_period:
            # No fiscal period found - skip tax transactions
            return tax_transaction_ids

        for line in lines:
            if not line.tax_code_id or line.tax_amount == Decimal("0"):
                continue

            # For credit notes, we record negative tax (reduces output tax)
            base_amount = line.line_amount if not is_credit_note else -line.line_amount

            try:
                tax_txn = tax_transaction_service.create_from_invoice_line(
                    db=db,
                    organization_id=organization_id,
                    fiscal_period_id=fiscal_period.fiscal_period_id,
                    tax_code_id=line.tax_code_id,
                    invoice_id=invoice.invoice_id,
                    invoice_line_id=line.line_id,
                    invoice_number=invoice.invoice_number,
                    transaction_date=invoice.invoice_date,
                    is_purchase=False,  # AR = OUTPUT tax (sales)
                    base_amount=base_amount,
                    currency_code=invoice.currency_code,
                    counterparty_name=customer.legal_name,
                    counterparty_tax_id=customer.tax_id,
                    exchange_rate=exchange_rate,
                )
                tax_transaction_ids.append(tax_txn.transaction_id)
            except Exception:
                # Log error but don't fail the posting
                pass

        return tax_transaction_ids


# Module-level singleton instance
ar_posting_adapter = ARPostingAdapter()
