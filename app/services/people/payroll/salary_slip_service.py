"""
SalarySlipService - Salary slip lifecycle management.

Handles creation, calculation, and workflow for salary slips.
Integrates with PAYECalculator for NTA 2025 tax computation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.hr.employment_type import EmploymentType
from app.models.people.payroll.salary_assignment import SalaryStructureAssignment
from app.models.people.payroll.salary_component import (
    SalaryComponent,
    SalaryComponentType,
)
from app.models.people.payroll.salary_slip import (
    SalarySlip,
    SalarySlipDeduction,
    SalarySlipEarning,
    SalarySlipStatus,
)
from app.models.people.payroll.salary_structure import SalaryStructure
from app.services.common import coerce_uuid
from app.services.people.payroll.paye_calculator import PAYEBreakdown, PAYECalculator

logger = logging.getLogger(__name__)

# Standard component codes for statutory deductions
BASIC_COMPONENT_CODE = "BASIC"
PENSION_COMPONENT_CODE = "PENSION"
EMPLOYER_PENSION_COMPONENT_CODE = "PENSION_EMPLOYER"
NHF_COMPONENT_CODE = "NHF"
NHIS_COMPONENT_CODE = "NHIS"
PAYE_COMPONENT_CODE = "PAYE"

# Statutory deductions withheld from employee pay
STATUTORY_COMPONENT_CODES = {
    PENSION_COMPONENT_CODE,
    NHF_COMPONENT_CODE,
    NHIS_COMPONENT_CODE,
    PAYE_COMPONENT_CODE,
}

# Employer contributions (not deducted from employee, but tracked separately)
EMPLOYER_CONTRIBUTION_CODES = {
    EMPLOYER_PENSION_COMPONENT_CODE,
}


@dataclass
class SalarySlipInput:
    """Input for creating a salary slip."""

    employee_id: UUID
    start_date: date
    end_date: date
    posting_date: date | None = None
    total_working_days: Decimal | None = None
    absent_days: Decimal = Decimal("0")
    leave_without_pay: Decimal = Decimal("0")


class SalarySlipService:
    """
    Service for salary slip lifecycle management.

    Handles creation, calculation, submission, approval, and workflow.
    Integrates with PAYECalculator for NTA 2025 tax computation.
    """

    @staticmethod
    def _is_contract_staff_employee(
        db: Session,
        employee: Employee,
        structure: SalaryStructure,
    ) -> bool:
        employment_type = employee.employment_type
        if employment_type is None and employee.employment_type_id:
            employment_type = db.get(EmploymentType, employee.employment_type_id)

        type_code = (
            (employment_type.type_code or "").strip().lower() if employment_type else ""
        )
        type_name = (
            (employment_type.type_name or "").strip().lower() if employment_type else ""
        )
        is_contract = type_code == "contract" or type_name == "contract"
        is_contract_structure = (
            structure.structure_name or ""
        ).strip().lower() == "contract staff"
        return is_contract or is_contract_structure

    @staticmethod
    def get_or_create_statutory_component(
        db: Session,
        organization_id: UUID,
        component_code: str,
        component_name: str,
        abbr: str,
        display_order: int = 100,
        created_by_id: UUID | None = None,
    ) -> SalaryComponent:
        """
        Get an existing statutory component or create it if it doesn't exist.

        Args:
            db: Database session
            organization_id: Organization ID
            component_code: Component code (e.g., "PAYE", "PENSION")
            component_name: Display name
            abbr: Abbreviation for payslips
            display_order: Order on payslip
            created_by_id: User creating the component

        Returns:
            SalaryComponent for the statutory deduction
        """
        org_id = coerce_uuid(organization_id)

        # Check if component exists
        component = db.scalar(
            select(SalaryComponent).where(
                SalaryComponent.organization_id == org_id,
                SalaryComponent.component_code == component_code,
            )
        )

        if component:
            return component

        # Create new statutory component
        component = SalaryComponent(
            organization_id=org_id,
            component_code=component_code,
            component_name=component_name,
            abbr=abbr,
            component_type=SalaryComponentType.DEDUCTION,
            is_statutory=True,
            is_tax_applicable=False,  # Statutory deductions are non-taxable
            depends_on_payment_days=False,  # Statutory calculated from annual amounts
            display_order=display_order,
            created_by_id=created_by_id,
        )
        db.add(component)
        db.flush()

        return component

    @staticmethod
    def get_statutory_components(
        db: Session,
        organization_id: UUID,
        created_by_id: UUID | None = None,
        include_employer_contributions: bool = False,
    ) -> dict[str, SalaryComponent]:
        """
        Get or create statutory deduction components needed for PAYE calculation.

        Args:
            db: Database session
            organization_id: Organization ID
            created_by_id: User creating components (optional)
            include_employer_contributions: If True, include employer contribution
                components like PENSION_EMPLOYER (default: False)

        Returns:
            Dictionary mapping component codes to SalaryComponent objects
        """
        org_id = coerce_uuid(organization_id)
        components = {}

        # Define statutory deductions (withheld from employee)
        statutory_defs = [
            (PENSION_COMPONENT_CODE, "Pension Contribution", "PEN", 101),
            (NHF_COMPONENT_CODE, "National Housing Fund", "NHF", 102),
            (NHIS_COMPONENT_CODE, "National Health Insurance", "NHIS", 103),
            (PAYE_COMPONENT_CODE, "Pay As You Earn Tax", "PAYE", 104),
        ]

        # Optionally include employer contributions
        if include_employer_contributions:
            statutory_defs.append(
                (
                    EMPLOYER_PENSION_COMPONENT_CODE,
                    "Employer Pension Contribution",
                    "PEN-ER",
                    105,
                ),
            )

        for code, name, abbr, order in statutory_defs:
            component = SalarySlipService.get_or_create_statutory_component(
                db, org_id, code, name, abbr, order, created_by_id
            )
            components[code] = component

        return components

    @staticmethod
    def generate_slip_number(db: Session, organization_id: UUID) -> str:
        """Generate a unique slip number.

        Delegates to SyncNumberingService for unified numbering.
        """
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        return SyncNumberingService(db).generate_next_number(
            organization_id, SequenceType.SALARY_SLIP
        )

    @staticmethod
    def get_active_assignment(
        db: Session,
        organization_id: UUID,
        employee_id: UUID,
        as_of_date: date,
    ) -> SalaryStructureAssignment | None:
        """Get the active salary structure assignment for an employee."""
        return db.scalars(
            select(SalaryStructureAssignment)
            .where(
                SalaryStructureAssignment.organization_id == organization_id,
                SalaryStructureAssignment.employee_id == employee_id,
                SalaryStructureAssignment.from_date <= as_of_date,
                (
                    (SalaryStructureAssignment.to_date.is_(None))
                    | (SalaryStructureAssignment.to_date >= as_of_date)
                ),
            )
            .order_by(SalaryStructureAssignment.from_date.desc())
        ).first()

    @staticmethod
    def create_salary_slip(
        db: Session,
        organization_id: UUID,
        input: SalarySlipInput,
        created_by_user_id: UUID | None,
    ) -> SalarySlip:
        """
        Create a new salary slip and calculate amounts from structure.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Salary slip input data
            created_by_user_id: User creating the slip

        Returns:
            Created SalarySlip

        Raises:
            HTTPException(400): If validation fails
            HTTPException(404): If employee/structure not found
        """
        org_id = coerce_uuid(organization_id)
        emp_id = coerce_uuid(input.employee_id)
        user_id = coerce_uuid(created_by_user_id)

        # Load employee
        employee = db.get(Employee, emp_id)
        if not employee or employee.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Employee not found")

        if employee.status not in {EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot generate slip for employee with status: {employee.status.value}",
            )

        # Check for existing slip in period
        existing = db.scalar(
            select(SalarySlip).where(
                SalarySlip.organization_id == org_id,
                SalarySlip.employee_id == emp_id,
                SalarySlip.start_date == input.start_date,
                SalarySlip.end_date == input.end_date,
                SalarySlip.status != SalarySlipStatus.CANCELLED,
            )
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Salary slip already exists for this period: {existing.slip_number}",
            )

        # Get active salary structure assignment
        assignment = SalarySlipService.get_active_assignment(
            db, org_id, emp_id, input.start_date
        )

        if not assignment:
            raise HTTPException(
                status_code=400,
                detail="No active salary structure assignment for employee",
            )

        structure = db.get(SalaryStructure, assignment.structure_id)
        if not structure:
            raise HTTPException(status_code=404, detail="Salary structure not found")

        # Calculate working days
        total_working_days = input.total_working_days
        if total_working_days is None:
            # Default to calendar days in period (simplified)
            total_working_days = Decimal((input.end_date - input.start_date).days + 1)

        payment_days = total_working_days - input.absent_days - input.leave_without_pay

        # Generate slip number
        slip_number = SalarySlipService.generate_slip_number(db, org_id)

        # Create salary slip
        slip = SalarySlip(
            organization_id=org_id,
            slip_number=slip_number,
            employee_id=emp_id,
            employee_name=employee.full_name,
            structure_id=structure.structure_id,
            posting_date=input.posting_date or input.end_date,
            start_date=input.start_date,
            end_date=input.end_date,
            currency_code=structure.currency_code,
            total_working_days=total_working_days,
            absent_days=input.absent_days,
            payment_days=payment_days,
            leave_without_pay=input.leave_without_pay,
            cost_center_id=employee.cost_center_id,
            status=SalarySlipStatus.DRAFT,
            bank_name=employee.bank_name,
            bank_account_number=employee.bank_account_number,
            bank_account_name=employee.bank_account_name,
            bank_branch_code=employee.bank_branch_code,
            created_by_id=user_id,
        )

        db.add(slip)
        db.flush()  # Get slip_id

        # Calculate and add earnings from structure
        gross_pay = Decimal("0")
        basic_pay = Decimal("0")  # Track basic salary for PAYE calculation
        base_amount = assignment.base or Decimal("0")
        variable_amount = assignment.variable or Decimal("0")
        variable_amount = assignment.variable or Decimal("0")
        variable_amount = assignment.variable or Decimal("0")

        for struct_earning in structure.earnings:
            component = struct_earning.component

            # Calculate amount
            if struct_earning.amount_based_on_formula and struct_earning.formula:
                # Simple formula evaluation (in production, use safe_eval)
                amount = SalarySlipService._evaluate_formula(
                    struct_earning.formula,
                    base=base_amount,
                    variable=variable_amount,
                    gross=gross_pay,
                    payment_days=payment_days,
                    total_days=total_working_days,
                )
            else:
                amount = struct_earning.amount
                if (
                    amount == 0
                    and component.component_code == BASIC_COMPONENT_CODE
                    and (base_amount or variable_amount)
                ):
                    amount = base_amount + variable_amount

            # Pro-rate based on payment days if applicable
            if component.depends_on_payment_days and total_working_days > 0:
                amount = amount * (payment_days / total_working_days)

            # Track BASIC component for PAYE calculation
            if component.component_code == BASIC_COMPONENT_CODE:
                basic_pay = amount

            earning_line = SalarySlipEarning(
                slip_id=slip.slip_id,
                component_id=component.component_id,
                component_name=component.component_name,
                abbr=component.abbr,
                amount=amount,
                default_amount=struct_earning.amount,
                statistical_component=component.statistical_component,
                do_not_include_in_total=component.do_not_include_in_total,
                display_order=struct_earning.display_order,
            )
            db.add(earning_line)

            if (
                not component.statistical_component
                and not component.do_not_include_in_total
            ):
                gross_pay += amount

        # If no BASIC component found, use base_amount from assignment
        if basic_pay == 0:
            basic_pay = base_amount

        skip_deductions = SalarySlipService._is_contract_staff_employee(
            db, employee, structure
        )

        # Calculate PAYE and statutory deductions
        total_deduction = Decimal("0")
        paye_breakdown: PAYEBreakdown | None = None

        # Calculate PAYE tax and statutory deductions using NTA 2025 rules
        if not skip_deductions and gross_pay > 0:
            calculator = PAYECalculator(db)
            paye_breakdown = calculator.calculate(
                organization_id=org_id,
                gross_monthly=gross_pay,
                basic_monthly=basic_pay,
                employee_id=emp_id,
                as_of_date=input.start_date,
            )

            # Get or create statutory components
            statutory_components = SalarySlipService.get_statutory_components(
                db, org_id, user_id
            )

            # Add statutory deductions from PAYE calculation
            statutory_deductions = [
                (
                    PENSION_COMPONENT_CODE,
                    paye_breakdown.monthly_pension,
                    "Pension (8% of Basic)",
                    False,
                ),
                (
                    NHF_COMPONENT_CODE,
                    paye_breakdown.monthly_nhf,
                    "NHF (2.5% of Basic)",
                    False,
                ),
                (NHIS_COMPONENT_CODE, paye_breakdown.monthly_nhis, "NHIS", False),
                (PAYE_COMPONENT_CODE, paye_breakdown.monthly_tax, "PAYE Tax", False),
                (
                    EMPLOYER_PENSION_COMPONENT_CODE,
                    paye_breakdown.monthly_employer_pension,
                    "Employer Pension (10% of Basic)",
                    True,
                ),
            ]

            for code, amount, _description, is_statistical in statutory_deductions:
                if amount > 0:
                    component = statutory_components[code]
                    deduction_line = SalarySlipDeduction(
                        slip_id=slip.slip_id,
                        component_id=component.component_id,
                        component_name=component.component_name,
                        abbr=component.abbr,
                        amount=amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                        default_amount=amount,
                        statistical_component=is_statistical,
                        do_not_include_in_total=is_statistical,
                        display_order=component.display_order,
                    )
                    db.add(deduction_line)
                    if not is_statistical:
                        total_deduction += amount

        # Add non-statutory deductions from structure
        if not skip_deductions:
            for struct_deduction in structure.deductions:
                component = struct_deduction.component

                # Skip statutory components - they're calculated by PAYE
                if (
                    component.is_statutory
                    or component.component_code in STATUTORY_COMPONENT_CODES
                ):
                    continue

                # Calculate amount
                if (
                    struct_deduction.amount_based_on_formula
                    and struct_deduction.formula
                ):
                    amount = SalarySlipService._evaluate_formula(
                        struct_deduction.formula,
                        base=base_amount,
                        variable=variable_amount,
                        gross=gross_pay,
                        payment_days=payment_days,
                        total_days=total_working_days,
                    )
                else:
                    amount = struct_deduction.amount

                deduction_line = SalarySlipDeduction(
                    slip_id=slip.slip_id,
                    component_id=component.component_id,
                    component_name=component.component_name,
                    abbr=component.abbr,
                    amount=amount,
                    default_amount=struct_deduction.amount,
                    statistical_component=component.statistical_component,
                    do_not_include_in_total=component.do_not_include_in_total,
                    display_order=struct_deduction.display_order,
                )
                db.add(deduction_line)

                if (
                    not component.statistical_component
                    and not component.do_not_include_in_total
                ):
                    total_deduction += amount

        # Add unpaid suspension deduction if applicable
        from app.services.people.discipline import DisciplineService

        discipline_service = DisciplineService(db)
        unpaid_suspensions = discipline_service.get_unpaid_suspensions(
            org_id, emp_id, input.start_date, input.end_date
        )

        if unpaid_suspensions:
            # Calculate total suspension days in this period
            total_suspension_days = Decimal("0")
            for suspension in unpaid_suspensions:
                # Calculate overlap with payroll period
                susp_start = max(suspension.effective_date, input.start_date)
                susp_end = min(suspension.end_date or input.end_date, input.end_date)
                if susp_start <= susp_end:
                    days = Decimal(str((susp_end - susp_start).days + 1))
                    total_suspension_days += days

            if total_suspension_days > 0:
                # Calculate daily rate and deduction
                daily_rate = (
                    gross_pay / total_working_days
                    if total_working_days > 0
                    else Decimal("0")
                )
                suspension_deduction = (daily_rate * total_suspension_days).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

                if suspension_deduction > 0:
                    # Get or create suspension deduction component
                    susp_component = (
                        SalarySlipService.get_or_create_statutory_component(
                            db,
                            org_id,
                            "SUSPENSION_DEDUCT",
                            "Unpaid Suspension",
                            "SUSP",
                            display_order=900,
                            created_by_id=user_id,
                        )
                    )

                    deduction_line = SalarySlipDeduction(
                        slip_id=slip.slip_id,
                        component_id=susp_component.component_id,
                        component_name=susp_component.component_name,
                        abbr=susp_component.abbr,
                        amount=suspension_deduction,
                        default_amount=suspension_deduction,
                        statistical_component=False,
                        do_not_include_in_total=False,
                        display_order=900,
                    )
                    db.add(deduction_line)
                    total_deduction += suspension_deduction

                    # Mark suspensions as processed
                    for suspension in unpaid_suspensions:
                        suspension.payroll_processed = True

        # Update slip totals
        net_pay = gross_pay - total_deduction
        exchange_rate = slip.exchange_rate or Decimal("1.0")

        slip.gross_pay = gross_pay
        slip.total_deduction = total_deduction
        slip.net_pay = net_pay
        slip.gross_pay_functional = gross_pay * exchange_rate
        slip.total_deduction_functional = total_deduction * exchange_rate
        slip.net_pay_functional = net_pay * exchange_rate

        db.commit()
        db.refresh(slip)

        return slip

    @staticmethod
    def update_salary_slip(
        db: Session,
        organization_id: UUID,
        slip_id: UUID,
        input: SalarySlipInput,
        updated_by_user_id: UUID,
    ) -> SalarySlip:
        """
        Update an existing draft salary slip and recalculate amounts.

        Raises HTTPException if slip is not in draft or validation fails.
        """
        org_id = coerce_uuid(organization_id)
        s_id = coerce_uuid(slip_id)
        user_id = coerce_uuid(updated_by_user_id)

        slip = db.get(SalarySlip, s_id)
        if not slip or slip.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Salary slip not found")

        if slip.status != SalarySlipStatus.DRAFT:
            raise HTTPException(
                status_code=400, detail="Only draft slips can be edited"
            )

        emp_id = coerce_uuid(input.employee_id)
        employee = db.get(Employee, emp_id)
        if not employee or employee.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Employee not found")

        if employee.status not in {EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot generate slip for employee with status: {employee.status.value}",
            )

        existing = db.scalar(
            select(SalarySlip).where(
                SalarySlip.organization_id == org_id,
                SalarySlip.employee_id == emp_id,
                SalarySlip.start_date == input.start_date,
                SalarySlip.end_date == input.end_date,
                SalarySlip.status != SalarySlipStatus.CANCELLED,
                SalarySlip.slip_id != s_id,
            )
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Salary slip already exists for this period: {existing.slip_number}",
            )

        assignment = SalarySlipService.get_active_assignment(
            db, org_id, emp_id, input.start_date
        )
        if not assignment:
            raise HTTPException(
                status_code=400,
                detail="No active salary structure assignment for employee",
            )

        structure = db.get(SalaryStructure, assignment.structure_id)
        if not structure:
            raise HTTPException(status_code=404, detail="Salary structure not found")

        total_working_days = input.total_working_days
        if total_working_days is None:
            total_working_days = Decimal((input.end_date - input.start_date).days + 1)

        payment_days = total_working_days - input.absent_days - input.leave_without_pay

        slip.employee_id = emp_id
        slip.employee_name = employee.full_name
        slip.structure_id = structure.structure_id
        slip.posting_date = input.posting_date or input.end_date
        slip.start_date = input.start_date
        slip.end_date = input.end_date
        slip.total_working_days = total_working_days
        slip.absent_days = input.absent_days
        slip.payment_days = payment_days
        slip.leave_without_pay = input.leave_without_pay
        slip.cost_center_id = employee.cost_center_id
        slip.bank_name = employee.bank_name
        slip.bank_account_number = employee.bank_account_number
        slip.bank_account_name = employee.bank_account_name
        slip.bank_branch_code = employee.bank_branch_code
        slip.updated_by_id = user_id

        slip.earnings.clear()
        slip.deductions.clear()
        db.flush()

        gross_pay = Decimal("0")
        basic_pay = Decimal("0")
        base_amount = assignment.base or Decimal("0")
        variable_amount = assignment.variable or Decimal("0")

        for struct_earning in structure.earnings:
            component = struct_earning.component
            if struct_earning.amount_based_on_formula and struct_earning.formula:
                amount = SalarySlipService._evaluate_formula(
                    struct_earning.formula,
                    base=base_amount,
                    variable=variable_amount,
                    gross=gross_pay,
                    payment_days=payment_days,
                    total_days=total_working_days,
                )
            else:
                amount = struct_earning.amount
                if (
                    amount == 0
                    and component.component_code == BASIC_COMPONENT_CODE
                    and (base_amount or variable_amount)
                ):
                    amount = base_amount + variable_amount

            if component.depends_on_payment_days and total_working_days > 0:
                amount = amount * (payment_days / total_working_days)

            if component.component_code == BASIC_COMPONENT_CODE:
                basic_pay = amount

            slip.earnings.append(
                SalarySlipEarning(
                    slip_id=slip.slip_id,
                    component_id=component.component_id,
                    component_name=component.component_name,
                    abbr=component.abbr,
                    amount=amount,
                    default_amount=struct_earning.amount,
                    statistical_component=component.statistical_component,
                    do_not_include_in_total=component.do_not_include_in_total,
                    display_order=struct_earning.display_order,
                )
            )

            if (
                not component.statistical_component
                and not component.do_not_include_in_total
            ):
                gross_pay += amount

        if basic_pay == 0:
            basic_pay = base_amount

        skip_deductions = SalarySlipService._is_contract_staff_employee(
            db, employee, structure
        )
        total_deduction = Decimal("0")

        if not skip_deductions and gross_pay > 0:
            calculator = PAYECalculator(db)
            paye_breakdown = calculator.calculate(
                organization_id=org_id,
                gross_monthly=gross_pay,
                basic_monthly=basic_pay,
                employee_id=emp_id,
                as_of_date=input.start_date,
            )

            statutory_components = SalarySlipService.get_statutory_components(
                db, org_id, user_id
            )

            statutory_deductions = [
                (
                    PENSION_COMPONENT_CODE,
                    paye_breakdown.monthly_pension,
                    "Pension (8% of Basic)",
                    False,
                ),
                (
                    NHF_COMPONENT_CODE,
                    paye_breakdown.monthly_nhf,
                    "NHF (2.5% of Basic)",
                    False,
                ),
                (NHIS_COMPONENT_CODE, paye_breakdown.monthly_nhis, "NHIS", False),
                (PAYE_COMPONENT_CODE, paye_breakdown.monthly_tax, "PAYE Tax", False),
                (
                    EMPLOYER_PENSION_COMPONENT_CODE,
                    paye_breakdown.monthly_employer_pension,
                    "Employer Pension (10% of Basic)",
                    True,
                ),
            ]

            for code, amount, _description, is_statistical in statutory_deductions:
                if amount > 0:
                    component = statutory_components[code]
                    slip.deductions.append(
                        SalarySlipDeduction(
                            slip_id=slip.slip_id,
                            component_id=component.component_id,
                            component_name=component.component_name,
                            abbr=component.abbr,
                            amount=amount.quantize(
                                Decimal("0.01"), rounding=ROUND_HALF_UP
                            ),
                            default_amount=amount,
                            statistical_component=is_statistical,
                            do_not_include_in_total=is_statistical,
                            display_order=component.display_order,
                        )
                    )
                    if not is_statistical:
                        total_deduction += amount

        if not skip_deductions:
            for struct_deduction in structure.deductions:
                component = struct_deduction.component

                if (
                    component.is_statutory
                    or component.component_code in STATUTORY_COMPONENT_CODES
                ):
                    continue

                if (
                    struct_deduction.amount_based_on_formula
                    and struct_deduction.formula
                ):
                    amount = SalarySlipService._evaluate_formula(
                        struct_deduction.formula,
                        base=base_amount,
                        variable=variable_amount,
                        gross=gross_pay,
                        payment_days=payment_days,
                        total_days=total_working_days,
                    )
                else:
                    amount = struct_deduction.amount

                slip.deductions.append(
                    SalarySlipDeduction(
                        slip_id=slip.slip_id,
                        component_id=component.component_id,
                        component_name=component.component_name,
                        abbr=component.abbr,
                        amount=amount,
                        default_amount=struct_deduction.amount,
                        statistical_component=component.statistical_component,
                        do_not_include_in_total=component.do_not_include_in_total,
                        display_order=struct_deduction.display_order,
                    )
                )

                if (
                    not component.statistical_component
                    and not component.do_not_include_in_total
                ):
                    total_deduction += amount

        net_pay = gross_pay - total_deduction
        exchange_rate = slip.exchange_rate or Decimal("1.0")

        slip.gross_pay = gross_pay
        slip.total_deduction = total_deduction
        slip.net_pay = net_pay
        slip.gross_pay_functional = gross_pay * exchange_rate
        slip.total_deduction_functional = total_deduction * exchange_rate
        slip.net_pay_functional = net_pay * exchange_rate

        db.commit()
        db.refresh(slip)

        return slip

    @staticmethod
    def _evaluate_formula(
        formula: str,
        base: Decimal,
        variable: Decimal,
        gross: Decimal,
        payment_days: Decimal,
        total_days: Decimal,
    ) -> Decimal:
        """
        Evaluate a simple formula expression.

        Supported variables: base, gross, payment_days, total_days

        WARNING: This is a simplified implementation. In production,
        use a safe expression evaluator to prevent code injection.
        """
        try:
            # Create safe context
            {
                "base": float(base),
                "variable": float(variable),
                "gross": float(gross),
                "payment_days": float(payment_days),
                "total_days": float(total_days),
            }

            # Only allow specific patterns
            safe_formula = formula.lower().strip()

            # Handle common patterns
            if safe_formula == "base":
                return base
            elif safe_formula == "gross":
                return gross
            elif safe_formula in {"variable", "var"}:
                return variable
            elif safe_formula in {
                "base + variable",
                "base+variable",
                "base + var",
                "base+var",
            }:
                return base + variable
            elif safe_formula in {
                "base - variable",
                "base-variable",
                "base - var",
                "base-var",
            }:
                return base - variable
            elif safe_formula.startswith("base *"):
                multiplier = safe_formula.replace("base *", "").strip()
                return base * Decimal(multiplier)
            elif safe_formula.startswith("variable *") or safe_formula.startswith(
                "var *"
            ):
                multiplier = (
                    safe_formula.replace("variable *", "").replace("var *", "").strip()
                )
                return variable * Decimal(multiplier)
            elif safe_formula.startswith("gross *"):
                multiplier = safe_formula.replace("gross *", "").strip()
                return gross * Decimal(multiplier)
            else:
                # For complex formulas, default to 0 (safe fallback)
                return Decimal("0")

        except Exception:
            return Decimal("0")

    @staticmethod
    def submit_salary_slip(
        db: Session,
        organization_id: UUID,
        slip_id: UUID,
        submitted_by_user_id: UUID,
    ) -> SalarySlip:
        """Submit a DRAFT salary slip for approval."""
        org_id = coerce_uuid(organization_id)
        s_id = coerce_uuid(slip_id)
        user_id = coerce_uuid(submitted_by_user_id)

        slip = db.get(SalarySlip, s_id)
        if not slip or slip.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Salary slip not found")

        if slip.status != SalarySlipStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot submit slip with status: {slip.status.value}",
            )

        slip.status = SalarySlipStatus.SUBMITTED
        slip.status_changed_at = datetime.now(UTC)
        slip.status_changed_by_id = user_id

        db.commit()
        db.refresh(slip)

        return slip

    @staticmethod
    def approve_salary_slip(
        db: Session,
        organization_id: UUID,
        slip_id: UUID,
        approved_by_user_id: UUID,
    ) -> SalarySlip:
        """Approve a SUBMITTED salary slip."""
        org_id = coerce_uuid(organization_id)
        s_id = coerce_uuid(slip_id)
        user_id = coerce_uuid(approved_by_user_id)

        slip = db.get(SalarySlip, s_id)
        if not slip or slip.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Salary slip not found")

        if slip.status != SalarySlipStatus.SUBMITTED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve slip with status: {slip.status.value}",
            )

        # SoD check - creator cannot approve
        if slip.created_by_id == user_id:
            raise HTTPException(
                status_code=403,
                detail="Segregation of duties: creator cannot approve their own slip",
            )

        slip.status = SalarySlipStatus.APPROVED
        slip.status_changed_at = datetime.now(UTC)
        slip.status_changed_by_id = user_id

        try:
            from app.services.people.payroll.payroll_notifications import (
                PayrollNotificationService,
            )

            notification_service = PayrollNotificationService(db)
            employee = slip.employee or db.get(Employee, slip.employee_id)
            if employee:
                notification_service.notify_payslip_posted(
                    slip, employee, queue_email=True
                )
        except Exception as notify_err:
            import logging

            logging.getLogger(__name__).warning(
                "Payroll approve: failed to notify for slip %s: %s",
                slip.slip_id,
                notify_err,
            )

        db.commit()
        db.refresh(slip)

        return slip

    @staticmethod
    def get(db: Session, organization_id: UUID, slip_id: UUID) -> SalarySlip:
        """Get a salary slip by ID."""
        org_id = coerce_uuid(organization_id)
        slip = db.get(SalarySlip, coerce_uuid(slip_id))
        if not slip or slip.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Salary slip not found")
        return slip

    @staticmethod
    def list(
        db: Session,
        organization_id: UUID,
        employee_id: UUID | None = None,
        status: SalarySlipStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
        include_lines: bool = False,
    ) -> list[SalarySlip]:
        """List salary slips with filters."""
        org_id = coerce_uuid(organization_id)

        stmt = select(SalarySlip).where(SalarySlip.organization_id == org_id)
        if include_lines:
            stmt = stmt.options(
                selectinload(SalarySlip.earnings).joinedload(
                    SalarySlipEarning.component
                ),
                selectinload(SalarySlip.deductions).joinedload(
                    SalarySlipDeduction.component
                ),
            )

        if employee_id:
            stmt = stmt.where(SalarySlip.employee_id == coerce_uuid(employee_id))

        if status:
            stmt = stmt.where(SalarySlip.status == status)

        if from_date:
            stmt = stmt.where(SalarySlip.start_date >= from_date)

        if to_date:
            stmt = stmt.where(SalarySlip.end_date <= to_date)

        return db.scalars(
            stmt.order_by(SalarySlip.created_at.desc()).limit(limit).offset(offset)
        ).all()

    @staticmethod
    def count(
        db: Session,
        organization_id: UUID,
        employee_id: UUID | None = None,
        status: SalarySlipStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> int:
        """Count salary slips with filters."""
        org_id = coerce_uuid(organization_id)

        stmt = (
            select(func.count())
            .select_from(SalarySlip)
            .where(SalarySlip.organization_id == org_id)
        )

        if employee_id:
            stmt = stmt.where(SalarySlip.employee_id == coerce_uuid(employee_id))

        if status:
            stmt = stmt.where(SalarySlip.status == status)

        if from_date:
            stmt = stmt.where(SalarySlip.start_date >= from_date)

        if to_date:
            stmt = stmt.where(SalarySlip.end_date <= to_date)

        return int(db.scalar(stmt) or 0)


# Module-level singleton instance
salary_slip_service = SalarySlipService()
