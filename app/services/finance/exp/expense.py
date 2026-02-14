"""
Expense Service.

Business logic for expense entry management.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.exp.expense_entry import (
    ExpenseEntry,
    ExpenseStatus,
    PaymentMethod,
)
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


class ExpenseService:
    """Service for expense entry operations."""

    @staticmethod
    def generate_expense_number(db: Session, organization_id: UUID) -> str:
        """Generate unique expense number."""
        today = date.today()
        prefix = f"EXP-{today.strftime('%Y%m')}-"

        last = (
            db.query(ExpenseEntry)
            .filter(
                ExpenseEntry.organization_id == organization_id,
                ExpenseEntry.expense_number.like(f"{prefix}%"),
            )
            .order_by(ExpenseEntry.expense_number.desc())
            .first()
        )

        if last:
            try:
                seq = int(last.expense_number.split("-")[-1]) + 1
            except ValueError:
                seq = 1
        else:
            seq = 1

        return f"{prefix}{seq:04d}"

    @staticmethod
    def create(
        db: Session,
        organization_id: str,
        expense_date: date,
        expense_account_id: str,
        amount: Decimal,
        description: str,
        payment_method: PaymentMethod,
        created_by: str,
        payment_account_id: str | None = None,
        tax_code_id: str | None = None,
        tax_amount: Decimal = Decimal("0"),
        currency_code: str = settings.default_functional_currency_code,
        payee: str | None = None,
        receipt_reference: str | None = None,
        notes: str | None = None,
        project_id: str | None = None,
        cost_center_id: str | None = None,
        business_unit_id: str | None = None,
    ) -> ExpenseEntry:
        """Create a new expense entry."""
        org_id = coerce_uuid(organization_id)

        expense = ExpenseEntry(
            organization_id=org_id,
            expense_number=ExpenseService.generate_expense_number(db, org_id),
            expense_date=expense_date,
            expense_account_id=coerce_uuid(expense_account_id),
            payment_account_id=coerce_uuid(payment_account_id)
            if payment_account_id
            else None,
            amount=amount,
            tax_amount=tax_amount,
            currency_code=currency_code,
            tax_code_id=coerce_uuid(tax_code_id) if tax_code_id else None,
            description=description,
            payment_method=payment_method,
            payee=payee,
            receipt_reference=receipt_reference,
            notes=notes,
            project_id=coerce_uuid(project_id) if project_id else None,
            cost_center_id=coerce_uuid(cost_center_id) if cost_center_id else None,
            business_unit_id=coerce_uuid(business_unit_id)
            if business_unit_id
            else None,
            status=ExpenseStatus.DRAFT,
            created_by=coerce_uuid(created_by),
        )

        db.add(expense)
        db.flush()

        return expense

    @staticmethod
    def submit(
        db: Session,
        organization_id: str,
        expense_id: str,
        submitted_by: str,
    ) -> ExpenseEntry:
        """Submit expense for approval."""
        org_id = coerce_uuid(organization_id)
        expense = (
            db.query(ExpenseEntry)
            .filter(
                ExpenseEntry.expense_id == coerce_uuid(expense_id),
                ExpenseEntry.organization_id == org_id,
            )
            .first()
        )
        if not expense:
            raise ValueError("Expense not found")

        if expense.status != ExpenseStatus.DRAFT:
            raise ValueError(f"Cannot submit expense in {expense.status.value} status")

        expense.status = ExpenseStatus.SUBMITTED
        expense.submitted_by = coerce_uuid(submitted_by)
        expense.submitted_at = datetime.utcnow()

        db.flush()
        return expense

    @staticmethod
    def approve(
        db: Session,
        organization_id: str,
        expense_id: str,
        approved_by: str,
    ) -> ExpenseEntry:
        """Approve expense."""
        org_id = coerce_uuid(organization_id)
        expense = (
            db.query(ExpenseEntry)
            .filter(
                ExpenseEntry.expense_id == coerce_uuid(expense_id),
                ExpenseEntry.organization_id == org_id,
            )
            .first()
        )
        if not expense:
            raise ValueError("Expense not found")

        if expense.status != ExpenseStatus.SUBMITTED:
            raise ValueError(f"Cannot approve expense in {expense.status.value} status")

        expense.status = ExpenseStatus.APPROVED
        expense.approved_by = coerce_uuid(approved_by)
        expense.approved_at = datetime.utcnow()

        db.flush()
        return expense

    @staticmethod
    def reject(
        db: Session,
        organization_id: str,
        expense_id: str,
        rejected_by: str,
    ) -> ExpenseEntry:
        """Reject expense."""
        org_id = coerce_uuid(organization_id)
        expense = (
            db.query(ExpenseEntry)
            .filter(
                ExpenseEntry.expense_id == coerce_uuid(expense_id),
                ExpenseEntry.organization_id == org_id,
            )
            .first()
        )
        if not expense:
            raise ValueError("Expense not found")

        if expense.status not in [ExpenseStatus.SUBMITTED, ExpenseStatus.APPROVED]:
            raise ValueError(f"Cannot reject expense in {expense.status.value} status")

        expense.status = ExpenseStatus.REJECTED
        expense.updated_by = coerce_uuid(rejected_by)
        expense.updated_at = datetime.utcnow()

        db.flush()
        return expense

    @staticmethod
    def post(
        db: Session,
        organization_id: str,
        expense_id: str,
        posted_by: str,
        fiscal_period_id: str,
    ) -> ExpenseEntry:
        """
        Post expense to general ledger.

        This creates an APPROVED journal entry, posts it through LedgerPostingService
        (writing immutable `gl.posted_ledger_line`), and updates the expense record.
        """
        from app.models.finance.gl.fiscal_period import FiscalPeriod
        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import JournalInput, JournalLineInput
        from app.services.finance.gl.period_guard import PeriodGuardService
        from app.services.finance.posting.base import BasePostingAdapter

        org_id = coerce_uuid(organization_id)
        expense = (
            db.query(ExpenseEntry)
            .filter(
                ExpenseEntry.expense_id == coerce_uuid(expense_id),
                ExpenseEntry.organization_id == org_id,
            )
            .first()
        )
        if not expense:
            raise ValueError("Expense not found")

        if expense.status != ExpenseStatus.APPROVED:
            raise ValueError(f"Cannot post expense in {expense.status.value} status")

        if not expense.payment_account_id:
            raise ValueError("Payment account is required to post expense")

        user_id = coerce_uuid(posted_by)
        period_id = coerce_uuid(fiscal_period_id)

        # Validate fiscal period belongs to the organization
        fiscal_period = db.get(FiscalPeriod, period_id)
        if not fiscal_period or fiscal_period.organization_id != org_id:
            raise ValueError(
                "Fiscal period not found or does not belong to organization"
            )

        # Ensure posting date belongs to the selected fiscal period and is open
        posting_date = expense.expense_date
        period_for_date = PeriodGuardService.get_period_for_date(
            db, org_id, posting_date
        )
        if not period_for_date:
            raise ValueError(f"No fiscal period found for posting date {posting_date}")
        if period_for_date.fiscal_period_id != period_id:
            raise ValueError("Expense date does not match selected fiscal period")

        PeriodGuardService.require_open_period(
            db,
            org_id,
            posting_date,
            allow_adjustment=False,
            reopen_session_id=None,
        )

        total_amount = (expense.amount or Decimal("0")) + (
            expense.tax_amount or Decimal("0")
        )

        journal_lines: list[JournalLineInput] = [
            # Debit expense
            JournalLineInput(
                account_id=expense.expense_account_id,
                debit_amount=expense.amount,
                credit_amount=Decimal("0"),
                description=expense.description,
                business_unit_id=expense.business_unit_id,
                cost_center_id=expense.cost_center_id,
                project_id=expense.project_id,
            ),
        ]

        # Debit tax if applicable
        if (expense.tax_amount or Decimal("0")) > 0 and expense.tax_code_id:
            from app.models.finance.tax.tax_code import TaxCode

            tax_code = db.get(TaxCode, expense.tax_code_id)
            if tax_code and tax_code.tax_paid_account_id:
                journal_lines.append(
                    JournalLineInput(
                        account_id=tax_code.tax_paid_account_id,
                        debit_amount=expense.tax_amount,
                        credit_amount=Decimal("0"),
                        description=f"Input tax - {expense.description}",
                        business_unit_id=expense.business_unit_id,
                        cost_center_id=expense.cost_center_id,
                        project_id=expense.project_id,
                    )
                )

        # Credit payment account (cash/bank/etc)
        journal_lines.append(
            JournalLineInput(
                account_id=expense.payment_account_id,
                debit_amount=Decimal("0"),
                credit_amount=total_amount,
                description=f"Payment for: {expense.description}",
                business_unit_id=expense.business_unit_id,
                cost_center_id=expense.cost_center_id,
                project_id=expense.project_id,
            )
        )

        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=posting_date,
            posting_date=posting_date,
            description=f"Expense: {expense.description}",
            reference=expense.expense_number,
            currency_code=expense.currency_code,
            lines=journal_lines,
            source_module="EXP",
            source_document_type="EXPENSE",
            source_document_id=expense.expense_id,
            correlation_id=getattr(expense, "correlation_id", None),
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db=db,
            organization_id=org_id,
            journal_input=journal_input,
            posted_by_user_id=user_id,
            error_prefix="Expense journal creation failed",
        )
        if error:
            raise ValueError(error.message)

        idempotency_key = BasePostingAdapter.make_idempotency_key(
            organization_id=org_id,
            source_module="EXP",
            source_document_id=coerce_uuid(expense.expense_id),
            action="post",
            version="v1",
        )

        posting_result = BasePostingAdapter.post_to_ledger(
            db=db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="EXP",
            correlation_id=getattr(expense, "correlation_id", None),
            posted_by_user_id=user_id,
            success_message="Expense posted successfully",
            error_prefix="Expense ledger posting failed",
        )
        if not posting_result.success:
            raise ValueError(posting_result.message)

        # Update expense
        expense.status = ExpenseStatus.POSTED
        expense.journal_entry_id = journal.journal_entry_id
        expense.posted_by = user_id
        expense.posted_at = datetime.utcnow()
        db.flush()

        return expense

    @staticmethod
    def void(
        db: Session,
        expense_id: str,
        voided_by: str,
    ) -> ExpenseEntry:
        """Void an expense entry."""
        expense = db.get(ExpenseEntry, coerce_uuid(expense_id))
        if not expense:
            raise ValueError("Expense not found")

        if expense.status == ExpenseStatus.POSTED:
            raise ValueError(
                "Cannot void posted expense - reverse the journal entry instead"
            )

        expense.status = ExpenseStatus.VOID
        expense.updated_by = coerce_uuid(voided_by)
        expense.updated_at = datetime.utcnow()

        db.flush()
        return expense


expense_service = ExpenseService()
