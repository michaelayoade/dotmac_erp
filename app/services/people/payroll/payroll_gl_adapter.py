"""
PayrollGLAdapter - Converts payroll documents to GL entries.

Transforms salary slips into journal entries and posts them to the
general ledger, integrating People payroll with Finance GL.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID
import uuid as uuid_lib

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.people.payroll.salary_slip import (
    SalarySlip,
    SalarySlipEarning,
    SalarySlipDeduction,
    SalarySlipStatus,
)
from app.models.people.payroll.salary_component import SalaryComponentType
from app.models.people.hr.employee import Employee
from app.services.common import coerce_uuid
from app.services.finance.gl.journal import JournalService, JournalInput, JournalLineInput
from app.services.finance.gl.ledger_posting import LedgerPostingService, PostingRequest
from app.models.finance.gl.journal_entry import JournalType

logger = logging.getLogger(__name__)

# Component code for employer pension contribution deduction line.
EMPLOYER_PENSION_COMPONENT_CODE = "PENSION_EMPLOYER"


@dataclass
class PayrollPostingResult:
    """Result of a payroll posting operation."""

    success: bool
    journal_entry_id: Optional[UUID] = None
    posting_batch_id: Optional[UUID] = None
    message: str = ""


class PayrollGLAdapter:
    """
    Adapter for posting payroll documents to the general ledger.

    Converts salary slips into journal entries and coordinates
    posting through the LedgerPostingService.

    GL Posting Pattern for Salary Slip:
    ┌─────────────────────────────────────────────────────────────┐
    │ Debit:  Salary Expense accounts (earnings)                  │
    │         - Basic Salary Expense                              │
    │         - Housing Allowance Expense                         │
    │         - Transport Allowance Expense                       │
    │         - etc.                                              │
    │                                                             │
    │ Credit: Deduction Liability accounts                        │
    │         - PAYE Tax Payable                                  │
    │         - Pension Contribution Payable                      │
    │         - etc.                                              │
    │                                                             │
    │ Credit: Net Pay to Payroll Payable account                  │
    │         (Employee's net salary owed)                        │
    └─────────────────────────────────────────────────────────────┘
    """

    @staticmethod
    def post_salary_slip(
        db: Session,
        organization_id: UUID,
        slip_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> PayrollPostingResult:
        """
        Post a salary slip to the general ledger.

        Creates a journal entry with:
        - Debit: Expense accounts (from earning components)
        - Credit: Liability accounts (from deduction components)
        - Credit: Payroll Payable (net pay)

        Args:
            db: Database session
            organization_id: Organization scope
            slip_id: Salary slip to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            PayrollPostingResult with outcome

        Raises:
            HTTPException: If posting fails
        """
        org_id = coerce_uuid(organization_id)
        s_id = coerce_uuid(slip_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load salary slip with related data
        slip = db.get(SalarySlip, s_id)
        if not slip or slip.organization_id != org_id:
            return PayrollPostingResult(success=False, message="Salary slip not found")

        if slip.status != SalarySlipStatus.APPROVED:
            return PayrollPostingResult(
                success=False,
                message=f"Salary slip must be APPROVED to post (current: {slip.status.value})",
            )

        if slip.journal_entry_id:
            return PayrollPostingResult(
                success=False,
                message="Salary slip has already been posted",
            )

        # Load employee for cost center
        employee = db.get(Employee, slip.employee_id)
        if not employee:
            return PayrollPostingResult(success=False, message="Employee not found")

        # Get organization for payroll payable account
        from app.models.finance.core_org.organization import Organization
        org = db.get(Organization, org_id)
        if not org:
            return PayrollPostingResult(success=False, message="Organization not found")

        # Get payroll payable account (from org settings or employee)
        payroll_payable_account_id = (
            employee.default_payroll_payable_account_id
            or getattr(org, 'salary_payable_account_id', None)
        )

        if not payroll_payable_account_id:
            return PayrollPostingResult(
                success=False,
                message="No payroll payable account configured for employee or organization",
            )

        # Build journal entry lines
        journal_lines: list[JournalLineInput] = []
        exchange_rate = slip.exchange_rate or Decimal("1.0")

        # Debit lines: Expense accounts (earnings)
        for earning in slip.earnings:
            if earning.statistical_component or earning.do_not_include_in_total:
                continue  # Skip statistical components

            # Get expense account from component
            component = earning.component
            if not component or not component.expense_account_id:
                return PayrollPostingResult(
                    success=False,
                    message=f"No expense account for earning component: {earning.component_name}",
                )

            functional_amount = earning.amount * exchange_rate

            journal_lines.append(
                JournalLineInput(
                    account_id=component.expense_account_id,
                    debit_amount=earning.amount,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=functional_amount,
                    credit_amount_functional=Decimal("0"),
                    description=f"Salary: {earning.component_name} - {slip.employee_name}",
                    cost_center_id=slip.cost_center_id or employee.cost_center_id,
                )
            )

        # Credit lines: Liability accounts (deductions)
        for deduction in slip.deductions:
            if deduction.statistical_component:
                continue  # Skip statistical components

            # Get liability account from component
            component = deduction.component
            if not component or not component.liability_account_id:
                return PayrollPostingResult(
                    success=False,
                    message=f"No liability account for deduction component: {deduction.component_name}",
                )
            if (
                deduction.do_not_include_in_total
                and component.component_code != EMPLOYER_PENSION_COMPONENT_CODE
            ):
                continue  # Skip excluded items except employer pension

            functional_amount = deduction.amount * exchange_rate

            journal_lines.append(
                JournalLineInput(
                    account_id=component.liability_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=deduction.amount,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=functional_amount,
                    description=f"Deduction: {deduction.component_name} - {slip.employee_name}",
                )
            )

        # Credit line: Net Pay to Payroll Payable
        net_functional = slip.net_pay * exchange_rate

        journal_lines.append(
            JournalLineInput(
                account_id=payroll_payable_account_id,
                debit_amount=Decimal("0"),
                credit_amount=slip.net_pay,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=net_functional,
                description=f"Net Pay: {slip.employee_name}",
            )
        )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=slip.posting_date,
            posting_date=posting_date,
            description=f"Salary Slip {slip.slip_number} - {slip.employee_name}",
            reference=slip.slip_number,
            currency_code=slip.currency_code,
            exchange_rate=exchange_rate,
            lines=journal_lines,
            source_module="PAYROLL",
            source_document_type="SALARY_SLIP",
            source_document_id=s_id,
        )

        try:
            journal = JournalService.create_journal(
                db, org_id, journal_input, user_id
            )

            # Submit and approve automatically for payroll posting
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)

            # Auto-approve (in production, use designated system account for SoD)
            JournalService.approve_journal(
                db, org_id, journal.journal_entry_id, user_id
            )

        except HTTPException as e:
            return PayrollPostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:PAYROLL:{s_id}:post:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="PAYROLL",
            posted_by_user_id=user_id,
        )

        try:
            posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

            if not posting_result.success:
                return PayrollPostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Ledger posting failed: {posting_result.message}",
                )

            # Update salary slip with journal reference
            slip.journal_entry_id = journal.journal_entry_id
            slip.status = SalarySlipStatus.POSTED
            slip.posted_at = datetime.now(timezone.utc)
            slip.posted_by_id = user_id

            db.commit()

            return PayrollPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
                message="Salary slip posted successfully",
            )

        except Exception as e:
            return PayrollPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=f"Posting error: {str(e)}",
            )

    @staticmethod
    def reverse_salary_slip_posting(
        db: Session,
        organization_id: UUID,
        slip_id: UUID,
        reversal_date: date,
        reversed_by_user_id: UUID,
        reason: str,
    ) -> PayrollPostingResult:
        """
        Reverse a posted salary slip's GL entries.

        Args:
            db: Database session
            organization_id: Organization scope
            slip_id: Salary slip to reverse
            reversal_date: Date for reversal
            reversed_by_user_id: User reversing
            reason: Reason for reversal

        Returns:
            PayrollPostingResult with reversal outcome
        """
        from app.services.finance.gl.reversal import ReversalService

        org_id = coerce_uuid(organization_id)
        s_id = coerce_uuid(slip_id)
        user_id = coerce_uuid(reversed_by_user_id)

        slip = db.get(SalarySlip, s_id)
        if not slip or slip.organization_id != org_id:
            return PayrollPostingResult(success=False, message="Salary slip not found")

        if not slip.journal_entry_id:
            return PayrollPostingResult(
                success=False, message="Salary slip has not been posted"
            )

        try:
            result = ReversalService.create_reversal(
                db=db,
                organization_id=org_id,
                original_journal_id=slip.journal_entry_id,
                reversal_date=reversal_date,
                created_by_user_id=user_id,
                reason=f"Salary slip reversal: {reason}",
                auto_post=True,
            )

            if not result.success:
                return PayrollPostingResult(success=False, message=result.message)

            # Update slip status
            slip.status = SalarySlipStatus.CANCELLED

            db.commit()

            return PayrollPostingResult(
                success=True,
                journal_entry_id=result.reversal_journal_id,
                message="Salary slip posting reversed successfully",
            )

        except HTTPException as e:
            return PayrollPostingResult(
                success=False, message=f"Reversal failed: {e.detail}"
            )

    @staticmethod
    def post_payroll_entry(
        db: Session,
        organization_id: UUID,
        entry_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
    ) -> PayrollPostingResult:
        """
        Post payroll run as ONE consolidated journal entry.

        Creates:
        - 1 Debit line: Total gross -> salaries_expense_account_id
        - N Credit lines: Deductions grouped by component (PAYE, Pension, NHF, etc.)
        - 1 Credit line: Total net pay -> salary_payable_account_id

        Args:
            db: Database session
            organization_id: Organization scope
            entry_id: Payroll entry ID
            posting_date: Date for the GL posting
            posted_by_user_id: User posting

        Returns:
            PayrollPostingResult with outcome
        """
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.models.finance.core_org.organization import Organization
        from app.models.people.payroll.payroll_entry import PayrollEntry, PayrollEntryStatus

        org_id = coerce_uuid(organization_id)
        e_id = coerce_uuid(entry_id)
        user_id = coerce_uuid(posted_by_user_id)

        # 1. Load and validate payroll entry
        entry = db.get(PayrollEntry, e_id)
        if not entry or entry.organization_id != org_id:
            return PayrollPostingResult(success=False, message="Payroll entry not found")

        if entry.status != PayrollEntryStatus.APPROVED:
            return PayrollPostingResult(
                success=False,
                message=f"Payroll entry must be APPROVED to post (current: {entry.status.value})",
            )

        if entry.journal_entry_id:
            return PayrollPostingResult(
                success=False,
                message="Payroll entry has already been posted",
            )

        # 2. Load organization and validate GL accounts
        org = db.get(Organization, org_id)
        if not org:
            return PayrollPostingResult(success=False, message="Organization not found")

        if not org.salaries_expense_account_id:
            return PayrollPostingResult(
                success=False,
                message="Salaries Expense account not configured. Go to Admin > Organizations to set it.",
            )
        if not org.salary_payable_account_id:
            return PayrollPostingResult(
                success=False,
                message="Salary Payable account not configured. Go to Admin > Organizations to set it.",
            )

        # 3. Get all approved slips with deductions eagerly loaded
        slips = db.scalars(
            select(SalarySlip)
            .options(
                selectinload(SalarySlip.deductions).selectinload(SalarySlipDeduction.component)
            )
            .where(
                SalarySlip.payroll_entry_id == e_id,
                SalarySlip.status == SalarySlipStatus.APPROVED,
            )
        ).all()

        if not slips:
            return PayrollPostingResult(
                success=False,
                message="No approved salary slips found in payroll entry",
            )

        # 4. Aggregate totals
        total_gross = sum((slip.gross_pay for slip in slips), Decimal("0"))
        total_net = sum((slip.net_pay for slip in slips), Decimal("0"))

        # Group deductions by component (to get liability account)
        # Key: component_id, Value: (component_name, total_amount, liability_account_id)
        deductions_by_component: dict[UUID, tuple[str, Decimal, UUID]] = {}

        for slip in slips:
            for ded in slip.deductions:
                if ded.statistical_component:
                    continue
                comp = ded.component
                if not comp or not comp.liability_account_id:
                    continue
                # Skip deductions marked as do_not_include_in_total
                # except employer pension which we still want to post
                if (
                    ded.do_not_include_in_total
                    and comp.component_code != EMPLOYER_PENSION_COMPONENT_CODE
                ):
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

        # 5. Build journal lines
        journal_lines: list[JournalLineInput] = []
        currency_code = slips[0].currency_code
        exchange_rate = slips[0].exchange_rate or Decimal("1.0")

        # Period reference for descriptions
        period_ref = f"{entry.payroll_month or entry.start_date.month}/{entry.payroll_year or entry.start_date.year}"

        # Debit: Total Gross to Salaries Expense
        journal_lines.append(
            JournalLineInput(
                account_id=org.salaries_expense_account_id,
                debit_amount=total_gross,
                credit_amount=Decimal("0"),
                debit_amount_functional=total_gross * exchange_rate,
                credit_amount_functional=Decimal("0"),
                description=f"Payroll {period_ref} - Salaries Expense",
            )
        )

        # Credits: Each deduction type
        for comp_id, (comp_name, amount, liability_acc_id) in deductions_by_component.items():
            if amount <= 0:
                continue
            journal_lines.append(
                JournalLineInput(
                    account_id=liability_acc_id,
                    debit_amount=Decimal("0"),
                    credit_amount=amount,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=amount * exchange_rate,
                    description=f"Payroll {period_ref} - {comp_name}",
                )
            )

        # Credit: Net Pay to Salary Payable
        journal_lines.append(
            JournalLineInput(
                account_id=org.salary_payable_account_id,
                debit_amount=Decimal("0"),
                credit_amount=total_net,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=total_net * exchange_rate,
                description=f"Payroll {period_ref} - Net Pay",
            )
        )

        # 6. Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=entry.posting_date or posting_date,
            posting_date=posting_date,
            description=f"Payroll Run {period_ref} ({len(slips)} employees)",
            reference=f"PR-{entry.payroll_year or entry.start_date.year}-{(entry.payroll_month or entry.start_date.month):02d}",
            currency_code=currency_code,
            exchange_rate=exchange_rate,
            lines=journal_lines,
            source_module="PAYROLL",
            source_document_type="PAYROLL_ENTRY",
            source_document_id=e_id,
        )

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)
        except HTTPException as e:
            return PayrollPostingResult(
                success=False,
                message=f"Journal creation failed: {e.detail}",
            )

        # 7. Post to ledger
        idempotency_key = f"{org_id}:PAYROLL_ENTRY:{e_id}:consolidated:v1"
        posting_result = LedgerPostingService.post_journal_entry(
            db,
            PostingRequest(
                organization_id=org_id,
                journal_entry_id=journal.journal_entry_id,
                posting_date=posting_date,
                idempotency_key=idempotency_key,
                source_module="PAYROLL",
                posted_by_user_id=user_id,
            ),
        )

        if not posting_result.success:
            return PayrollPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=f"Ledger posting failed: {posting_result.message}",
            )

        # 8. Update all slips to POSTED
        now = datetime.now(timezone.utc)
        for slip in slips:
            slip.status = SalarySlipStatus.POSTED
            slip.journal_entry_id = journal.journal_entry_id
            slip.posted_at = now
            slip.posted_by_id = user_id

        # 9. Update payroll entry to POSTED
        entry.status = PayrollEntryStatus.POSTED
        entry.journal_entry_id = journal.journal_entry_id

        db.commit()

        return PayrollPostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=f"Posted: Gross {total_gross:,.2f} | Net {total_net:,.2f} ({len(slips)} employees)",
        )


# Module-level singleton instance
payroll_gl_adapter = PayrollGLAdapter()
