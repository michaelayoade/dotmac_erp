"""
Payroll → GL Integration Adapter.

Creates GL journal entries from salary slips and payroll runs.
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.people.payroll import (
    SalarySlip,
    SalarySlipStatus,
    SalaryComponentType,
    PayrollEntry,
    PayrollEntryStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class GLPostingResult:
    """Result of posting to GL."""
    success: bool
    journal_entry_id: Optional[uuid.UUID] = None
    error_message: Optional[str] = None


class PayrollGLAdapter:
    """
    Adapter for creating GL journal entries from payroll.

    When a salary slip is approved, this adapter:
    1. Creates debit entries for each earning (expense accounts)
    2. Creates credit entries for deductions (liability accounts)
    3. Creates credit entry for net pay (payroll payable account)
    4. Links the journal entry to the salary slip

    Supports:
    - Individual salary slip posting
    - Bulk payroll run posting (consolidated or per-employee)
    """

    @staticmethod
    def post_salary_slip(
        db: Session,
        org_id: uuid.UUID,
        slip_id: uuid.UUID,
        posting_date: date,
        user_id: uuid.UUID,
    ) -> GLPostingResult:
        """
        Post a single salary slip to GL.

        Args:
            db: Database session
            org_id: Organization ID
            slip_id: Salary slip ID
            posting_date: Date to post the entry
            user_id: User creating the entry

        Returns:
            GLPostingResult with journal ID or error
        """
        try:
            # Get the salary slip
            slip = db.get(SalarySlip, slip_id)
            if not slip:
                return GLPostingResult(
                    success=False,
                    error_message=f"Salary slip {slip_id} not found"
                )

            if slip.organization_id != org_id:
                return GLPostingResult(
                    success=False,
                    error_message="Salary slip does not belong to this organization"
                )

            if slip.status != SalarySlipStatus.APPROVED:
                return GLPostingResult(
                    success=False,
                    error_message=f"Salary slip must be APPROVED, current status: {slip.status}"
                )

            if slip.journal_entry_id is not None:
                return GLPostingResult(
                    success=False,
                    error_message="Salary slip already posted to GL"
                )

            # Get employee for cost center
            employee = slip.employee
            cost_center_id = employee.cost_center_id if employee else None

            # Import GL services (deferred to avoid circular imports)
            from app.services.finance.gl.journal import JournalService
            from app.services.finance.gl import JournalInput, JournalLineInput

            # Build journal entry lines
            lines = []

            # DEBITS: Earnings (Salary Expense accounts)
            for earning in slip.earnings:
                component = earning.salary_component
                if component and component.expense_account_id:
                    lines.append(JournalLineInput(
                        account_id=component.expense_account_id,
                        debit_amount=earning.amount,
                        credit_amount=Decimal("0.00"),
                        description=f"{component.component_name}: {slip.employee_code}",
                        cost_center_id=cost_center_id,
                    ))
                else:
                    logger.warning(
                        f"Earning component {earning.component_id} has no expense account"
                    )

            # CREDITS: Deductions (Liability accounts like tax, pension, etc.)
            for deduction in slip.deductions:
                component = deduction.salary_component
                if component and component.liability_account_id:
                    lines.append(JournalLineInput(
                        account_id=component.liability_account_id,
                        debit_amount=Decimal("0.00"),
                        credit_amount=deduction.amount,
                        description=f"{component.component_name}: {slip.employee_code}",
                    ))
                else:
                    logger.warning(
                        f"Deduction component {deduction.component_id} has no liability account"
                    )

            # CREDIT: Net Pay (Payroll Payable account)
            payroll_payable_account_id = (
                employee.default_payroll_payable_account_id
                if employee else None
            )

            if payroll_payable_account_id and slip.net_pay > 0:
                lines.append(JournalLineInput(
                    account_id=payroll_payable_account_id,
                    debit_amount=Decimal("0.00"),
                    credit_amount=slip.net_pay,
                    description=f"Net Pay: {slip.employee_code}",
                ))

            if not lines:
                return GLPostingResult(
                    success=False,
                    error_message="No valid GL lines to post"
                )

            # Validate debits = credits
            total_debits = sum(line.debit_amount for line in lines)
            total_credits = sum(line.credit_amount for line in lines)
            if total_debits != total_credits:
                logger.warning(
                    f"Salary slip {slip_id} debits ({total_debits}) != credits ({total_credits})"
                )

            # Create journal entry
            journal_input = JournalInput(
                journal_date=posting_date,
                reference_number=slip.slip_number,
                description=f"Salary: {slip.employee_code} ({slip.payroll_period_start} to {slip.payroll_period_end})",
                source_module="PAYROLL",
                source_document_id=str(slip_id),
                lines=lines,
            )

            journal = JournalService.create_journal(
                db, org_id, journal_input, user_id
            )

            # Link journal to slip
            slip.journal_entry_id = journal.journal_entry_id
            slip.status = SalarySlipStatus.POSTED

            db.commit()

            logger.info(
                f"Posted salary slip {slip_id} to GL as journal {journal.journal_entry_id}"
            )

            return GLPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
            )

        except Exception as e:
            logger.exception(f"Error posting salary slip {slip_id}")
            db.rollback()
            return GLPostingResult(
                success=False,
                error_message=str(e),
            )

    @staticmethod
    def post_payroll_run(
        db: Session,
        org_id: uuid.UUID,
        payroll_entry_id: uuid.UUID,
        posting_date: date,
        user_id: uuid.UUID,
        consolidated: bool = False,
    ) -> GLPostingResult:
        """
        Post an entire payroll run to GL.

        Args:
            db: Database session
            org_id: Organization ID
            payroll_entry_id: Payroll entry/run ID
            posting_date: Date to post
            user_id: User creating entries
            consolidated: If True, create one journal for entire run;
                         If False, create one journal per salary slip

        Returns:
            GLPostingResult (for consolidated) or summary result
        """
        try:
            payroll = db.get(PayrollEntry, payroll_entry_id)
            if not payroll:
                return GLPostingResult(
                    success=False,
                    error_message="Payroll entry not found"
                )

            if payroll.status != PayrollEntryStatus.APPROVED:
                return GLPostingResult(
                    success=False,
                    error_message="Payroll must be APPROVED before posting"
                )

            # Get all approved slips in this run
            slips = [s for s in payroll.salary_slips if s.status == SalarySlipStatus.APPROVED]

            if not slips:
                return GLPostingResult(
                    success=False,
                    error_message="No approved salary slips to post"
                )

            if consolidated:
                # TODO: Implement consolidated posting
                # For now, fall through to per-slip posting
                pass

            # Post each slip individually
            posted_count = 0
            failed_count = 0
            last_error = None

            for slip in slips:
                result = PayrollGLAdapter.post_salary_slip(
                    db, org_id, slip.slip_id, posting_date, user_id
                )
                if result.success:
                    posted_count += 1
                else:
                    failed_count += 1
                    last_error = result.error_message

            # Update payroll status
            if failed_count == 0:
                payroll.status = PayrollEntryStatus.POSTED
                db.commit()

            logger.info(
                f"Payroll run {payroll_entry_id}: posted {posted_count}, failed {failed_count}"
            )

            return GLPostingResult(
                success=failed_count == 0,
                error_message=f"Posted {posted_count}, failed {failed_count}. Last error: {last_error}"
                if failed_count > 0 else None,
            )

        except Exception as e:
            logger.exception(f"Error posting payroll run {payroll_entry_id}")
            db.rollback()
            return GLPostingResult(
                success=False,
                error_message=str(e),
            )

    @staticmethod
    def reverse_salary_slip_posting(
        db: Session,
        org_id: uuid.UUID,
        slip_id: uuid.UUID,
        reversal_date: date,
        user_id: uuid.UUID,
        reason: str,
    ) -> GLPostingResult:
        """
        Reverse a posted salary slip's GL entry.

        Creates a reversing journal entry and unlinks the original.
        """
        try:
            slip = db.get(SalarySlip, slip_id)
            if not slip or slip.status != SalarySlipStatus.POSTED:
                return GLPostingResult(
                    success=False,
                    error_message="Salary slip not found or not posted"
                )

            if not slip.journal_entry_id:
                return GLPostingResult(
                    success=False,
                    error_message="No journal entry linked to reverse"
                )

            from app.services.finance.gl.journal import JournalService

            # Reverse the journal entry
            reversal = JournalService.reverse_journal(
                db,
                org_id,
                slip.journal_entry_id,
                reversal_date,
                reason,
                user_id,
            )

            # Unlink and reset slip status
            slip.journal_entry_id = None
            slip.status = SalarySlipStatus.APPROVED

            db.commit()

            logger.info(f"Reversed salary slip {slip_id} posting")

            return GLPostingResult(
                success=True,
                journal_entry_id=reversal.journal_entry_id if reversal else None,
            )

        except Exception as e:
            logger.exception(f"Error reversing salary slip {slip_id}")
            db.rollback()
            return GLPostingResult(
                success=False,
                error_message=str(e),
            )
