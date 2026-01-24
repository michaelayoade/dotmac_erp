"""
ExpensePostingAdapter - Converts expense claims to GL entries.

Transforms approved expense claims into supplier invoices and journal entries,
posting them to the general ledger for AP processing.
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

from app.models.expense.cash_advance import CashAdvance, CashAdvanceStatus
from app.models.expense.expense_claim import (
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseClaimStatus,
    ExpenseCategory,
)
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.gl.journal_entry import JournalType
from app.services.common import coerce_uuid
from app.services.finance.gl.journal import JournalService, JournalInput, JournalLineInput
from app.services.finance.gl.ledger_posting import LedgerPostingService, PostingRequest


@dataclass
class ExpensePostingResult:
    """Result of an expense posting operation."""

    success: bool
    journal_entry_id: Optional[UUID] = None
    supplier_invoice_id: Optional[UUID] = None
    posting_batch_id: Optional[UUID] = None
    message: str = ""


class ExpensePostingAdapter:
    """
    Adapter for posting expense claims to the general ledger.

    Converts approved expense claims into journal entries and optionally
    creates supplier invoices for AP processing.

    GL Entry Structure for expense claims:
    - DEBIT: Expense accounts (from category mappings or line item overrides)
    - CREDIT: Employee payable account (configurable per organization)

    For cash advance settlement:
    - DEBIT: Employee advance account (reduce receivable)
    - CREDIT: Expense accounts (offset)
    """

    # Default account codes (fallback if org settings not configured)
    DEFAULT_EMPLOYEE_PAYABLE_ACCOUNT_CODE = "2110"  # Current Liabilities - Accrued Expenses

    @staticmethod
    def post_expense_claim(
        db: Session,
        organization_id: UUID,
        claim_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        *,
        employee_payable_account_id: Optional[UUID] = None,
        auto_post: bool = True,
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> ExpensePostingResult:
        """
        Post an approved expense claim to the general ledger.

        Creates a journal entry with:
        - Debit: Expense accounts (from category mappings or item overrides)
        - Credit: Employee payable account

        Args:
            db: Database session
            organization_id: Organization scope
            claim_id: Expense claim to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting the claim
            employee_payable_account_id: Override for employee payable account
            auto_post: If True, automatically post to ledger after journal creation
            idempotency_key: Optional idempotency key for duplicate prevention
            correlation_id: Optional correlation ID for tracing

        Returns:
            ExpensePostingResult with outcome

        Raises:
            HTTPException: If posting fails
        """
        org_id = coerce_uuid(organization_id)
        c_id = coerce_uuid(claim_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load claim with items
        claim = db.get(ExpenseClaim, c_id)
        if not claim or claim.organization_id != org_id:
            return ExpensePostingResult(success=False, message="Expense claim not found")

        if claim.status != ExpenseClaimStatus.APPROVED:
            return ExpensePostingResult(
                success=False,
                message=f"Expense claim must be APPROVED to post (current: {claim.status.value})",
            )

        # Check if already posted
        if claim.journal_entry_id:
            return ExpensePostingResult(
                success=True,
                journal_entry_id=claim.journal_entry_id,
                message="Expense claim already posted (idempotent)",
            )

        # Load items
        items = list(claim.items)
        if not items:
            return ExpensePostingResult(
                success=False, message="Expense claim has no items"
            )

        # Get employee payable account
        payable_account_id = employee_payable_account_id
        if not payable_account_id:
            payable_account_id = ExpensePostingAdapter._get_employee_payable_account(
                db, org_id
            )

        if not payable_account_id:
            return ExpensePostingResult(
                success=False,
                message="No employee payable account configured for organization",
            )

        # Build journal entry lines
        journal_lines: list[JournalLineInput] = []
        total_amount = Decimal("0")

        # Debit lines (expense accounts from categories/overrides)
        for item in items:
            account_id = ExpensePostingAdapter._determine_expense_account(db, org_id, item)
            if not account_id:
                return ExpensePostingResult(
                    success=False,
                    message=f"No expense account for item: {item.description[:50]}",
                )

            # Use approved amount if available, otherwise claimed amount
            amount = item.approved_amount or item.claimed_amount

            journal_lines.append(
                JournalLineInput(
                    account_id=account_id,
                    debit_amount=amount,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=amount,  # Same currency assumed
                    credit_amount_functional=Decimal("0"),
                    description=f"Expense: {item.description[:100]}",
                    cost_center_id=item.cost_center_id or claim.cost_center_id,
                    project_id=claim.project_id,
                )
            )
            total_amount += amount

        # Credit line (Employee payable account)
        journal_lines.append(
            JournalLineInput(
                account_id=payable_account_id,
                debit_amount=Decimal("0"),
                credit_amount=total_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=total_amount,
                description=f"Employee expense payable: {claim.claim_number}",
            )
        )

        # Build employee name for description
        employee_name = "Employee"
        if claim.employee:
            employee_name = f"{claim.employee.first_name} {claim.employee.last_name}"

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=claim.claim_date,
            posting_date=posting_date,
            description=f"Expense Claim {claim.claim_number} - {employee_name}",
            reference=claim.claim_number,
            currency_code=claim.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="EXPENSE",
            source_document_type="EXPENSE_CLAIM",
            source_document_id=c_id,
            correlation_id=correlation_id,
        )

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)

            # Submit and approve automatically for expense posting
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)

        except HTTPException as e:
            return ExpensePostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Update claim with journal reference
        claim.journal_entry_id = journal.journal_entry_id

        # Post to ledger if auto_post enabled
        posting_batch_id = None
        if auto_post:
            if not idempotency_key:
                idempotency_key = f"{org_id}:EXPENSE:{c_id}:post:v1"

            posting_request = PostingRequest(
                organization_id=org_id,
                journal_entry_id=journal.journal_entry_id,
                posting_date=posting_date,
                idempotency_key=idempotency_key,
                source_module="EXPENSE",
                correlation_id=correlation_id,
                posted_by_user_id=user_id,
            )

            try:
                posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

                if not posting_result.success:
                    return ExpensePostingResult(
                        success=False,
                        journal_entry_id=journal.journal_entry_id,
                        message=f"Ledger posting failed: {posting_result.message}",
                    )

                posting_batch_id = posting_result.batch_id

            except Exception as e:
                return ExpensePostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Posting error: {str(e)}",
                )

        db.flush()

        return ExpensePostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_batch_id,
            message="Expense claim posted successfully",
        )

    @staticmethod
    def create_supplier_invoice_from_expense(
        db: Session,
        organization_id: UUID,
        claim_id: UUID,
        created_by_user_id: UUID,
        *,
        supplier_id: Optional[UUID] = None,
    ) -> ExpensePostingResult:
        """
        Create a supplier invoice from an approved expense claim.

        This is used for organizations that want to process employee
        reimbursements through the AP workflow.

        Args:
            db: Database session
            organization_id: Organization scope
            claim_id: Expense claim to convert
            created_by_user_id: User creating the invoice
            supplier_id: Optional supplier to use (defaults to employee-as-supplier)

        Returns:
            ExpensePostingResult with supplier_invoice_id
        """
        org_id = coerce_uuid(organization_id)
        c_id = coerce_uuid(claim_id)
        user_id = coerce_uuid(created_by_user_id)

        # Load claim
        claim = db.get(ExpenseClaim, c_id)
        if not claim or claim.organization_id != org_id:
            return ExpensePostingResult(success=False, message="Expense claim not found")

        if claim.status != ExpenseClaimStatus.APPROVED:
            return ExpensePostingResult(
                success=False,
                message=f"Expense claim must be APPROVED (current: {claim.status.value})",
            )

        # Check if already has invoice
        if claim.supplier_invoice_id:
            return ExpensePostingResult(
                success=True,
                supplier_invoice_id=claim.supplier_invoice_id,
                message="Supplier invoice already exists (idempotent)",
            )

        # Get or create supplier for employee
        supplier = None
        if supplier_id:
            supplier = db.get(Supplier, supplier_id)

        if not supplier:
            # Try to find/create supplier for employee
            supplier = ExpensePostingAdapter._get_or_create_employee_supplier(
                db, org_id, claim.employee, user_id
            )

        if not supplier:
            return ExpensePostingResult(
                success=False,
                message="Could not determine supplier for expense claim",
            )

        # Generate invoice number
        from sqlalchemy import func, select
        count = db.scalar(
            select(func.count(SupplierInvoice.invoice_id)).where(
                SupplierInvoice.organization_id == org_id
            )
        ) or 0
        invoice_number = f"EXP-INV-{date.today().year}-{count + 1:05d}"

        # Create supplier invoice
        invoice = SupplierInvoice(
            organization_id=org_id,
            supplier_id=supplier.supplier_id,
            invoice_number=invoice_number,
            supplier_invoice_number=claim.claim_number,
            invoice_type=SupplierInvoiceType.STANDARD,
            invoice_date=claim.claim_date,
            due_date=claim.claim_date,  # Immediate payment expected
            currency_code=claim.currency_code,
            exchange_rate=Decimal("1.0"),
            subtotal_amount=claim.total_approved_amount or claim.total_claimed_amount,
            tax_amount=Decimal("0"),
            total_amount=claim.total_approved_amount or claim.total_claimed_amount,
            functional_currency_amount=claim.total_approved_amount or claim.total_claimed_amount,
            amount_paid=Decimal("0"),
            amount_due=claim.total_approved_amount or claim.total_claimed_amount,
            status=SupplierInvoiceStatus.DRAFT,
            ap_control_account_id=supplier.ap_control_account_id,
            description=f"Expense Reimbursement: {claim.purpose}",
            internal_notes=f"Created from expense claim {claim.claim_number}",
            created_by_user_id=user_id,
            correlation_id=str(uuid_lib.uuid4()),
        )
        db.add(invoice)
        db.flush()

        # Create invoice lines from claim items
        for idx, item in enumerate(claim.items, start=1):
            account_id = ExpensePostingAdapter._determine_expense_account(db, org_id, item)

            line = SupplierInvoiceLine(
                organization_id=org_id,
                invoice_id=invoice.invoice_id,
                line_number=idx,
                description=item.description,
                quantity=Decimal("1"),
                unit_price=item.approved_amount or item.claimed_amount,
                line_amount=item.approved_amount or item.claimed_amount,
                tax_amount=Decimal("0"),
                expense_account_id=account_id,
                cost_center_id=item.cost_center_id or claim.cost_center_id,
                project_id=claim.project_id,
            )
            db.add(line)

        # Link claim to invoice
        claim.supplier_invoice_id = invoice.invoice_id

        db.flush()

        return ExpensePostingResult(
            success=True,
            supplier_invoice_id=invoice.invoice_id,
            message="Supplier invoice created from expense claim",
        )

    @staticmethod
    def post_cash_advance(
        db: Session,
        organization_id: UUID,
        advance_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        *,
        bank_account_id: UUID,
        auto_post: bool = True,
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> ExpensePostingResult:
        """
        Post a disbursed cash advance to the general ledger.

        Creates a journal entry with:
        - Debit: Employee advance account (asset - money owed by employee)
        - Credit: Bank/Cash account (payment made)

        Args:
            db: Database session
            organization_id: Organization scope
            advance_id: Cash advance to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            bank_account_id: Bank/Cash account for credit
            auto_post: If True, automatically post to ledger
            idempotency_key: Optional idempotency key
            correlation_id: Optional correlation ID

        Returns:
            ExpensePostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        adv_id = coerce_uuid(advance_id)
        user_id = coerce_uuid(posted_by_user_id)
        bank_id = coerce_uuid(bank_account_id)

        # Load advance
        advance = db.get(CashAdvance, adv_id)
        if not advance or advance.organization_id != org_id:
            return ExpensePostingResult(success=False, message="Cash advance not found")

        if advance.status != CashAdvanceStatus.DISBURSED:
            return ExpensePostingResult(
                success=False,
                message=f"Cash advance must be DISBURSED to post (current: {advance.status.value})",
            )

        # Check if already posted
        if advance.journal_entry_id:
            return ExpensePostingResult(
                success=True,
                journal_entry_id=advance.journal_entry_id,
                message="Cash advance already posted (idempotent)",
            )

        # Get advance account
        advance_account_id = advance.advance_account_id
        if not advance_account_id:
            advance_account_id = ExpensePostingAdapter._get_advance_account(db, org_id)

        if not advance_account_id:
            return ExpensePostingResult(
                success=False,
                message="No advance account configured for cash advances",
            )

        amount = advance.approved_amount or advance.requested_amount

        # Build employee name for description
        employee_name = "Employee"
        if advance.employee:
            employee_name = f"{advance.employee.first_name} {advance.employee.last_name}"

        # Build journal lines
        journal_lines = [
            # Debit: Employee advance (asset)
            JournalLineInput(
                account_id=advance_account_id,
                debit_amount=amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=amount,
                credit_amount_functional=Decimal("0"),
                description=f"Cash advance to {employee_name}",
                cost_center_id=advance.cost_center_id,
            ),
            # Credit: Bank/Cash
            JournalLineInput(
                account_id=bank_id,
                debit_amount=Decimal("0"),
                credit_amount=amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=amount,
                description=f"Cash advance disbursement: {advance.advance_number}",
            ),
        ]

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=advance.disbursed_on or advance.request_date,
            posting_date=posting_date,
            description=f"Cash Advance {advance.advance_number} - {employee_name}",
            reference=advance.advance_number,
            currency_code=advance.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="EXPENSE",
            source_document_type="CASH_ADVANCE",
            source_document_id=adv_id,
            correlation_id=correlation_id,
        )

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)
        except HTTPException as e:
            return ExpensePostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Update advance with journal reference
        advance.journal_entry_id = journal.journal_entry_id

        # Post to ledger if auto_post enabled
        posting_batch_id = None
        if auto_post:
            if not idempotency_key:
                idempotency_key = f"{org_id}:ADVANCE:{adv_id}:post:v1"

            posting_request = PostingRequest(
                organization_id=org_id,
                journal_entry_id=journal.journal_entry_id,
                posting_date=posting_date,
                idempotency_key=idempotency_key,
                source_module="EXPENSE",
                correlation_id=correlation_id,
                posted_by_user_id=user_id,
            )

            try:
                posting_result = LedgerPostingService.post_journal_entry(db, posting_request)
                if not posting_result.success:
                    return ExpensePostingResult(
                        success=False,
                        journal_entry_id=journal.journal_entry_id,
                        message=f"Ledger posting failed: {posting_result.message}",
                    )
                posting_batch_id = posting_result.batch_id
            except Exception as e:
                return ExpensePostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Posting error: {str(e)}",
                )

        db.flush()

        return ExpensePostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_batch_id,
            message="Cash advance posted successfully",
        )

    @staticmethod
    def settle_cash_advance(
        db: Session,
        organization_id: UUID,
        advance_id: UUID,
        claim_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        *,
        settlement_amount: Optional[Decimal] = None,
        auto_post: bool = True,
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> ExpensePostingResult:
        """
        Settle a cash advance against an expense claim.

        Creates a journal entry to offset the advance against expenses:
        - Debit: Expense accounts (from claim items)
        - Credit: Employee advance account (reduce receivable)

        If the expense exceeds the advance, the difference goes to employee payable.
        If the advance exceeds the expense, a refund entry may be needed separately.

        Args:
            db: Database session
            organization_id: Organization scope
            advance_id: Cash advance to settle
            claim_id: Expense claim for settlement
            posting_date: Date for GL posting
            posted_by_user_id: User posting
            settlement_amount: Amount to settle (defaults to full claim amount)
            auto_post: If True, automatically post to ledger
            idempotency_key: Optional idempotency key
            correlation_id: Optional correlation ID

        Returns:
            ExpensePostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        adv_id = coerce_uuid(advance_id)
        c_id = coerce_uuid(claim_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load advance and claim
        advance = db.get(CashAdvance, adv_id)
        if not advance or advance.organization_id != org_id:
            return ExpensePostingResult(success=False, message="Cash advance not found")

        claim = db.get(ExpenseClaim, c_id)
        if not claim or claim.organization_id != org_id:
            return ExpensePostingResult(success=False, message="Expense claim not found")

        if advance.status not in {CashAdvanceStatus.DISBURSED, CashAdvanceStatus.PARTIALLY_SETTLED}:
            return ExpensePostingResult(
                success=False,
                message=f"Cash advance must be DISBURSED or PARTIALLY_SETTLED (current: {advance.status.value})",
            )

        if claim.status != ExpenseClaimStatus.APPROVED:
            return ExpensePostingResult(
                success=False,
                message=f"Expense claim must be APPROVED (current: {claim.status.value})",
            )

        # Calculate settlement amounts
        claim_amount = claim.total_approved_amount or claim.total_claimed_amount
        outstanding_advance = advance.outstanding_balance
        settle_amount = settlement_amount or min(claim_amount, outstanding_advance)

        if settle_amount <= Decimal("0"):
            return ExpensePostingResult(
                success=False,
                message="Nothing to settle - advance may already be fully settled",
            )

        # Get advance account
        advance_account_id = advance.advance_account_id
        if not advance_account_id:
            advance_account_id = ExpensePostingAdapter._get_advance_account(db, org_id)

        if not advance_account_id:
            return ExpensePostingResult(
                success=False,
                message="No advance account configured",
            )

        # Build employee name
        employee_name = "Employee"
        if claim.employee:
            employee_name = f"{claim.employee.first_name} {claim.employee.last_name}"

        # Build journal lines
        journal_lines = []

        # Debit expense accounts (proportionally distributed)
        remaining_to_allocate = settle_amount
        for item in claim.items:
            if remaining_to_allocate <= Decimal("0"):
                break

            item_amount = item.approved_amount or item.claimed_amount
            allocate_amount = min(item_amount, remaining_to_allocate)

            account_id = ExpensePostingAdapter._determine_expense_account(db, org_id, item)
            if account_id:
                journal_lines.append(
                    JournalLineInput(
                        account_id=account_id,
                        debit_amount=allocate_amount,
                        credit_amount=Decimal("0"),
                        debit_amount_functional=allocate_amount,
                        credit_amount_functional=Decimal("0"),
                        description=f"Expense (advance settlement): {item.description[:80]}",
                        cost_center_id=item.cost_center_id or claim.cost_center_id,
                        project_id=claim.project_id,
                    )
                )
                remaining_to_allocate -= allocate_amount

        # Credit advance account (reduce receivable)
        journal_lines.append(
            JournalLineInput(
                account_id=advance_account_id,
                debit_amount=Decimal("0"),
                credit_amount=settle_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=settle_amount,
                description=f"Advance settlement: {advance.advance_number}",
            )
        )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=claim.claim_date,
            posting_date=posting_date,
            description=f"Advance Settlement {advance.advance_number} via {claim.claim_number}",
            reference=f"{advance.advance_number}/{claim.claim_number}",
            currency_code=claim.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="EXPENSE",
            source_document_type="ADVANCE_SETTLEMENT",
            source_document_id=adv_id,
            correlation_id=correlation_id,
        )

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)
        except HTTPException as e:
            return ExpensePostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Update advance settlement amount
        advance.amount_settled += settle_amount
        if advance.is_fully_settled:
            advance.status = CashAdvanceStatus.FULLY_SETTLED
            advance.settled_on = posting_date
        else:
            advance.status = CashAdvanceStatus.PARTIALLY_SETTLED

        # Update claim advance adjustment
        claim.advance_adjusted = settle_amount
        claim.cash_advance_id = advance.advance_id
        claim.net_payable_amount = claim_amount - settle_amount

        # Post to ledger if auto_post enabled
        posting_batch_id = None
        if auto_post:
            if not idempotency_key:
                idempotency_key = f"{org_id}:SETTLE:{adv_id}:{c_id}:v1"

            posting_request = PostingRequest(
                organization_id=org_id,
                journal_entry_id=journal.journal_entry_id,
                posting_date=posting_date,
                idempotency_key=idempotency_key,
                source_module="EXPENSE",
                correlation_id=correlation_id,
                posted_by_user_id=user_id,
            )

            try:
                posting_result = LedgerPostingService.post_journal_entry(db, posting_request)
                if not posting_result.success:
                    return ExpensePostingResult(
                        success=False,
                        journal_entry_id=journal.journal_entry_id,
                        message=f"Ledger posting failed: {posting_result.message}",
                    )
                posting_batch_id = posting_result.batch_id
            except Exception as e:
                return ExpensePostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Posting error: {str(e)}",
                )

        db.flush()

        return ExpensePostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_batch_id,
            message=f"Advance settlement posted: {settle_amount}",
        )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    @staticmethod
    def _determine_expense_account(
        db: Session,
        organization_id: UUID,
        item: ExpenseClaimItem,
    ) -> Optional[UUID]:
        """
        Determine the expense account for an expense claim item.

        Priority:
        1. Item-level override (expense_account_id on item)
        2. Category default (expense_account_id on category)
        3. Organization default expense account
        """
        # Priority 1: Item-level override
        if item.expense_account_id:
            return item.expense_account_id

        # Priority 2: Category default
        if item.category_id:
            category = db.get(ExpenseCategory, item.category_id)
            if category and category.expense_account_id:
                return category.expense_account_id

        # Priority 3: Organization default (from settings)
        from app.models.finance.core_org.organization import Organization
        org = db.get(Organization, organization_id)
        if org and hasattr(org, 'default_expense_account_id'):
            return getattr(org, 'default_expense_account_id', None)

        return None

    @staticmethod
    def _get_employee_payable_account(
        db: Session,
        organization_id: UUID,
    ) -> Optional[UUID]:
        """Get the employee payable account for the organization."""
        from app.models.finance.gl.account import Account
        from sqlalchemy import select

        # Try organization settings first
        from app.models.finance.core_org.organization import Organization
        org = db.get(Organization, organization_id)
        if org and hasattr(org, 'employee_payable_account_id'):
            acc_id = getattr(org, 'employee_payable_account_id', None)
            if acc_id:
                return acc_id

        # Fall back to finding by code pattern
        account = db.scalar(
            select(Account).where(
                Account.organization_id == organization_id,
                Account.account_code.like("211%"),  # Common pattern for accrued expenses
                Account.is_active == True,
            ).order_by(Account.account_code).limit(1)
        )
        return account.account_id if account else None

    @staticmethod
    def _get_advance_account(
        db: Session,
        organization_id: UUID,
    ) -> Optional[UUID]:
        """Get the employee advance account for the organization."""
        from app.models.finance.gl.account import Account
        from sqlalchemy import select

        # Try organization settings first
        from app.models.finance.core_org.organization import Organization
        org = db.get(Organization, organization_id)
        if org and hasattr(org, 'employee_advance_account_id'):
            acc_id = getattr(org, 'employee_advance_account_id', None)
            if acc_id:
                return acc_id

        # Fall back to finding by name/code pattern
        account = db.scalar(
            select(Account).where(
                Account.organization_id == organization_id,
                Account.account_name.ilike("%advance%"),
                Account.is_active == True,
            ).order_by(Account.account_code).limit(1)
        )
        return account.account_id if account else None

    @staticmethod
    def _get_or_create_employee_supplier(
        db: Session,
        organization_id: UUID,
        employee,
        user_id: UUID,
    ) -> Optional[Supplier]:
        """
        Get or create a supplier record for an employee.

        Used when processing expense reimbursements through AP.
        """
        if not employee:
            return None

        from sqlalchemy import select

        # Look for existing supplier linked to employee
        supplier = db.scalar(
            select(Supplier).where(
                Supplier.organization_id == organization_id,
                Supplier.employee_id == employee.employee_id,
            )
        )
        if supplier:
            return supplier

        # Look by email match
        if employee.work_email:
            supplier = db.scalar(
                select(Supplier).where(
                    Supplier.organization_id == organization_id,
                    Supplier.email == employee.work_email,
                )
            )
            if supplier:
                return supplier

        # Create new supplier for employee
        supplier = Supplier(
            organization_id=organization_id,
            supplier_code=f"EMP-{employee.employee_id.hex[:8].upper()}",
            legal_name=f"{employee.first_name} {employee.last_name}",
            trading_name=f"{employee.first_name} {employee.last_name}",
            email=employee.work_email,
            supplier_type="INDIVIDUAL",
            is_employee=True,
            employee_id=employee.employee_id,
            currency_code="NGN",
            is_active=True,
            created_by_user_id=user_id,
        )
        db.add(supplier)
        db.flush()

        return supplier


# Module-level singleton instance
expense_posting_adapter = ExpensePostingAdapter()
