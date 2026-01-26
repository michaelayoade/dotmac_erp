"""
SalarySlipService - Salary slip lifecycle management.

Handles creation, calculation, and workflow for salary slips.
Integrates with PAYECalculator for NTA 2025 tax computation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
from uuid import UUID
import uuid as uuid_lib

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.people.payroll.salary_slip import (
    SalarySlip,
    SalarySlipEarning,
    SalarySlipDeduction,
    SalarySlipStatus,
)
from app.models.people.payroll.salary_component import SalaryComponent, SalaryComponentType
from app.models.people.payroll.salary_structure import SalaryStructure
from app.models.people.payroll.salary_assignment import SalaryStructureAssignment
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.hr.employment_type import EmploymentType
from app.services.common import coerce_uuid
from app.services.people.payroll.paye_calculator import PAYECalculator, PAYEBreakdown


# Standard component codes for statutory deductions
BASIC_COMPONENT_CODE = "BASIC"
PENSION_COMPONENT_CODE = "PENSION"
NHF_COMPONENT_CODE = "NHF"
NHIS_COMPONENT_CODE = "NHIS"
PAYE_COMPONENT_CODE = "PAYE"

STATUTORY_COMPONENT_CODES = {
    PENSION_COMPONENT_CODE,
    NHF_COMPONENT_CODE,
    NHIS_COMPONENT_CODE,
    PAYE_COMPONENT_CODE,
}


@dataclass
class SalarySlipInput:
    """Input for creating a salary slip."""

    employee_id: UUID
    start_date: date
    end_date: date
    posting_date: Optional[date] = None
    total_working_days: Optional[Decimal] = None
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

        type_code = (employment_type.type_code or "").strip().lower() if employment_type else ""
        type_name = (employment_type.type_name or "").strip().lower() if employment_type else ""
        is_contract = type_code == "contract" or type_name == "contract"
        is_contract_structure = (structure.structure_name or "").strip().lower() == "contract staff"
        return is_contract and is_contract_structure

    @staticmethod
    def get_or_create_statutory_component(
        db: Session,
        organization_id: UUID,
        component_code: str,
        component_name: str,
        abbr: str,
        display_order: int = 100,
        created_by_id: Optional[UUID] = None,
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
        component = (
            db.query(SalaryComponent)
            .filter(
                SalaryComponent.organization_id == org_id,
                SalaryComponent.component_code == component_code,
            )
            .first()
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
        created_by_id: Optional[UUID] = None,
    ) -> dict[str, SalaryComponent]:
        """
        Get or create all statutory components needed for PAYE calculation.

        Returns:
            Dictionary mapping component codes to SalaryComponent objects
        """
        org_id = coerce_uuid(organization_id)
        components = {}

        # Define statutory components
        statutory_defs = [
            (PENSION_COMPONENT_CODE, "Pension Contribution", "PEN", 101),
            (NHF_COMPONENT_CODE, "National Housing Fund", "NHF", 102),
            (NHIS_COMPONENT_CODE, "National Health Insurance", "NHIS", 103),
            (PAYE_COMPONENT_CODE, "Pay As You Earn Tax", "PAYE", 104),
        ]

        for code, name, abbr, order in statutory_defs:
            component = SalarySlipService.get_or_create_statutory_component(
                db, org_id, code, name, abbr, order, created_by_id
            )
            components[code] = component

        return components

    @staticmethod
    def generate_slip_number(db: Session, organization_id: UUID) -> str:
        """Generate a unique slip number."""
        # Simple sequential numbering - can be enhanced with fiscal year prefix
        from sqlalchemy import func

        count = (
            db.query(func.count(SalarySlip.slip_id))
            .filter(SalarySlip.organization_id == organization_id)
            .scalar()
        ) or 0

        return f"SLIP-{datetime.now().year}-{(count + 1):05d}"

    @staticmethod
    def get_active_assignment(
        db: Session,
        organization_id: UUID,
        employee_id: UUID,
        as_of_date: date,
    ) -> Optional[SalaryStructureAssignment]:
        """Get the active salary structure assignment for an employee."""
        return (
            db.query(SalaryStructureAssignment)
            .filter(
                SalaryStructureAssignment.organization_id == organization_id,
                SalaryStructureAssignment.employee_id == employee_id,
                SalaryStructureAssignment.from_date <= as_of_date,
                (
                    (SalaryStructureAssignment.to_date.is_(None))
                    | (SalaryStructureAssignment.to_date >= as_of_date)
                ),
            )
            .order_by(SalaryStructureAssignment.from_date.desc())
            .first()
        )

    @staticmethod
    def create_salary_slip(
        db: Session,
        organization_id: UUID,
        input: SalarySlipInput,
        created_by_user_id: UUID,
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
                detail=f"Cannot generate slip for employee with status: {employee.status.value}"
            )

        # Check for existing slip in period
        existing = (
            db.query(SalarySlip)
            .filter(
                SalarySlip.organization_id == org_id,
                SalarySlip.employee_id == emp_id,
                SalarySlip.start_date == input.start_date,
                SalarySlip.end_date == input.end_date,
                SalarySlip.status != SalarySlipStatus.CANCELLED,
            )
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Salary slip already exists for this period: {existing.slip_number}"
            )

        # Get active salary structure assignment
        assignment = SalarySlipService.get_active_assignment(
            db, org_id, emp_id, input.start_date
        )

        if not assignment:
            raise HTTPException(
                status_code=400,
                detail="No active salary structure assignment for employee"
            )

        structure = db.get(SalaryStructure, assignment.structure_id)
        if not structure:
            raise HTTPException(
                status_code=404,
                detail="Salary structure not found"
            )

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
            created_by_id=user_id,
        )

        db.add(slip)
        db.flush()  # Get slip_id

        # Calculate and add earnings from structure
        gross_pay = Decimal("0")
        basic_pay = Decimal("0")  # Track basic salary for PAYE calculation
        base_amount = assignment.base or Decimal("0")

        for struct_earning in structure.earnings:
            component = struct_earning.component

            # Calculate amount
            if struct_earning.amount_based_on_formula and struct_earning.formula:
                # Simple formula evaluation (in production, use safe_eval)
                amount = SalarySlipService._evaluate_formula(
                    struct_earning.formula,
                    base=base_amount,
                    gross=gross_pay,
                    payment_days=payment_days,
                    total_days=total_working_days,
                )
            else:
                amount = struct_earning.amount

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

            if not component.statistical_component and not component.do_not_include_in_total:
                gross_pay += amount

        # If no BASIC component found, use base_amount from assignment
        if basic_pay == 0:
            basic_pay = base_amount

        skip_deductions = SalarySlipService._is_contract_staff_employee(db, employee, structure)

        # Calculate PAYE and statutory deductions
        total_deduction = Decimal("0")
        paye_breakdown: Optional[PAYEBreakdown] = None

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
                (PENSION_COMPONENT_CODE, paye_breakdown.monthly_pension, "Pension (8% of Basic)"),
                (NHF_COMPONENT_CODE, paye_breakdown.monthly_nhf, "NHF (2.5% of Basic)"),
                (NHIS_COMPONENT_CODE, paye_breakdown.monthly_nhis, "NHIS"),
                (PAYE_COMPONENT_CODE, paye_breakdown.monthly_tax, "PAYE Tax"),
            ]

            for code, amount, description in statutory_deductions:
                if amount > 0:
                    component = statutory_components[code]
                    deduction_line = SalarySlipDeduction(
                        slip_id=slip.slip_id,
                        component_id=component.component_id,
                        component_name=component.component_name,
                        abbr=component.abbr,
                        amount=amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                        default_amount=amount,
                        statistical_component=False,
                        do_not_include_in_total=False,
                        display_order=component.display_order,
                    )
                    db.add(deduction_line)
                    total_deduction += amount

        # Add non-statutory deductions from structure
        if not skip_deductions:
            for struct_deduction in structure.deductions:
                component = struct_deduction.component

                # Skip statutory components - they're calculated by PAYE
                if component.is_statutory or component.component_code in STATUTORY_COMPONENT_CODES:
                    continue

                # Calculate amount
                if struct_deduction.amount_based_on_formula and struct_deduction.formula:
                    amount = SalarySlipService._evaluate_formula(
                        struct_deduction.formula,
                        base=base_amount,
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

                if not component.statistical_component and not component.do_not_include_in_total:
                    total_deduction += amount

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
    def _evaluate_formula(
        formula: str,
        base: Decimal,
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
            context = {
                "base": float(base),
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
            elif safe_formula.startswith("base *"):
                multiplier = safe_formula.replace("base *", "").strip()
                return base * Decimal(multiplier)
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
                detail=f"Cannot submit slip with status: {slip.status.value}"
            )

        slip.status = SalarySlipStatus.SUBMITTED
        slip.status_changed_at = datetime.now(timezone.utc)
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
                detail=f"Cannot approve slip with status: {slip.status.value}"
            )

        # SoD check - creator cannot approve
        if slip.created_by_id == user_id:
            raise HTTPException(
                status_code=403,
                detail="Segregation of duties: creator cannot approve their own slip"
            )

        slip.status = SalarySlipStatus.APPROVED
        slip.status_changed_at = datetime.now(timezone.utc)
        slip.status_changed_by_id = user_id

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
        employee_id: Optional[UUID] = None,
        status: Optional[SalarySlipStatus] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SalarySlip]:
        """List salary slips with filters."""
        org_id = coerce_uuid(organization_id)

        query = db.query(SalarySlip).filter(SalarySlip.organization_id == org_id)

        if employee_id:
            query = query.filter(SalarySlip.employee_id == coerce_uuid(employee_id))

        if status:
            query = query.filter(SalarySlip.status == status)

        if from_date:
            query = query.filter(SalarySlip.start_date >= from_date)

        if to_date:
            query = query.filter(SalarySlip.end_date <= to_date)

        return (
            query
            .order_by(SalarySlip.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    @staticmethod
    def count(
        db: Session,
        organization_id: UUID,
        employee_id: Optional[UUID] = None,
        status: Optional[SalarySlipStatus] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
    ) -> int:
        """Count salary slips with filters."""
        org_id = coerce_uuid(organization_id)

        query = db.query(SalarySlip).filter(SalarySlip.organization_id == org_id)

        if employee_id:
            query = query.filter(SalarySlip.employee_id == coerce_uuid(employee_id))

        if status:
            query = query.filter(SalarySlip.status == status)

        if from_date:
            query = query.filter(SalarySlip.start_date >= from_date)

        if to_date:
            query = query.filter(SalarySlip.end_date <= to_date)

        return query.count()


# Module-level singleton instance
salary_slip_service = SalarySlipService()
