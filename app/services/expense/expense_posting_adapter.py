"""
ExpensePostingAdapter - Converts expense claims to GL entries.

Transforms approved expense claims into supplier invoices and journal entries,
posting them to the general ledger for AP processing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.expense.cash_advance import CashAdvance, CashAdvanceStatus
from app.models.expense.expense_claim import (
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)
from app.models.expense.expense_claim_action import (
    ExpenseClaimAction,
    ExpenseClaimActionStatus,
    ExpenseClaimActionType,
)
from app.models.finance.ap.supplier import Supplier, SupplierType
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.gl.journal_entry import JournalType
from app.services.common import coerce_uuid
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
)
from app.services.finance.platform.org_context import org_context_service
from app.services.finance.posting.base import BasePostingAdapter, PostingResult

logger = logging.getLogger(__name__)


@dataclass
class ExpensePostingResult(PostingResult):
    """Result of an expense posting operation."""

    supplier_invoice_id: UUID | None = None


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
    DEFAULT_EMPLOYEE_PAYABLE_ACCOUNT_CODE = (
        "2110"  # Current Liabilities - Accrued Expenses
    )

    @staticmethod
    def _action_key(claim_id: UUID, action: ExpenseClaimActionType) -> str:
        return f"EXPENSE:{claim_id}:{action.value}:v1"

    @staticmethod
    def _next_invoice_number(db: Session, org_id: UUID) -> str:
        """Generate next expense invoice number.

        Delegates to SyncNumberingService for race-condition-safe generation.
        """
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        return SyncNumberingService(db).generate_next_number(
            org_id, SequenceType.EXPENSE_INVOICE
        )

    @staticmethod
    def _try_record_action(
        db: Session,
        org_id: UUID,
        claim_id: UUID,
        action: ExpenseClaimActionType,
    ) -> bool:
        action_key = ExpensePostingAdapter._action_key(claim_id, action)
        stmt = (
            insert(ExpenseClaimAction)
            .values(
                organization_id=org_id,
                claim_id=claim_id,
                action_type=action,
                action_key=action_key,
                status=ExpenseClaimActionStatus.STARTED,
            )
            .on_conflict_do_nothing(
                index_elements=["organization_id", "claim_id", "action_type"],
            )
            .returning(ExpenseClaimAction.action_id)
        )
        result = db.execute(stmt)
        inserted_action_id = result.scalar_one_or_none()
        db.flush()
        if inserted_action_id is not None:
            return True

        existing = db.scalar(
            select(ExpenseClaimAction).where(
                ExpenseClaimAction.organization_id == org_id,
                ExpenseClaimAction.claim_id == claim_id,
                ExpenseClaimAction.action_type == action,
            )
        )
        if not existing:
            return False
        if existing.status == ExpenseClaimActionStatus.FAILED:
            existing.status = ExpenseClaimActionStatus.STARTED
            db.flush()
            return True
        return False

    @staticmethod
    def _set_action_status(
        db: Session,
        org_id: UUID,
        claim_id: UUID,
        action: ExpenseClaimActionType,
        status: ExpenseClaimActionStatus,
    ) -> None:
        record = db.scalar(
            select(ExpenseClaimAction).where(
                ExpenseClaimAction.organization_id == org_id,
                ExpenseClaimAction.claim_id == claim_id,
                ExpenseClaimAction.action_type == action,
            )
        )
        if record:
            record.status = status
            db.flush()

    @staticmethod
    def post_expense_claim(
        db: Session,
        organization_id: UUID,
        claim_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        *,
        employee_payable_account_id: UUID | None = None,
        auto_post: bool = True,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
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
            return ExpensePostingResult(
                success=False, message="Expense claim not found"
            )

        # Allow posting for APPROVED (normal workflow) and for claims that are
        # already in a posted state but missing GL entries (sync/import backfill).
        postable_statuses = ExpenseClaimStatus.gl_impacting()
        if claim.status not in postable_statuses:
            return ExpensePostingResult(
                success=False,
                message=f"Expense claim must be APPROVED or PAID to post (current: {claim.status.value})",
            )

        # Skip zero-amount claims — nothing meaningful to post to GL
        if claim.total_approved_amount == Decimal("0"):
            return ExpensePostingResult(
                success=True,
                message="Zero amount expense — no GL posting needed",
            )

        # Check if already posted
        if claim.journal_entry_id:
            return ExpensePostingResult(
                success=True,
                journal_entry_id=claim.journal_entry_id,
                message="Expense claim already posted (idempotent)",
            )

        action_started = ExpensePostingAdapter._try_record_action(
            db,
            org_id,
            c_id,
            ExpenseClaimActionType.POST_GL,
        )
        if not action_started:
            return ExpensePostingResult(
                success=True,
                journal_entry_id=claim.journal_entry_id,
                message="Expense claim posting already initiated (idempotent)",
            )

        # Load items
        items = list(claim.items)
        if not items:
            ExpensePostingAdapter._set_action_status(
                db,
                org_id,
                c_id,
                ExpenseClaimActionType.POST_GL,
                ExpenseClaimActionStatus.FAILED,
            )
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
            ExpensePostingAdapter._set_action_status(
                db,
                org_id,
                c_id,
                ExpenseClaimActionType.POST_GL,
                ExpenseClaimActionStatus.FAILED,
            )
            return ExpensePostingResult(
                success=False,
                message="No employee payable account configured for organization",
            )

        # Build journal entry lines
        journal_lines: list[JournalLineInput] = []
        total_amount = Decimal("0")

        # Debit lines (expense accounts from categories/overrides)
        for item in items:
            account_id = ExpensePostingAdapter._determine_expense_account(
                db, org_id, item
            )
            if not account_id:
                ExpensePostingAdapter._set_action_status(
                    db,
                    org_id,
                    c_id,
                    ExpenseClaimActionType.POST_GL,
                    ExpenseClaimActionStatus.FAILED,
                )
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
            employee_name = claim.employee.full_name

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

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            ExpensePostingAdapter._set_action_status(
                db,
                org_id,
                c_id,
                ExpenseClaimActionType.POST_GL,
                ExpenseClaimActionStatus.FAILED,
            )
            return ExpensePostingResult(success=False, message=error.message)

        # Update claim with journal reference
        claim.journal_entry_id = journal.journal_entry_id

        # Post to ledger if auto_post enabled
        posting_batch_id = None
        if auto_post:
            if not idempotency_key:
                idempotency_key = ExpensePostingAdapter._action_key(
                    c_id, ExpenseClaimActionType.POST_GL
                )

            posting_result = BasePostingAdapter.post_to_ledger(
                db,
                organization_id=org_id,
                journal_entry_id=journal.journal_entry_id,
                posting_date=posting_date,
                idempotency_key=idempotency_key,
                source_module="EXPENSE",
                correlation_id=correlation_id,
                posted_by_user_id=user_id,
                success_message="Expense claim posted successfully",
            )
            if not posting_result.success:
                ExpensePostingAdapter._set_action_status(
                    db,
                    org_id,
                    c_id,
                    ExpenseClaimActionType.POST_GL,
                    ExpenseClaimActionStatus.FAILED,
                )
                return ExpensePostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=posting_result.message,
                )

            posting_batch_id = posting_result.posting_batch_id

        db.flush()

        ExpensePostingAdapter._set_action_status(
            db,
            org_id,
            c_id,
            ExpenseClaimActionType.POST_GL,
            ExpenseClaimActionStatus.COMPLETED,
        )
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
        supplier_id: UUID | None = None,
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

        action_started = False
        try:
            # Load claim
            claim = db.get(ExpenseClaim, c_id)
            if not claim or claim.organization_id != org_id:
                return ExpensePostingResult(
                    success=False, message="Expense claim not found"
                )

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

            action_started = ExpensePostingAdapter._try_record_action(
                db,
                org_id,
                c_id,
                ExpenseClaimActionType.CREATE_SUPPLIER_INVOICE,
            )
            if not action_started:
                return ExpensePostingResult(
                    success=True,
                    supplier_invoice_id=claim.supplier_invoice_id,
                    message="Supplier invoice creation already initiated (idempotent)",
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
                ExpensePostingAdapter._set_action_status(
                    db,
                    org_id,
                    c_id,
                    ExpenseClaimActionType.CREATE_SUPPLIER_INVOICE,
                    ExpenseClaimActionStatus.FAILED,
                )
                return ExpensePostingResult(
                    success=False,
                    message="Could not determine supplier for expense claim",
                )

            # Generate invoice number
            invoice_number = ExpensePostingAdapter._next_invoice_number(db, org_id)

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
                subtotal_amount=claim.total_approved_amount
                or claim.total_claimed_amount,
                tax_amount=Decimal("0"),
                total_amount=claim.total_approved_amount or claim.total_claimed_amount,
                functional_currency_amount=claim.total_approved_amount
                or claim.total_claimed_amount,
                amount_paid=Decimal("0"),
                amount_due=claim.total_approved_amount or claim.total_claimed_amount,
                status=SupplierInvoiceStatus.DRAFT,
                ap_control_account_id=supplier.ap_control_account_id,
                description=f"Expense Reimbursement: {claim.purpose}",
                internal_notes=f"Created from expense claim {claim.claim_number}",
                created_by_user_id=user_id,
                correlation_id=ExpensePostingAdapter._action_key(
                    c_id, ExpenseClaimActionType.CREATE_SUPPLIER_INVOICE
                ),
            )
            db.add(invoice)
            db.flush()

            # Create invoice lines from claim items
            for idx, item in enumerate(claim.items, start=1):
                account_id = ExpensePostingAdapter._determine_expense_account(
                    db, org_id, item
                )

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

            ExpensePostingAdapter._set_action_status(
                db,
                org_id,
                c_id,
                ExpenseClaimActionType.CREATE_SUPPLIER_INVOICE,
                ExpenseClaimActionStatus.COMPLETED,
            )
            return ExpensePostingResult(
                success=True,
                supplier_invoice_id=invoice.invoice_id,
                message="Supplier invoice created from expense claim",
            )
        except Exception:
            if action_started:
                ExpensePostingAdapter._set_action_status(
                    db,
                    org_id,
                    c_id,
                    ExpenseClaimActionType.CREATE_SUPPLIER_INVOICE,
                    ExpenseClaimActionStatus.FAILED,
                )
            raise

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
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
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
            employee_name = advance.employee.full_name

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

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return ExpensePostingResult(success=False, message=error.message)

        # Update advance with journal reference
        advance.journal_entry_id = journal.journal_entry_id

        # Post to ledger if auto_post enabled
        posting_batch_id = None
        if auto_post:
            if not idempotency_key:
                idempotency_key = f"{org_id}:ADVANCE:{adv_id}:post:v1"

            posting_result = BasePostingAdapter.post_to_ledger(
                db,
                organization_id=org_id,
                journal_entry_id=journal.journal_entry_id,
                posting_date=posting_date,
                idempotency_key=idempotency_key,
                source_module="EXPENSE",
                correlation_id=correlation_id,
                posted_by_user_id=user_id,
                success_message="Cash advance posted successfully",
            )
            if not posting_result.success:
                return ExpensePostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=posting_result.message,
                )
            posting_batch_id = posting_result.posting_batch_id

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
        settlement_amount: Decimal | None = None,
        auto_post: bool = True,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
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
            return ExpensePostingResult(
                success=False, message="Expense claim not found"
            )

        if advance.status not in {
            CashAdvanceStatus.DISBURSED,
            CashAdvanceStatus.PARTIALLY_SETTLED,
        }:
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
        if claim.employee:
            pass

        # Build journal lines
        journal_lines = []

        # Debit expense accounts (proportionally distributed)
        remaining_to_allocate = settle_amount
        for item in claim.items:
            if remaining_to_allocate <= Decimal("0"):
                break

            item_amount = item.approved_amount or item.claimed_amount
            allocate_amount = min(item_amount, remaining_to_allocate)

            account_id = ExpensePostingAdapter._determine_expense_account(
                db, org_id, item
            )
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

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return ExpensePostingResult(success=False, message=error.message)

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

            posting_result = BasePostingAdapter.post_to_ledger(
                db,
                organization_id=org_id,
                journal_entry_id=journal.journal_entry_id,
                posting_date=posting_date,
                idempotency_key=idempotency_key,
                source_module="EXPENSE",
                correlation_id=correlation_id,
                posted_by_user_id=user_id,
                success_message=f"Advance settlement posted: {settle_amount}",
            )
            if not posting_result.success:
                return ExpensePostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=posting_result.message,
                )
            posting_batch_id = posting_result.posting_batch_id

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
    ) -> UUID | None:
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
        if org and hasattr(org, "default_expense_account_id"):
            return getattr(org, "default_expense_account_id", None)

        return None

    @staticmethod
    def _get_employee_payable_account(
        db: Session,
        organization_id: UUID,
    ) -> UUID | None:
        """Get the employee payable account for the organization."""

        # Try organization settings first
        from app.models.finance.core_org.organization import Organization
        from app.models.finance.gl.account import Account

        org = db.get(Organization, organization_id)
        if org:
            acc_id: UUID | None = getattr(org, "employee_payable_account_id", None)
            if not acc_id:
                # Backward-compatible org setting name used in tests/config seeds.
                acc_id = getattr(org, "salary_payable_account_id", None)
            if acc_id:
                return acc_id

        # Fall back to finding by code pattern
        account = db.scalar(
            select(Account)
            .where(
                Account.organization_id == organization_id,
                Account.account_code.like(
                    "211%"
                ),  # Common pattern for accrued expenses
                Account.is_active == True,
            )
            .order_by(Account.account_code)
            .limit(1)
        )
        return account.account_id if account else None

    @staticmethod
    def _get_advance_account(
        db: Session,
        organization_id: UUID,
    ) -> UUID | None:
        """Get the employee advance account for the organization."""

        # Try organization settings first
        from app.models.finance.core_org.organization import Organization
        from app.models.finance.gl.account import Account

        org = db.get(Organization, organization_id)
        if org and hasattr(org, "employee_advance_account_id"):
            acc_id: UUID | None = getattr(org, "employee_advance_account_id", None)
            if acc_id:
                return acc_id

        # Fall back to finding by name/code pattern
        account = db.scalar(
            select(Account)
            .where(
                Account.organization_id == organization_id,
                Account.account_name.ilike("%advance%"),
                Account.is_active == True,
            )
            .order_by(Account.account_code)
            .limit(1)
        )
        return account.account_id if account else None

    @staticmethod
    def _get_or_create_employee_supplier(
        db: Session,
        organization_id: UUID,
        employee,
        user_id: UUID,
    ) -> Supplier | None:
        """
        Get or create a supplier record for an employee.

        Used when processing expense reimbursements through AP.
        """
        if not employee:
            return None

        supplier_code = f"EMP-{employee.employee_id.hex[:8].upper()}"

        # Look for existing supplier linked to employee
        supplier = db.scalar(
            select(Supplier).where(
                Supplier.organization_id == organization_id,
                Supplier.supplier_code == supplier_code,
            )
        )
        if supplier:
            return supplier

        payable_account_id = ExpensePostingAdapter._get_employee_payable_account(
            db, organization_id
        )
        if not payable_account_id:
            return None
        resolved_currency_code = org_context_service.get_functional_currency(
            db, organization_id
        )

        # Create new supplier for employee
        supplier = Supplier(
            organization_id=organization_id,
            supplier_code=supplier_code,
            supplier_type=SupplierType.CONTRACTOR,
            legal_name=employee.full_name,
            trading_name=employee.full_name,
            ap_control_account_id=payable_account_id,
            primary_contact={"email": employee.work_email}
            if employee.work_email
            else None,
            currency_code=resolved_currency_code,
            is_active=True,
            created_by_user_id=user_id,
        )
        db.add(supplier)
        db.flush()

        return supplier

    @staticmethod
    def post_expense_reimbursement(
        db: Session,
        organization_id: UUID,
        claim_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        *,
        bank_account_id: UUID,
        employee_payable_account_id: UUID | None = None,
        payment_reference: str | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> ExpensePostingResult:
        """
        Post expense reimbursement (payment) to the general ledger.

        This is called when an expense claim is paid via Paystack transfer or
        other payment method. It clears the employee payable.

        GL Entry Structure:
        - DEBIT: Employee payable account (clear the liability)
        - CREDIT: Bank account (record the cash outflow)

        Args:
            db: Database session
            organization_id: Organization scope
            claim_id: The expense claim being paid
            posting_date: Date for the posting
            posted_by_user_id: User performing the posting
            bank_account_id: Bank account used for payment (source of funds)
            employee_payable_account_id: Optional override for payable account
            payment_reference: Optional payment reference (e.g., Paystack transfer code)
            idempotency_key: Key for preventing duplicate postings
            correlation_id: Tracing correlation ID

        Returns:
            ExpensePostingResult with journal entry details
        """
        from app.models.finance.banking.bank_account import BankAccount

        org_id = coerce_uuid(organization_id)
        c_id = coerce_uuid(claim_id)
        user_id = coerce_uuid(posted_by_user_id)
        bank_id = coerce_uuid(bank_account_id)

        # Load expense claim
        claim = db.get(ExpenseClaim, c_id)
        if not claim or claim.organization_id != org_id:
            return ExpensePostingResult(
                success=False, message="Expense claim not found"
            )

        # Validate claim is in PAID status
        if claim.status != ExpenseClaimStatus.PAID:
            return ExpensePostingResult(
                success=False,
                message=f"Cannot post reimbursement for claim in '{claim.status.value}' status",
            )

        # Get bank account and its GL account
        bank_account = db.get(BankAccount, bank_id)
        if not bank_account or bank_account.organization_id != org_id:
            return ExpensePostingResult(success=False, message="Bank account not found")

        if not bank_account.gl_account_id:
            return ExpensePostingResult(
                success=False,
                message="Bank account has no linked GL account",
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
                message="Employee payable account not configured",
            )
        resolved_currency_code = (
            claim.currency_code
            or bank_account.currency_code
            or (org_context_service.get_functional_currency(db, org_id))
        )

        # Build journal lines
        reimbursement_amount = claim.net_payable_amount or Decimal("0")
        if reimbursement_amount <= Decimal("0"):
            return ExpensePostingResult(
                success=False,
                message="No reimbursement amount to post",
            )

        journal_lines = [
            # Debit: Employee Payable (clear liability)
            JournalLineInput(
                account_id=payable_account_id,
                debit_amount=reimbursement_amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=reimbursement_amount,
                credit_amount_functional=Decimal("0"),
                description=f"Expense reimbursement: {claim.claim_number}",
            ),
            # Credit: Bank Account (cash outflow)
            JournalLineInput(
                account_id=bank_account.gl_account_id,
                debit_amount=Decimal("0"),
                credit_amount=reimbursement_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=reimbursement_amount,
                description=f"Transfer to employee: {claim.claim_number}",
            ),
        ]

        # Create journal entry
        reference = payment_reference or claim.payment_reference or claim.claim_number
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=claim.paid_on or posting_date,
            posting_date=posting_date,
            description=f"Expense Reimbursement {claim.claim_number}",
            reference=reference,
            currency_code=resolved_currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="EXPENSE",
            source_document_type="EXPENSE_REIMBURSEMENT",
            source_document_id=c_id,
            correlation_id=correlation_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return ExpensePostingResult(success=False, message=error.message)

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:EXP:REIMB:{c_id}:post:v1"

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="EXPENSE",
            correlation_id=correlation_id,
            posted_by_user_id=user_id,
            success_message="Expense reimbursement posted successfully",
        )
        if not posting_result.success:
            return ExpensePostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        # Update claim with reimbursement journal reference
        claim.reimbursement_journal_id = journal.journal_entry_id

        return ExpensePostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )

    @staticmethod
    def post_transfer_fee(
        db: Session,
        organization_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        *,
        fee_amount: Decimal,
        bank_account_id: UUID,
        fee_expense_account_id: UUID,
        reference: str,
        description: str,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> ExpensePostingResult:
        """
        Post transfer fee (bank charge) to the general ledger.

        GL Entry Structure:
        - DEBIT: Bank charges expense account (expense)
        - CREDIT: Bank account (where fee was deducted from)

        Args:
            db: Database session
            organization_id: Organization scope
            posting_date: Date for the posting
            posted_by_user_id: User performing the posting
            fee_amount: Fee amount in currency units (not kobo)
            bank_account_id: Bank account the fee was deducted from
            fee_expense_account_id: GL expense account for bank charges
            reference: Payment/transfer reference
            description: Description for the journal entry
            idempotency_key: Key for preventing duplicate postings
            correlation_id: Tracing correlation ID

        Returns:
            ExpensePostingResult with journal entry details
        """
        from app.models.finance.banking.bank_account import BankAccount

        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(posted_by_user_id)
        bank_id = coerce_uuid(bank_account_id)
        expense_account_id = coerce_uuid(fee_expense_account_id)

        if fee_amount <= Decimal("0"):
            return ExpensePostingResult(success=False, message="No fee amount to post")

        # Get bank account and its GL account
        bank_account = db.get(BankAccount, bank_id)
        if not bank_account or bank_account.organization_id != org_id:
            return ExpensePostingResult(success=False, message="Bank account not found")

        if not bank_account.gl_account_id:
            return ExpensePostingResult(
                success=False,
                message="Bank account has no linked GL account",
            )
        resolved_currency_code = bank_account.currency_code or (
            org_context_service.get_functional_currency(db, org_id)
        )

        # Build journal lines
        journal_lines = [
            # Debit: Bank Charges Expense
            JournalLineInput(
                account_id=expense_account_id,
                debit_amount=fee_amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=fee_amount,
                credit_amount_functional=Decimal("0"),
                description=f"Transfer fee: {reference}",
            ),
            # Credit: Bank Account (fee deducted)
            JournalLineInput(
                account_id=bank_account.gl_account_id,
                debit_amount=Decimal("0"),
                credit_amount=fee_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=fee_amount,
                description=f"Transfer fee deducted: {reference}",
            ),
        ]

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=posting_date,
            posting_date=posting_date,
            description=description,
            reference=f"FEE-{reference}",
            currency_code=resolved_currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="PAYMENTS",
            source_document_type="TRANSFER_FEE",
            correlation_id=correlation_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Fee journal creation failed",
        )
        if error:
            return ExpensePostingResult(success=False, message=error.message)

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:FEE:{reference}:post:v1"

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="PAYMENTS",
            correlation_id=correlation_id,
            posted_by_user_id=user_id,
            success_message="Transfer fee posted successfully",
        )
        if not posting_result.success:
            return ExpensePostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        return ExpensePostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )

    @staticmethod
    def post_expense_reimbursement_reversal(
        db: Session,
        organization_id: UUID,
        claim_id: UUID,
        original_journal_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        *,
        bank_account_id: UUID,
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> ExpensePostingResult:
        """
        Post reversal for expense reimbursement.

        Creates opposite entries to reverse the original reimbursement posting.

        GL Entry Structure (opposite of original):
        - DEBIT: Bank account (restore the cash)
        - CREDIT: Employee payable account (restore the liability)

        Args:
            db: Database session
            organization_id: Organization scope
            claim_id: The expense claim
            original_journal_id: Original journal entry being reversed
            posting_date: Date for the reversal
            posted_by_user_id: User performing the reversal
            bank_account_id: Bank account used in original posting
            reason: Reason for reversal
            correlation_id: Tracing correlation ID

        Returns:
            ExpensePostingResult with reversal journal entry details
        """
        from app.models.finance.banking.bank_account import BankAccount
        from app.models.finance.gl.journal_entry import JournalEntry

        org_id = coerce_uuid(organization_id)
        c_id = coerce_uuid(claim_id)
        user_id = coerce_uuid(posted_by_user_id)
        bank_id = coerce_uuid(bank_account_id)
        orig_journal_id = coerce_uuid(original_journal_id)

        # Load expense claim
        claim = db.get(ExpenseClaim, c_id)
        if not claim or claim.organization_id != org_id:
            return ExpensePostingResult(
                success=False, message="Expense claim not found"
            )

        # Get original journal to get the amount
        original_journal = db.get(JournalEntry, orig_journal_id)
        if not original_journal:
            return ExpensePostingResult(
                success=False, message="Original journal not found"
            )

        # Get the reimbursement amount from original journal
        reversal_amount = sum(
            (
                line.debit_amount
                for line in original_journal.lines
                if line.debit_amount > 0
            ),
            Decimal("0"),
        )

        if reversal_amount <= Decimal("0"):
            return ExpensePostingResult(success=False, message="No amount to reverse")

        # Get bank account and its GL account
        bank_account = db.get(BankAccount, bank_id)
        if not bank_account or not bank_account.gl_account_id:
            return ExpensePostingResult(
                success=False, message="Bank account not found or has no GL account"
            )
        resolved_currency_code = (
            claim.currency_code
            or bank_account.currency_code
            or (org_context_service.get_functional_currency(db, org_id))
        )

        # Get employee payable account
        payable_account_id = ExpensePostingAdapter._get_employee_payable_account(
            db, org_id
        )
        if not payable_account_id:
            return ExpensePostingResult(
                success=False, message="Employee payable account not configured"
            )

        # Build reversal journal lines (opposite of original)
        journal_lines = [
            # Debit: Bank Account (restore cash)
            JournalLineInput(
                account_id=bank_account.gl_account_id,
                debit_amount=reversal_amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=reversal_amount,
                credit_amount_functional=Decimal("0"),
                description=f"Reversal: {claim.claim_number}",
            ),
            # Credit: Employee Payable (restore liability)
            JournalLineInput(
                account_id=payable_account_id,
                debit_amount=Decimal("0"),
                credit_amount=reversal_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=reversal_amount,
                description=f"Reversal: {claim.claim_number}",
            ),
        ]

        # Create reversal journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=posting_date,
            posting_date=posting_date,
            description=f"Reversal: Expense Reimbursement {claim.claim_number}"
            + (f" - {reason}" if reason else ""),
            reference=f"REV-{claim.claim_number}",
            currency_code=resolved_currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="EXPENSE",
            source_document_type="EXPENSE_REIMBURSEMENT_REVERSAL",
            source_document_id=c_id,
            correlation_id=correlation_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Reversal journal creation failed",
        )
        if error:
            return ExpensePostingResult(success=False, message=error.message)

        # Post to ledger
        idempotency_key = f"{org_id}:EXP:REIMB:{c_id}:reversal:v1"

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="EXPENSE",
            correlation_id=correlation_id,
            posted_by_user_id=user_id,
            success_message="Reimbursement reversal posted successfully",
        )
        if not posting_result.success:
            return ExpensePostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        return ExpensePostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )

    @staticmethod
    def post_transfer_fee_reversal(
        db: Session,
        organization_id: UUID,
        original_journal_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        *,
        fee_amount: Decimal,
        bank_account_id: UUID,
        reference: str,
        correlation_id: str | None = None,
    ) -> ExpensePostingResult:
        """
        Post reversal for transfer fee.

        Creates opposite entries to reverse the original fee posting.

        GL Entry Structure (opposite of original):
        - DEBIT: Bank account (restore the fee amount)
        - CREDIT: Bank charges expense (reverse the expense)

        Args:
            db: Database session
            organization_id: Organization scope
            original_journal_id: Original fee journal entry
            posting_date: Date for the reversal
            posted_by_user_id: User performing the reversal
            fee_amount: Fee amount to reverse
            bank_account_id: Bank account used in original posting
            reference: Payment reference
            correlation_id: Tracing correlation ID

        Returns:
            ExpensePostingResult with reversal journal entry details
        """
        from app.models.domain_settings import SettingDomain
        from app.models.finance.banking.bank_account import BankAccount
        from app.services.settings_spec import resolve_value

        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(posted_by_user_id)
        bank_id = coerce_uuid(bank_account_id)

        if fee_amount <= Decimal("0"):
            return ExpensePostingResult(
                success=False, message="No fee amount to reverse"
            )

        # Get bank account and its GL account
        bank_account = db.get(BankAccount, bank_id)
        if not bank_account or not bank_account.gl_account_id:
            return ExpensePostingResult(
                success=False, message="Bank account not found or has no GL account"
            )
        resolved_currency_code = bank_account.currency_code or (
            org_context_service.get_functional_currency(db, org_id)
        )

        # Get fee expense account from settings
        fee_account_id = resolve_value(
            db, SettingDomain.payments, "paystack_transfer_fee_account_id"
        )
        if not fee_account_id:
            return ExpensePostingResult(
                success=False, message="Fee expense account not configured"
            )

        fee_account_uuid = coerce_uuid(fee_account_id)

        # Build reversal journal lines (opposite of original)
        journal_lines = [
            # Debit: Bank Account (restore fee)
            JournalLineInput(
                account_id=bank_account.gl_account_id,
                debit_amount=fee_amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=fee_amount,
                credit_amount_functional=Decimal("0"),
                description=f"Fee reversal: {reference}",
            ),
            # Credit: Bank Charges Expense (reverse expense)
            JournalLineInput(
                account_id=fee_account_uuid,
                debit_amount=Decimal("0"),
                credit_amount=fee_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=fee_amount,
                description=f"Fee reversal: {reference}",
            ),
        ]

        # Create reversal journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=posting_date,
            posting_date=posting_date,
            description=f"Reversal: Transfer fee {reference}",
            reference=f"REV-FEE-{reference}",
            currency_code=resolved_currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="PAYMENTS",
            source_document_type="TRANSFER_FEE_REVERSAL",
            correlation_id=correlation_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Fee reversal journal creation failed",
        )
        if error:
            return ExpensePostingResult(success=False, message=error.message)

        # Post to ledger
        idempotency_key = f"{org_id}:FEE:{reference}:reversal:v1"

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="PAYMENTS",
            correlation_id=correlation_id,
            posted_by_user_id=user_id,
            success_message="Transfer fee reversal posted successfully",
        )
        if not posting_result.success:
            return ExpensePostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        return ExpensePostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )


# Module-level singleton instance
expense_posting_adapter = ExpensePostingAdapter()
