"""
PayrollGLAdapter - Converts payroll documents to GL entries.

Transforms salary slips into journal entries and posts them to the
general ledger, integrating People payroll with Finance GL.
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
            if deduction.statistical_component or deduction.do_not_include_in_total:
                continue  # Skip statistical components

            # Get liability account from component
            component = deduction.component
            if not component or not component.liability_account_id:
                return PayrollPostingResult(
                    success=False,
                    message=f"No liability account for deduction component: {deduction.component_name}",
                )

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
    ) -> list[PayrollPostingResult]:
        """
        Post all approved salary slips in a payroll entry.

        Args:
            db: Database session
            organization_id: Organization scope
            entry_id: Payroll entry ID
            posting_date: Date for the GL posting
            posted_by_user_id: User posting

        Returns:
            List of PayrollPostingResult for each slip
        """
        from app.models.people.payroll.payroll_entry import PayrollEntry, PayrollEntryStatus

        org_id = coerce_uuid(organization_id)
        e_id = coerce_uuid(entry_id)

        # Load payroll entry
        entry = db.get(PayrollEntry, e_id)
        if not entry or entry.organization_id != org_id:
            return [PayrollPostingResult(success=False, message="Payroll entry not found")]

        if entry.status != PayrollEntryStatus.APPROVED:
            return [PayrollPostingResult(
                success=False,
                message=f"Payroll entry must be APPROVED to post (current: {entry.status.value})",
            )]

        # Get all approved slips for this entry
        slips = (
            db.query(SalarySlip)
            .filter(
                SalarySlip.payroll_entry_id == e_id,
                SalarySlip.status == SalarySlipStatus.APPROVED,
            )
            .all()
        )

        if not slips:
            return [PayrollPostingResult(
                success=False,
                message="No approved salary slips found in payroll entry",
            )]

        # Post each slip
        results = []
        for slip in slips:
            result = PayrollGLAdapter.post_salary_slip(
                db=db,
                organization_id=org_id,
                slip_id=slip.slip_id,
                posting_date=posting_date,
                posted_by_user_id=posted_by_user_id,
            )
            results.append(result)

        # Update entry status if all slips posted successfully
        if all(r.success for r in results):
            entry.status = PayrollEntryStatus.POSTED
            db.commit()

        return results


# Module-level singleton instance
payroll_gl_adapter = PayrollGLAdapter()
