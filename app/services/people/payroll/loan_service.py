"""
Loan Service - Employee Loan Management.

Handles loan lifecycle: application, approval, disbursement, repayment.
Integrates with payroll for automatic deductions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.hr.employee import Employee
from app.models.people.payroll.employee_loan import EmployeeLoan, LoanStatus
from app.models.people.payroll.loan_repayment import (
    LoanRepayment,
    RepaymentType,
    SalarySlipLoanDeduction,
)
from app.models.people.payroll.loan_type import InterestMethod, LoanType
from app.models.people.payroll.salary_slip import SalarySlip, SalarySlipStatus
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)

CURRENCY_QUANT = Decimal("0.01")


def _round_currency(amount: Decimal) -> Decimal:
    """Round to 2 decimal places."""
    return amount.quantize(CURRENCY_QUANT, rounding=ROUND_HALF_UP)


@dataclass
class LoanDeductionItem:
    """Information about a loan deduction for payroll."""

    loan_id: UUID
    loan_number: str
    loan_type_name: str
    amount: Decimal
    principal_portion: Decimal
    interest_portion: Decimal
    balance_after: Decimal
    is_final_payment: bool


@dataclass
class LoanApplicationInput:
    """Input for creating a loan application."""

    employee_id: UUID
    principal_amount: Decimal
    tenure_months: int
    loan_type_id: UUID | None = None
    interest_rate: Decimal | None = None
    interest_method: str | None = None
    purpose: str | None = None
    first_repayment_date: date | None = None


class LoanService:
    """
    Service for employee loan management.

    Handles:
    - Loan application and validation
    - Approval workflow
    - Disbursement tracking
    - Repayment processing (manual and payroll)
    - Outstanding balance queries for payroll
    """

    # Minimum net pay as percentage of gross (prevents over-deduction)
    MIN_NET_PAY_RATIO = Decimal("0.33")  # Employee must receive at least 33% of gross

    def __init__(self, db: Session):
        self.db = db

    def generate_loan_number(self, organization_id: UUID) -> str:
        """Generate a unique loan number."""
        year = date.today().year

        # Get max sequence for this year
        from sqlalchemy import func

        max_seq = self.db.scalar(
            select(func.max(EmployeeLoan.loan_number)).where(
                EmployeeLoan.organization_id == organization_id,
                EmployeeLoan.loan_number.like(f"LOAN-{year}-%"),
            )
        )

        if max_seq:
            # Extract sequence number and increment
            try:
                seq = int(max_seq.split("-")[-1]) + 1
            except (ValueError, IndexError):
                seq = 1
        else:
            seq = 1

        return f"LOAN-{year}-{seq:05d}"

    def calculate_loan_terms(
        self,
        principal: Decimal,
        tenure_months: int,
        interest_rate: Decimal,
        interest_method: InterestMethod,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """
        Calculate loan terms: total interest, total repayable, monthly installment.

        Returns:
            Tuple of (total_interest, total_repayable, monthly_installment)
        """
        if interest_method == InterestMethod.NONE or interest_rate == 0:
            # No interest - simple division
            total_interest = Decimal("0")
            total_repayable = principal
            monthly_installment = _round_currency(principal / tenure_months)

        elif interest_method == InterestMethod.FLAT:
            # Simple/flat interest: interest = principal * rate * years
            years = Decimal(tenure_months) / 12
            annual_rate = interest_rate / 100
            total_interest = _round_currency(principal * annual_rate * years)
            total_repayable = principal + total_interest
            monthly_installment = _round_currency(total_repayable / tenure_months)

        else:  # REDUCING_BALANCE
            # EMI formula: EMI = P * r * (1+r)^n / ((1+r)^n - 1)
            # where r = monthly rate, n = number of months
            monthly_rate = interest_rate / 100 / 12

            if monthly_rate > 0:
                factor = (1 + monthly_rate) ** tenure_months
                monthly_installment = _round_currency(
                    principal * monthly_rate * factor / (factor - 1)
                )
                total_repayable = _round_currency(monthly_installment * tenure_months)
                total_interest = total_repayable - principal
            else:
                total_interest = Decimal("0")
                total_repayable = principal
                monthly_installment = _round_currency(principal / tenure_months)

        return total_interest, total_repayable, monthly_installment

    def create_loan(
        self,
        organization_id: UUID,
        input: LoanApplicationInput,
        created_by_id: UUID,
    ) -> EmployeeLoan:
        """
        Create a new loan application.

        Validates eligibility and calculates loan terms.
        """
        org_id = coerce_uuid(organization_id)
        emp_id = coerce_uuid(input.employee_id)
        user_id = coerce_uuid(created_by_id)

        # Validate employee exists
        employee = self.db.get(Employee, emp_id)
        if not employee or employee.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Employee not found")

        # Validate employee status - only active employees can apply for loans
        from app.services.people.employee_status_validator import (
            EmployeeStatusValidator,
            OperationType,
        )

        status_validator = EmployeeStatusValidator(self.db)
        result = status_validator.validate_operation(
            emp_id, OperationType.LOAN_APPLICATION
        )
        if not result.is_valid:
            raise HTTPException(status_code=400, detail=result.message)

        # Resolve interest rate and method — from loan type or direct input
        loan_type: LoanType | None = None
        type_id: UUID | None = None
        requires_approval = True  # default

        if input.loan_type_id:
            type_id = coerce_uuid(input.loan_type_id)
            loan_type = self.db.get(LoanType, type_id)
            if not loan_type or loan_type.organization_id != org_id:
                raise HTTPException(status_code=404, detail="Loan type not found")

            if not loan_type.is_active:
                raise HTTPException(status_code=400, detail="Loan type is not active")

            # Validate amount against type constraints
            if input.principal_amount < loan_type.min_amount:
                raise HTTPException(
                    status_code=400,
                    detail=f"Amount must be at least {loan_type.min_amount}",
                )
            if loan_type.max_amount and input.principal_amount > loan_type.max_amount:
                raise HTTPException(
                    status_code=400,
                    detail=f"Amount cannot exceed {loan_type.max_amount}",
                )

            # Validate tenure against type constraints
            if input.tenure_months < loan_type.min_tenure_months:
                raise HTTPException(
                    status_code=400,
                    detail=f"Tenure must be at least {loan_type.min_tenure_months} months",
                )
            if input.tenure_months > loan_type.max_tenure_months:
                raise HTTPException(
                    status_code=400,
                    detail=f"Tenure cannot exceed {loan_type.max_tenure_months} months",
                )

            # Validate service duration
            if loan_type.min_service_months > 0:
                service_months = self._calculate_service_months(employee)
                if service_months < loan_type.min_service_months:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Employee must have at least {loan_type.min_service_months} months of service",
                    )

            # Check for existing active loans of same type
            existing = self.db.scalar(
                select(EmployeeLoan).where(
                    EmployeeLoan.employee_id == emp_id,
                    EmployeeLoan.loan_type_id == type_id,
                    EmployeeLoan.status == LoanStatus.DISBURSED,
                )
            )
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Employee already has an active {loan_type.type_name} loan",
                )

            interest_rate = loan_type.default_interest_rate
            interest_method = loan_type.interest_method
            requires_approval = loan_type.requires_approval
        else:
            # Direct input — no loan type
            interest_rate = (
                input.interest_rate if input.interest_rate is not None else Decimal("0")
            )
            method_str = input.interest_method or "NONE"
            interest_method = InterestMethod(method_str)

        # Basic validation
        if input.principal_amount <= 0:
            raise HTTPException(
                status_code=400, detail="Principal amount must be positive"
            )
        if input.tenure_months < 1:
            raise HTTPException(
                status_code=400, detail="Tenure must be at least 1 month"
            )

        # Calculate loan terms
        total_interest, total_repayable, monthly_installment = (
            self.calculate_loan_terms(
                input.principal_amount,
                input.tenure_months,
                interest_rate,
                interest_method,
            )
        )

        # Generate loan number
        loan_number = self.generate_loan_number(org_id)

        # Determine first repayment date (default: start of next month)
        if input.first_repayment_date:
            first_repayment = input.first_repayment_date
        else:
            today = date.today()
            next_month = date(today.year, today.month, 1) + timedelta(days=32)
            first_repayment = date(next_month.year, next_month.month, 1)

        # Create loan
        loan = EmployeeLoan(
            organization_id=org_id,
            loan_number=loan_number,
            employee_id=emp_id,
            loan_type_id=type_id,
            principal_amount=input.principal_amount,
            interest_rate=interest_rate,
            total_interest=total_interest,
            total_repayable=total_repayable,
            tenure_months=input.tenure_months,
            monthly_installment=monthly_installment,
            outstanding_balance=total_repayable,
            first_repayment_date=first_repayment,
            purpose=input.purpose,
            status=LoanStatus.PENDING if requires_approval else LoanStatus.APPROVED,
            created_by_id=user_id,
        )

        self.db.add(loan)
        self.db.flush()

        logger.info(
            "Created loan %s for employee %s: %s over %d months",
            loan.loan_number,
            employee.full_name,
            input.principal_amount,
            input.tenure_months,
        )

        return loan

    def approve_loan(
        self,
        organization_id: UUID,
        loan_id: UUID,
        approved_by_id: UUID,
    ) -> EmployeeLoan:
        """Approve a pending loan."""
        org_id = coerce_uuid(organization_id)
        l_id = coerce_uuid(loan_id)
        user_id = coerce_uuid(approved_by_id)

        loan = self.db.get(EmployeeLoan, l_id)
        if not loan or loan.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Loan not found")

        if loan.status != LoanStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve loan with status: {loan.status.value}",
            )

        loan.status = LoanStatus.APPROVED
        loan.approval_date = date.today()
        loan.approved_by_id = user_id

        self.db.flush()

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=self.db,
                organization_id=loan.organization_id,
                entity_type="LOAN",
                entity_id=loan.loan_id,
                event="ON_APPROVAL",
                old_values={"status": "PENDING"},
                new_values={"status": "APPROVED"},
                user_id=user_id,
            )
        except Exception:
            logger.exception("Ignored exception")

        logger.info("Approved loan %s", loan.loan_number)
        return loan

    def reject_loan(
        self,
        organization_id: UUID,
        loan_id: UUID,
        rejected_by_id: UUID,
        reason: str,
    ) -> EmployeeLoan:
        """Reject a pending loan."""
        org_id = coerce_uuid(organization_id)
        l_id = coerce_uuid(loan_id)

        loan = self.db.get(EmployeeLoan, l_id)
        if not loan or loan.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Loan not found")

        if loan.status != LoanStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reject loan with status: {loan.status.value}",
            )

        loan.status = LoanStatus.REJECTED
        loan.rejection_reason = reason

        self.db.flush()

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=self.db,
                organization_id=loan.organization_id,
                entity_type="LOAN",
                entity_id=loan.loan_id,
                event="ON_REJECTION",
                old_values={"status": "PENDING"},
                new_values={"status": "REJECTED"},
                user_id=coerce_uuid(rejected_by_id),
            )
        except Exception:
            logger.exception("Ignored exception")

        logger.info("Rejected loan %s: %s", loan.loan_number, reason)
        return loan

    def disburse_loan(
        self,
        organization_id: UUID,
        loan_id: UUID,
        disbursed_by_id: UUID,
        disbursement_reference: str | None = None,
    ) -> EmployeeLoan:
        """Mark an approved loan as disbursed."""
        org_id = coerce_uuid(organization_id)
        l_id = coerce_uuid(loan_id)
        user_id = coerce_uuid(disbursed_by_id)

        loan = self.db.get(EmployeeLoan, l_id)
        if not loan or loan.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Loan not found")

        if loan.status != LoanStatus.APPROVED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot disburse loan with status: {loan.status.value}",
            )

        loan.status = LoanStatus.DISBURSED
        loan.disbursement_date = date.today()
        loan.disbursed_by_id = user_id
        loan.disbursement_reference = disbursement_reference

        self.db.flush()

        logger.info("Disbursed loan %s", loan.loan_number)
        return loan

    def get_active_loans_for_employee(
        self,
        employee_id: UUID,
        as_of_date: date | None = None,
        organization_id: UUID | None = None,
    ) -> list[EmployeeLoan]:
        """Get all active (disbursed, not completed) loans for an employee."""
        emp_id = coerce_uuid(employee_id)

        stmt = (
            select(EmployeeLoan)
            .where(
                EmployeeLoan.employee_id == emp_id,
                EmployeeLoan.status == LoanStatus.DISBURSED,
                EmployeeLoan.outstanding_balance > 0,
            )
            .order_by(EmployeeLoan.disbursement_date)
        )

        if organization_id is not None:
            stmt = stmt.where(
                EmployeeLoan.organization_id == coerce_uuid(organization_id)
            )
        else:
            logger.warning(
                "get_active_loans_for_employee called without organization_id for employee %s",
                emp_id,
            )

        return list(self.db.scalars(stmt).all())

    def get_due_deductions(
        self,
        employee_id: UUID,
        period_start: date,
        period_end: date,
        *,
        gross_pay: Decimal | None = None,
        total_existing_deductions: Decimal = Decimal("0"),
        exclude_slip_id: UUID | None = None,
        organization_id: UUID | None = None,
    ) -> list[LoanDeductionItem]:
        """
        Get loan deductions due for an employee in a pay period.

        Returns list of LoanDeductionItem with amounts to deduct.
        """
        emp_id = coerce_uuid(employee_id)

        # Get active loans (filtered by org_id when available)
        active_loans = self.get_active_loans_for_employee(
            emp_id, organization_id=organization_id
        )

        if not active_loans:
            return []

        deductions: list[LoanDeductionItem] = []
        running_loan_deductions = Decimal("0")

        for loan in active_loans:
            # Check if repayment is due in this period
            if loan.first_repayment_date and loan.first_repayment_date > period_end:
                # Loan repayment hasn't started yet
                continue

            if self._has_repayment_in_period(loan.loan_id, period_start, period_end):
                continue

            if self._has_linked_slip_deduction_in_period(
                loan.loan_id,
                period_start,
                period_end,
                exclude_slip_id=exclude_slip_id,
            ):
                continue

            # Calculate amount for this period
            amount = _round_currency(loan.monthly_installment)
            balance = loan.outstanding_balance

            # Handle final payment (may be less than regular installment)
            if amount > balance:
                amount = balance

            if gross_pay is not None and gross_pay > 0:
                min_net_pay = _round_currency(gross_pay * self.MIN_NET_PAY_RATIO)
                max_allowed_total_deduction = gross_pay - min_net_pay
                used_deductions = total_existing_deductions + running_loan_deductions
                remaining_capacity = _round_currency(
                    max_allowed_total_deduction - used_deductions
                )
                if remaining_capacity <= 0:
                    continue
                if amount > remaining_capacity:
                    amount = remaining_capacity
            if amount <= 0:
                continue

            # Calculate principal/interest split
            if loan.interest_rate == 0 or loan.total_interest == 0:
                principal_portion = amount
                interest_portion = Decimal("0")
            elif (
                loan.loan_type
                and loan.loan_type.interest_method == InterestMethod.REDUCING_BALANCE
            ):
                # Reducing-balance: interest on current outstanding balance
                interest_portion = _round_currency(
                    balance * (loan.interest_rate / Decimal("100") / Decimal("12"))
                )
                if interest_portion > amount:
                    interest_portion = amount
                principal_portion = amount - interest_portion
            else:
                # Flat interest: constant ratio across all installments
                interest_portion = _round_currency(
                    amount * (loan.total_interest / loan.total_repayable)
                )
                principal_portion = amount - interest_portion

            balance_after = _round_currency(balance - amount)
            is_final = balance_after <= 0

            deductions.append(
                LoanDeductionItem(
                    loan_id=loan.loan_id,
                    loan_number=loan.loan_number,
                    loan_type_name=loan.loan_type.type_name
                    if loan.loan_type
                    else "Loan",
                    amount=amount,
                    principal_portion=principal_portion,
                    interest_portion=interest_portion,
                    balance_after=max(balance_after, Decimal("0")),
                    is_final_payment=is_final,
                )
            )
            running_loan_deductions += amount

        return deductions

    def _has_repayment_in_period(
        self, loan_id: UUID, period_start: date, period_end: date
    ) -> bool:
        return (
            self.db.scalar(
                select(LoanRepayment.repayment_id).where(
                    LoanRepayment.loan_id == loan_id,
                    LoanRepayment.repayment_type == RepaymentType.PAYROLL_DEDUCTION,
                    LoanRepayment.repayment_date >= period_start,
                    LoanRepayment.repayment_date <= period_end,
                )
            )
            is not None
        )

    def _has_linked_slip_deduction_in_period(
        self,
        loan_id: UUID,
        period_start: date,
        period_end: date,
        *,
        exclude_slip_id: UUID | None,
    ) -> bool:
        stmt = (
            select(SalarySlipLoanDeduction.deduction_id)
            .join(SalarySlip, SalarySlip.slip_id == SalarySlipLoanDeduction.slip_id)
            .where(
                SalarySlipLoanDeduction.loan_id == loan_id,
                SalarySlip.start_date == period_start,
                SalarySlip.end_date == period_end,
                SalarySlip.status != SalarySlipStatus.CANCELLED,
            )
        )
        if exclude_slip_id:
            stmt = stmt.where(SalarySlip.slip_id != coerce_uuid(exclude_slip_id))
        return self.db.scalar(stmt) is not None

    def record_payroll_deduction(
        self,
        loan_id: UUID,
        slip_id: UUID,
        amount: Decimal,
        principal_portion: Decimal,
        interest_portion: Decimal,
        repayment_date: date,
        created_by_id: UUID | None = None,
        skip_link_creation: bool = False,
        organization_id: UUID | None = None,
    ) -> LoanRepayment:
        """
        Record a loan deduction from payroll.

        Updates loan balance and creates repayment record.
        """
        l_id = coerce_uuid(loan_id)
        s_id = coerce_uuid(slip_id)

        loan = self.db.get(EmployeeLoan, l_id)
        if not loan:
            raise ValueError(f"Loan {loan_id} not found")
        if organization_id is not None and loan.organization_id != organization_id:
            raise ValueError(f"Loan {loan_id} does not belong to organization {organization_id}")

        if loan.status != LoanStatus.DISBURSED:
            raise RuntimeError(
                f"Cannot record repayment for loan with status: {loan.status.value}"
            )

        # Update loan balance
        loan.outstanding_balance = _round_currency(loan.outstanding_balance - amount)
        loan.principal_paid = _round_currency(loan.principal_paid + principal_portion)
        loan.interest_paid = _round_currency(loan.interest_paid + interest_portion)
        loan.installments_paid += 1

        # Check if loan is fully repaid
        if loan.outstanding_balance <= 0:
            loan.status = LoanStatus.COMPLETED
            loan.completion_date = repayment_date
            loan.outstanding_balance = Decimal("0")

        # Create repayment record
        repayment = LoanRepayment(
            loan_id=l_id,
            repayment_type=RepaymentType.PAYROLL_DEDUCTION,
            repayment_date=repayment_date,
            amount=amount,
            principal_portion=principal_portion,
            interest_portion=interest_portion,
            balance_after=loan.outstanding_balance,
            salary_slip_id=s_id,
            created_by_id=created_by_id,
        )

        self.db.add(repayment)

        # Create link record (skip if already created during slip generation)
        if not skip_link_creation:
            link = SalarySlipLoanDeduction(
                slip_id=s_id,
                loan_id=l_id,
                amount=amount,
                principal_portion=principal_portion,
                interest_portion=interest_portion,
                repayment_id=repayment.repayment_id,
            )
            self.db.add(link)

        self.db.flush()

        logger.info(
            "Recorded payroll deduction for loan %s: %s (balance: %s)",
            loan.loan_number,
            amount,
            loan.outstanding_balance,
        )

        return repayment

    def record_manual_payment(
        self,
        organization_id: UUID,
        loan_id: UUID,
        amount: Decimal,
        payment_date: date,
        payment_reference: str | None = None,
        payment_method: str | None = None,
        notes: str | None = None,
        created_by_id: UUID | None = None,
    ) -> LoanRepayment:
        """Record a manual (non-payroll) loan payment."""
        org_id = coerce_uuid(organization_id)
        l_id = coerce_uuid(loan_id)

        loan = self.db.get(EmployeeLoan, l_id)
        if not loan or loan.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Loan not found")

        if loan.status != LoanStatus.DISBURSED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot record payment for loan with status: {loan.status.value}",
            )

        if amount > loan.outstanding_balance:
            logger.info(
                "Manual payment %s exceeds outstanding %s for loan %s, capping",
                amount,
                loan.outstanding_balance,
                loan.loan_number,
            )
            amount = loan.outstanding_balance

        # Calculate split
        if loan.interest_rate == 0:
            principal_portion = amount
            interest_portion = Decimal("0")
        elif (
            loan.loan_type
            and loan.loan_type.interest_method == InterestMethod.REDUCING_BALANCE
        ):
            # Reducing-balance: interest on current outstanding balance
            interest_portion = _round_currency(
                loan.outstanding_balance
                * (loan.interest_rate / Decimal("100") / Decimal("12"))
            )
            if interest_portion > amount:
                interest_portion = amount
            principal_portion = amount - interest_portion
        else:
            interest_portion = _round_currency(
                amount * (loan.total_interest / loan.total_repayable)
            )
            principal_portion = amount - interest_portion

        # Update loan
        loan.outstanding_balance = _round_currency(loan.outstanding_balance - amount)
        loan.principal_paid = _round_currency(loan.principal_paid + principal_portion)
        loan.interest_paid = _round_currency(loan.interest_paid + interest_portion)

        if loan.outstanding_balance <= 0:
            loan.status = LoanStatus.COMPLETED
            loan.completion_date = payment_date
            loan.outstanding_balance = Decimal("0")

        # Create repayment
        repayment = LoanRepayment(
            loan_id=l_id,
            repayment_type=RepaymentType.MANUAL_PAYMENT,
            repayment_date=payment_date,
            amount=amount,
            principal_portion=principal_portion,
            interest_portion=interest_portion,
            balance_after=loan.outstanding_balance,
            payment_reference=payment_reference,
            payment_method=payment_method,
            notes=notes,
            created_by_id=created_by_id,
        )

        self.db.add(repayment)
        self.db.flush()

        logger.info(
            "Recorded manual payment for loan %s: %s (balance: %s)",
            loan.loan_number,
            amount,
            loan.outstanding_balance,
        )

        return repayment

    def _calculate_service_months(self, employee: Employee) -> int:
        """Calculate employee's service duration in months."""
        if not employee.date_of_joining:
            return 0

        today = date.today()
        diff = (today.year - employee.date_of_joining.year) * 12
        diff += today.month - employee.date_of_joining.month

        return max(0, diff)

    def get(
        self,
        organization_id: UUID,
        loan_id: UUID,
    ) -> EmployeeLoan:
        """Get a loan by ID."""
        org_id = coerce_uuid(organization_id)
        l_id = coerce_uuid(loan_id)

        loan = self.db.get(EmployeeLoan, l_id)
        if not loan or loan.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Loan not found")

        return loan

    def list_for_employee(
        self,
        organization_id: UUID,
        employee_id: UUID,
        status: LoanStatus | None = None,
    ) -> list[EmployeeLoan]:
        """List all loans for an employee."""
        org_id = coerce_uuid(organization_id)
        emp_id = coerce_uuid(employee_id)

        stmt = (
            select(EmployeeLoan)
            .where(
                EmployeeLoan.organization_id == org_id,
                EmployeeLoan.employee_id == emp_id,
            )
            .order_by(EmployeeLoan.created_at.desc())
        )

        if status:
            stmt = stmt.where(EmployeeLoan.status == status)

        return list(self.db.scalars(stmt).all())


# Module-level singleton
loan_service = LoanService
