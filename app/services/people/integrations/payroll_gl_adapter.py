"""
Payroll → GL Integration Adapter.

Creates GL journal entries from salary slips and payroll runs.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.people.payroll import (
    PayrollEntry,
    PayrollEntryStatus,
    SalarySlip,
    SalarySlipStatus,
)
from app.services.common import coerce_uuid
from app.services.finance.posting.base import BasePostingAdapter
from app.services.settings_cache import get_cached_setting

logger = logging.getLogger(__name__)


@dataclass
class GLPostingResult:
    """Result of posting to GL."""

    success: bool
    journal_entry_id: uuid.UUID | None = None
    posting_batch_id: uuid.UUID | None = None
    error_message: str | None = None


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
                    success=False, error_message=f"Salary slip {slip_id} not found"
                )

            if slip.organization_id != org_id:
                return GLPostingResult(
                    success=False,
                    error_message="Salary slip does not belong to this organization",
                )

            if slip.status != SalarySlipStatus.APPROVED:
                return GLPostingResult(
                    success=False,
                    error_message=f"Salary slip must be APPROVED, current status: {slip.status}",
                )

            if slip.journal_entry_id is not None:
                return GLPostingResult(
                    success=False, error_message="Salary slip already posted to GL"
                )

            # Get employee for cost center
            employee = slip.employee
            cost_center_id = employee.cost_center_id if employee else None

            # Import GL services (deferred to avoid circular imports)
            from app.models.finance.gl.journal_entry import JournalType
            from app.services.finance.gl import JournalInput, JournalLineInput

            # Build journal entry lines
            lines: list[JournalLineInput] = []

            # DEBITS: Earnings (Salary Expense accounts)
            for earning in slip.earnings:
                component = earning.component
                if component and component.expense_account_id:
                    lines.append(
                        JournalLineInput(
                            account_id=component.expense_account_id,
                            debit_amount=earning.amount,
                            credit_amount=Decimal("0.00"),
                            description=f"{component.component_name}: {slip.employee_name or slip.employee_id}",
                            cost_center_id=cost_center_id,
                        )
                    )
                else:
                    logger.warning(
                        f"Earning component {earning.component_id} has no expense account"
                    )

            # CREDITS: Deductions (Liability accounts like tax, pension, etc.)
            for deduction in slip.deductions:
                component = deduction.component
                if component and component.liability_account_id:
                    lines.append(
                        JournalLineInput(
                            account_id=component.liability_account_id,
                            debit_amount=Decimal("0.00"),
                            credit_amount=deduction.amount,
                            description=f"{component.component_name}: {slip.employee_name or slip.employee_id}",
                        )
                    )
                else:
                    logger.warning(
                        f"Deduction component {deduction.component_id} has no liability account"
                    )

            # CREDIT: Net Pay (Payroll Payable account)
            payroll_payable_account_id = (
                employee.default_payroll_payable_account_id if employee else None
            )

            if payroll_payable_account_id and slip.net_pay > 0:
                lines.append(
                    JournalLineInput(
                        account_id=payroll_payable_account_id,
                        debit_amount=Decimal("0.00"),
                        credit_amount=slip.net_pay,
                        description=f"Net Pay: {slip.employee_name or slip.employee_id}",
                    )
                )

            if not lines:
                return GLPostingResult(
                    success=False, error_message="No valid GL lines to post"
                )

            # Validate debits = credits
            total_debits = sum((line.debit_amount for line in lines), Decimal("0"))
            total_credits = sum((line.credit_amount for line in lines), Decimal("0"))
            if total_debits != total_credits:
                logger.warning(
                    f"Salary slip {slip_id} debits ({total_debits}) != credits ({total_credits})"
                )

            # Create journal entry
            journal_input = JournalInput(
                journal_type=JournalType.STANDARD,
                entry_date=posting_date,
                posting_date=posting_date,
                reference=slip.slip_number,
                description=(
                    f"Salary: {slip.employee_name or slip.employee_id} "
                    f"({slip.start_date} to {slip.end_date})"
                ),
                currency_code=slip.currency_code,
                exchange_rate=slip.exchange_rate,
                source_module="PAYROLL",
                source_document_type="SALARY_SLIP",
                source_document_id=slip_id,
                lines=lines,
            )

            journal, error = BasePostingAdapter.create_and_approve_journal(
                db,
                org_id,
                journal_input,
                user_id,
                error_prefix="Journal creation failed",
            )
            if error:
                return GLPostingResult(success=False, error_message=error.message)

            posting_result = BasePostingAdapter.post_to_ledger(
                db,
                organization_id=org_id,
                journal_entry_id=journal.journal_entry_id,
                posting_date=posting_date,
                idempotency_key=f"{org_id}:PAYROLL:SLIP:{slip_id}:post:v1",
                source_module="PAYROLL",
                correlation_id=None,
                posted_by_user_id=user_id,
                success_message="Salary slip posted successfully",
            )
            if not posting_result.success:
                return GLPostingResult(
                    success=False, error_message=posting_result.message
                )

            # Link journal to slip
            slip.journal_entry_id = journal.journal_entry_id
            slip.status = SalarySlipStatus.POSTED

            # Trigger payslip posted notification
            try:
                from app.services.people.payroll.payroll_notifications import (
                    PayrollNotificationService,
                )

                notification_service = PayrollNotificationService(db)
                employee = slip.employee
                if employee:
                    notification_service.notify_payslip_posted(
                        slip, employee, queue_email=True
                    )
            except Exception as notify_err:
                logger.warning(
                    "Failed to send notification for slip %s: %s",
                    slip_id,
                    notify_err,
                )

            db.commit()

            logger.info(
                f"Posted salary slip {slip_id} to GL as journal {journal.journal_entry_id}"
            )

            return GLPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
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
        posted_at: datetime | None = None,
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
                    success=False, error_message="Payroll entry not found"
                )

            if payroll.status != PayrollEntryStatus.APPROVED:
                return GLPostingResult(
                    success=False,
                    error_message="Payroll must be APPROVED before posting",
                )

            # Get all approved slips in this run
            slips = [
                s for s in payroll.salary_slips if s.status == SalarySlipStatus.APPROVED
            ]

            if not slips:
                return GLPostingResult(
                    success=False, error_message="No approved salary slips to post"
                )

            if consolidated:
                # Consolidated posting: ONE journal entry for entire payroll run
                return PayrollGLAdapter._post_payroll_run_consolidated(
                    db, org_id, payroll, slips, posting_date, user_id, posted_at
                )

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
                if failed_count > 0
                else None,
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
                    success=False, error_message="Salary slip not found or not posted"
                )

            if not slip.journal_entry_id:
                return GLPostingResult(
                    success=False, error_message="No journal entry linked to reverse"
                )

            from app.services.finance.gl.journal import JournalService

            # Reverse the journal entry
            reversal = JournalService.reverse_entry(
                db,
                org_id,
                slip.journal_entry_id,
                reversal_date,
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

    @staticmethod
    def _post_payroll_run_consolidated(
        db: Session,
        org_id: uuid.UUID,
        payroll: PayrollEntry,
        slips: list[SalarySlip],
        posting_date: date,
        user_id: uuid.UUID,
        posted_at: datetime | None = None,
    ) -> GLPostingResult:
        """
        Post payroll run as ONE consolidated journal entry.

        Creates:
        - 1 Debit line: Total gross → org.salaries_expense_account_id
        - N Credit lines: Deductions grouped by component (PAYE, Pension, NHF, etc.)
        - 1 Credit line: Total net pay → org.salary_payable_account_id
        """
        from app.models.finance.core_org.organization import Organization
        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl import JournalInput, JournalLineInput

        try:
            # Check if already posted
            if payroll.journal_entry_id is not None:
                return GLPostingResult(
                    success=False, error_message="Payroll run already posted to GL"
                )

            # Load organization and validate GL accounts
            org = db.get(Organization, org_id)
            if not org:
                return GLPostingResult(
                    success=False, error_message="Organization not found"
                )

            if not org.salaries_expense_account_id:
                return GLPostingResult(
                    success=False,
                    error_message="Salaries Expense account not configured. Go to Admin > Organizations to set it.",
                )
            if not org.salary_payable_account_id:
                return GLPostingResult(
                    success=False,
                    error_message="Salary Payable account not configured. Go to Admin > Organizations to set it.",
                )

            # Aggregate totals
            total_gross = sum(
                ((slip.gross_pay or Decimal("0")) for slip in slips),
                Decimal("0"),
            )
            total_net = sum(
                ((slip.net_pay or Decimal("0")) for slip in slips),
                Decimal("0"),
            )

            # Group deductions by component to aggregate amounts per liability account
            # Key: component_id, Value: (component_name, total_amount, liability_account_id)
            deductions_by_component: dict[
                uuid.UUID, tuple[str, Decimal, uuid.UUID]
            ] = {}

            for slip in slips:
                for ded in slip.deductions:
                    if ded.statistical_component:
                        continue
                    comp = ded.component
                    if not comp or not comp.liability_account_id:
                        logger.warning(
                            f"Deduction component {ded.component_id} has no liability account, skipping"
                        )
                        continue

                    key = comp.component_id
                    if key in deductions_by_component:
                        name, amt, acc_id = deductions_by_component[key]
                        deductions_by_component[key] = (name, amt + ded.amount, acc_id)
                    else:
                        deductions_by_component[key] = (
                            comp.component_name,
                            ded.amount,
                            comp.liability_account_id,
                        )

            # Build journal lines
            lines: list[JournalLineInput] = []
            currency_code = slips[0].currency_code if slips else "NGN"
            exchange_rate = slips[0].exchange_rate if slips else Decimal("1.0")

            # Period reference for descriptions
            period_month = payroll.payroll_month or payroll.start_date.month
            period_year = payroll.payroll_year or payroll.start_date.year
            period_ref = f"{period_month}/{period_year}"

            # DEBIT: Total Gross to Salaries Expense
            lines.append(
                JournalLineInput(
                    account_id=org.salaries_expense_account_id,
                    debit_amount=total_gross,
                    credit_amount=Decimal("0.00"),
                    description=f"Payroll {period_ref} - Salaries Expense ({len(slips)} employees)",
                )
            )

            # CREDITS: Each deduction type to its liability account
            for _comp_id, (
                comp_name,
                amount,
                liability_acc_id,
            ) in deductions_by_component.items():
                if amount <= 0:
                    continue
                lines.append(
                    JournalLineInput(
                        account_id=liability_acc_id,
                        debit_amount=Decimal("0.00"),
                        credit_amount=amount,
                        description=f"Payroll {period_ref} - {comp_name}",
                    )
                )

            # CREDIT: Net Pay to Salary Payable
            lines.append(
                JournalLineInput(
                    account_id=org.salary_payable_account_id,
                    debit_amount=Decimal("0.00"),
                    credit_amount=total_net,
                    description=f"Payroll {period_ref} - Net Pay ({len(slips)} employees)",
                )
            )

            # Validate debits = credits, allow small rounding adjustment if configured
            total_debits = sum((line.debit_amount for line in lines), Decimal("0"))
            total_credits = sum((line.credit_amount for line in lines), Decimal("0"))
            diff = total_debits - total_credits
            if diff != 0:
                rounding_account_id = None
                rounding_account_id_raw = get_cached_setting(
                    db, SettingDomain.payroll, "payroll_rounding_account_id", None
                )
                if rounding_account_id_raw:
                    try:
                        rounding_account_id = coerce_uuid(
                            rounding_account_id_raw, raise_http=False
                        )
                    except Exception:
                        rounding_account_id = None
                if rounding_account_id:
                    max_rounding = Decimal("5.00")
                    if abs(diff) <= max_rounding:
                        lines.append(
                            JournalLineInput(
                                account_id=rounding_account_id,
                                debit_amount=Decimal("0.00") if diff > 0 else abs(diff),
                                credit_amount=abs(diff)
                                if diff > 0
                                else Decimal("0.00"),
                                description=f"Payroll {period_ref} - Rounding Adjustment",
                            )
                        )
                        total_debits = sum(
                            (line.debit_amount for line in lines), Decimal("0")
                        )
                        total_credits = sum(
                            (line.credit_amount for line in lines), Decimal("0")
                        )
                        diff = total_debits - total_credits
                if diff != 0:
                    logger.error(
                        f"Payroll run {payroll.entry_id} is unbalanced: "
                        f"debits={total_debits}, credits={total_credits}, diff={diff}"
                    )
                    return GLPostingResult(
                        success=False,
                        error_message=(
                            f"Journal entry would be unbalanced by {diff}. "
                            "Check deduction configurations."
                        ),
                    )

            # Create journal entry
            journal_input = JournalInput(
                journal_type=JournalType.STANDARD,
                entry_date=posting_date,
                posting_date=posting_date,
                reference=f"PR-{period_year}-{period_month:02d}",
                description=f"Payroll Run {period_ref} ({len(slips)} employees)",
                currency_code=currency_code,
                exchange_rate=exchange_rate,
                source_module="PAYROLL",
                source_document_type="PAYROLL_ENTRY",
                source_document_id=payroll.entry_id,
                lines=lines,
            )

            journal, error = BasePostingAdapter.create_and_approve_journal(
                db,
                org_id,
                journal_input,
                user_id,
                error_prefix="Journal creation failed",
            )
            if error:
                return GLPostingResult(success=False, error_message=error.message)

            posting_result = BasePostingAdapter.post_to_ledger(
                db,
                organization_id=org_id,
                journal_entry_id=journal.journal_entry_id,
                posting_date=posting_date,
                idempotency_key=f"{org_id}:PAYROLL:RUN:{payroll.entry_id}:post:v1",
                source_module="PAYROLL",
                correlation_id=None,
                posted_by_user_id=user_id,
                success_message="Payroll run posted successfully",
            )
            if not posting_result.success:
                return GLPostingResult(
                    success=False, error_message=posting_result.message
                )

            # Link journal to payroll entry
            payroll.journal_entry_id = journal.journal_entry_id
            payroll.status = PayrollEntryStatus.POSTED
            payroll.status_changed_at = posted_at or datetime.now(UTC)
            payroll.status_changed_by_id = user_id

            # Update all slips to POSTED with same journal reference
            now = posted_at or datetime.now(UTC)
            for slip in slips:
                slip.status = SalarySlipStatus.POSTED
                slip.journal_entry_id = journal.journal_entry_id
                slip.posted_at = now
                slip.posted_by_id = user_id

            db.commit()

            logger.info(
                f"Posted payroll run {payroll.entry_id} to GL as consolidated journal {journal.journal_entry_id}: "
                f"gross={total_gross}, net={total_net}, employees={len(slips)}"
            )

            return GLPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
            )

        except Exception as e:
            logger.exception(
                f"Error posting consolidated payroll run {payroll.entry_id}"
            )
            db.rollback()
            return GLPostingResult(
                success=False,
                error_message=str(e),
            )
