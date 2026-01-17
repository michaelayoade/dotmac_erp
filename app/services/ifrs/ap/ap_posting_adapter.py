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
from app.models.ifrs.inv.item import Item
from app.models.ifrs.inv.item_category import ItemCategory
from app.services.common import coerce_uuid
from app.services.ifrs.gl.journal import JournalService, JournalInput, JournalLineInput
from app.services.ifrs.gl.ledger_posting import LedgerPostingService, PostingRequest
from app.services.ifrs.tax.tax_transaction import tax_transaction_service
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

        # Debit lines (expense/asset/inventory accounts)
        for inv_line in lines:
            # Determine account using smart routing
            account_id = APPostingAdapter._determine_debit_account(
                db, org_id, inv_line, supplier
            )

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

            # Create tax transactions for taxable invoice lines
            APPostingAdapter._create_tax_transactions(
                db=db,
                organization_id=org_id,
                invoice=invoice,
                lines=lines,
                supplier=supplier,
                exchange_rate=exchange_rate,
                is_credit_note=invoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE,
            )

            # Create fixed assets for capitalizable lines (AP → FA integration)
            # Only for standard invoices, not credit notes
            if invoice.invoice_type != SupplierInvoiceType.CREDIT_NOTE:
                APPostingAdapter._create_assets_for_capitalizable_lines(
                    db=db,
                    organization_id=org_id,
                    invoice=invoice,
                    lines=lines,
                    supplier=supplier,
                    user_id=user_id,
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

        # Determine amounts - handle WHT deduction
        # gross_amount = invoice amount (what we owe)
        # amount = net paid to bank (after WHT deduction)
        # withholding_tax_amount = WHT withheld
        wht_amount = payment.withholding_tax_amount or Decimal("0")
        gross_amount = payment.gross_amount or (payment.amount + wht_amount)
        net_amount = payment.amount

        gross_functional = gross_amount * exchange_rate
        net_functional = net_amount * exchange_rate
        wht_functional = wht_amount * exchange_rate

        # Build journal lines
        journal_lines = [
            # Debit AP Control (reduce liability) - GROSS amount
            JournalLineInput(
                account_id=supplier.ap_control_account_id,
                debit_amount=gross_amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=gross_functional,
                credit_amount_functional=Decimal("0"),
                description=f"Payment to {supplier.legal_name}",
            ),
            # Credit Bank/Cash - NET amount (what we actually pay)
            JournalLineInput(
                account_id=payment.bank_account_id,
                debit_amount=Decimal("0"),
                credit_amount=net_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=net_functional,
                description=f"AP Payment: {payment.payment_number}",
            ),
        ]

        # Add WHT Payable line if WHT is withheld
        # WHT we withhold goes to tax_collected_account (liability to remit to tax authority)
        if wht_amount > Decimal("0") and payment.withholding_tax_code_id:
            from app.models.ifrs.tax.tax_code import TaxCode

            wht_code = db.get(TaxCode, payment.withholding_tax_code_id)
            # Use tax_collected_account_id for WHT payable (what we owe to tax authority)
            wht_account_id = wht_code.tax_collected_account_id if wht_code else None

            if wht_account_id:
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

            # Create WHT tax transaction for reporting
            if wht_amount > Decimal("0") and payment.withholding_tax_code_id:
                APPostingAdapter._create_wht_transaction(
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

    @staticmethod
    def _create_tax_transactions(
        db: Session,
        organization_id: UUID,
        invoice: SupplierInvoice,
        lines: list[SupplierInvoiceLine],
        supplier: Supplier,
        exchange_rate: Decimal,
        is_credit_note: bool = False,
    ) -> list[UUID]:
        """
        Create tax transactions for supplier invoice lines with tax codes.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice: The supplier invoice being posted
            lines: Invoice lines
            supplier: Supplier for counterparty info
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

            # For credit notes, we record negative tax (reduces input tax)
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
                    is_purchase=True,  # AP = INPUT tax (purchases)
                    base_amount=base_amount,
                    currency_code=invoice.currency_code,
                    counterparty_name=supplier.legal_name,
                    counterparty_tax_id=supplier.tax_id,
                    exchange_rate=exchange_rate,
                )
                tax_transaction_ids.append(tax_txn.transaction_id)
            except Exception:
                # Log error but don't fail the posting
                pass

        return tax_transaction_ids

    @staticmethod
    def _create_wht_transaction(
        db: Session,
        organization_id: UUID,
        payment,  # SupplierPayment
        supplier: Supplier,
        wht_amount: Decimal,
        exchange_rate: Decimal,
    ) -> Optional[UUID]:
        """
        Create a WHT tax transaction for a supplier payment.

        This records the withholding tax withheld from the supplier payment
        for tax reporting purposes.

        Args:
            db: Database session
            organization_id: Organization scope
            payment: SupplierPayment object
            supplier: Supplier object
            wht_amount: WHT amount withheld
            exchange_rate: Exchange rate to functional currency

        Returns:
            Transaction ID if created, None otherwise
        """
        from app.models.ifrs.gl.fiscal_period import FiscalPeriod
        from app.models.ifrs.tax.tax_transaction import TaxTransactionType
        from app.services.ifrs.tax.tax_transaction import TaxTransactionInput

        # Get fiscal period from payment date
        fiscal_period = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.organization_id == organization_id,
                FiscalPeriod.start_date <= payment.payment_date,
                FiscalPeriod.end_date >= payment.payment_date,
            )
            .first()
        )

        if not fiscal_period:
            return None

        try:
            # Calculate gross amount (base for WHT)
            gross_amount = payment.gross_amount or (payment.amount + wht_amount)

            tax_txn = tax_transaction_service.record_transaction(
                db=db,
                organization_id=organization_id,
                input=TaxTransactionInput(
                    tax_code_id=payment.withholding_tax_code_id,
                    transaction_type=TaxTransactionType.WITHHOLDING,
                    fiscal_period_id=fiscal_period.fiscal_period_id,
                    transaction_date=payment.payment_date,
                    base_amount=gross_amount,
                    tax_amount=wht_amount,
                    currency_code=payment.currency_code,
                    exchange_rate=exchange_rate,
                    functional_currency_base=gross_amount * exchange_rate,
                    functional_currency_tax=wht_amount * exchange_rate,
                    source_module="AP",
                    source_document_type="SUPPLIER_PAYMENT",
                    source_document_id=payment.payment_id,
                    counterparty_name=supplier.legal_name,
                    counterparty_tax_id=supplier.tax_id,
                    reference=payment.payment_number,
                ),
            )
            return tax_txn.transaction_id
        except Exception:
            # Log error but don't fail the posting
            return None

    @staticmethod
    def _determine_debit_account(
        db: Session,
        organization_id: UUID,
        line: SupplierInvoiceLine,
        supplier: Supplier,
    ) -> Optional[UUID]:
        """
        Determine the appropriate debit account for an invoice line.

        Routing logic:
        1. If line has item_id → use inventory account from Item or ItemCategory
        2. If line has goods_receipt_line_id → use GRNI account (for matched items)
        3. If line has asset_account_id (capitalization) → use asset account
        4. Else → use expense_account_id or supplier default

        Args:
            db: Database session
            organization_id: Organization scope
            line: The invoice line
            supplier: The supplier for default accounts

        Returns:
            Account UUID or None if not determinable
        """
        # Priority 1: Inventory item - route to inventory account
        if line.item_id:
            item = db.get(Item, line.item_id)
            if item:
                # Check item-level override first
                if item.inventory_account_id:
                    return item.inventory_account_id

                # Fall back to category inventory account
                if item.category_id:
                    category = db.get(ItemCategory, item.category_id)
                    if category and category.inventory_account_id:
                        return category.inventory_account_id

        # Priority 2: GR-matched line - use GRNI clearing account
        # (In GRNI accounting, goods receipt debits Inventory/Cr GRNI
        #  Invoice then debits GRNI/Cr AP to clear the accrual)
        if line.goods_receipt_line_id:
            # Get GRNI account from organization settings
            from app.models.core_org.organization import Organization
            org = db.get(Organization, organization_id)
            if org and hasattr(org, 'grni_account_id') and org.grni_account_id:
                return org.grni_account_id
            # If no GRNI account configured, fall through to expense routing

        # Priority 3: Capitalize flag - use asset account
        if line.capitalize_flag and line.asset_account_id:
            return line.asset_account_id

        # Priority 4: Explicit expense account on line
        if line.expense_account_id:
            return line.expense_account_id

        # Priority 5: Asset account on line (non-capitalize)
        if line.asset_account_id:
            return line.asset_account_id

        # Priority 6: Supplier default
        return supplier.default_expense_account_id

    @staticmethod
    def _create_assets_for_capitalizable_lines(
        db: Session,
        organization_id: UUID,
        invoice: SupplierInvoice,
        lines: list[SupplierInvoiceLine],
        supplier: Supplier,
        user_id: UUID,
    ) -> None:
        """
        Create fixed assets for invoice lines marked for capitalization.

        Uses the CapitalizationService to create DRAFT assets for lines
        that have capitalize_flag=True and asset_category_id set.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice: The posted invoice
            lines: Invoice lines to check
            supplier: Supplier for asset linkage
            user_id: User creating the assets
        """
        from app.services.ifrs.fa.capitalization import CapitalizationService

        # Check if any lines are capitalizable
        capitalizable_lines = [
            line for line in lines
            if line.capitalize_flag and line.asset_category_id
        ]

        if not capitalizable_lines:
            return

        # Create assets through CapitalizationService
        result = CapitalizationService.create_assets_from_invoice(
            db=db,
            organization_id=organization_id,
            invoice=invoice,
            lines=capitalizable_lines,
            supplier=supplier,
            user_id=user_id,
        )

        # Log errors but don't fail the posting
        # (Assets are supplementary - invoice posting should still succeed)
        if result.errors:
            # In production, log these errors
            pass


# Module-level singleton instance
ap_posting_adapter = APPostingAdapter()
