"""
Expense Service.

Business logic for expense entry management.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ifrs.exp.expense_entry import ExpenseEntry, ExpenseStatus, PaymentMethod
from app.models.ifrs.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.models.ifrs.gl.journal_entry_line import JournalEntryLine
from app.services.common import coerce_uuid


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
        payment_account_id: Optional[str] = None,
        tax_code_id: Optional[str] = None,
        tax_amount: Decimal = Decimal("0"),
        currency_code: str = settings.default_functional_currency_code,
        payee: Optional[str] = None,
        receipt_reference: Optional[str] = None,
        notes: Optional[str] = None,
        project_id: Optional[str] = None,
        cost_center_id: Optional[str] = None,
        business_unit_id: Optional[str] = None,
    ) -> ExpenseEntry:
        """Create a new expense entry."""
        org_id = coerce_uuid(organization_id)

        expense = ExpenseEntry(
            organization_id=org_id,
            expense_number=ExpenseService.generate_expense_number(db, org_id),
            expense_date=expense_date,
            expense_account_id=coerce_uuid(expense_account_id),
            payment_account_id=coerce_uuid(payment_account_id) if payment_account_id else None,
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
            business_unit_id=coerce_uuid(business_unit_id) if business_unit_id else None,
            status=ExpenseStatus.DRAFT,
            created_by=coerce_uuid(created_by),
        )

        db.add(expense)
        db.flush()

        return expense

    @staticmethod
    def submit(
        db: Session,
        expense_id: str,
        submitted_by: str,
    ) -> ExpenseEntry:
        """Submit expense for approval."""
        expense = db.get(ExpenseEntry, coerce_uuid(expense_id))
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
        expense_id: str,
        approved_by: str,
    ) -> ExpenseEntry:
        """Approve expense."""
        expense = db.get(ExpenseEntry, coerce_uuid(expense_id))
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
        expense_id: str,
        rejected_by: str,
    ) -> ExpenseEntry:
        """Reject expense."""
        expense = db.get(ExpenseEntry, coerce_uuid(expense_id))
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
        expense_id: str,
        posted_by: str,
        fiscal_period_id: str,
    ) -> ExpenseEntry:
        """Post expense to general ledger."""
        expense = db.get(ExpenseEntry, coerce_uuid(expense_id))
        if not expense:
            raise ValueError("Expense not found")

        if expense.status != ExpenseStatus.APPROVED:
            raise ValueError(f"Cannot post expense in {expense.status.value} status")

        if not expense.payment_account_id:
            raise ValueError("Payment account is required to post expense")

        user_id = coerce_uuid(posted_by)
        period_id = coerce_uuid(fiscal_period_id)

        # Generate journal number
        today = date.today()
        prefix = f"JE-EXP-{today.strftime('%Y%m')}-"
        last_je = (
            db.query(JournalEntry)
            .filter(
                JournalEntry.organization_id == expense.organization_id,
                JournalEntry.journal_number.like(f"{prefix}%"),
            )
            .order_by(JournalEntry.journal_number.desc())
            .first()
        )
        if last_je:
            try:
                seq = int(last_je.journal_number.split("-")[-1]) + 1
            except ValueError:
                seq = 1
        else:
            seq = 1
        journal_number = f"{prefix}{seq:04d}"

        # Create journal entry
        journal = JournalEntry(
            organization_id=expense.organization_id,
            journal_number=journal_number,
            journal_type=JournalType.STANDARD,
            fiscal_period_id=period_id,
            entry_date=expense.expense_date,
            posting_date=expense.expense_date,
            description=f"Expense: {expense.description}",
            reference=expense.expense_number,
            currency_code=expense.currency_code,
            source_module="EXP",
            source_document_id=expense.expense_id,
            status=JournalStatus.POSTED,
            created_by_user_id=user_id,
            posted_by_user_id=user_id,
            posted_at=datetime.utcnow(),
        )
        db.add(journal)
        db.flush()

        total_amount = expense.amount + expense.tax_amount

        # Debit expense account
        debit_line = JournalEntryLine(
            journal_entry_id=journal.journal_entry_id,
            line_number=1,
            account_id=expense.expense_account_id,
            description=expense.description,
            debit_amount=expense.amount,
            credit_amount=Decimal("0"),
            currency_code=expense.currency_code,
        )
        db.add(debit_line)

        # Debit tax if applicable
        line_num = 2
        if expense.tax_amount > 0 and expense.tax_code_id:
            from app.models.ifrs.tax.tax_code import TaxCode
            tax_code = db.get(TaxCode, expense.tax_code_id)
            if tax_code and tax_code.input_tax_account_id:
                tax_line = JournalEntryLine(
                    journal_entry_id=journal.journal_entry_id,
                    line_number=line_num,
                    account_id=tax_code.input_tax_account_id,
                    description=f"Input tax - {expense.description}",
                    debit_amount=expense.tax_amount,
                    credit_amount=Decimal("0"),
                    currency_code=expense.currency_code,
                )
                db.add(tax_line)
                line_num += 1

        # Credit payment account
        credit_line = JournalEntryLine(
            journal_entry_id=journal.journal_entry_id,
            line_number=line_num,
            account_id=expense.payment_account_id,
            description=f"Payment for: {expense.description}",
            debit_amount=Decimal("0"),
            credit_amount=total_amount,
            currency_code=expense.currency_code,
        )
        db.add(credit_line)

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
            raise ValueError("Cannot void posted expense - reverse the journal entry instead")

        expense.status = ExpenseStatus.VOID
        expense.updated_by = coerce_uuid(voided_by)
        expense.updated_at = datetime.utcnow()

        db.flush()
        return expense


expense_service = ExpenseService()
