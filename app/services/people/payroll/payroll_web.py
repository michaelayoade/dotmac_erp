"""Payroll Web Service - Web view methods for payroll.

Provides view-focused data and operations for payroll web routes.
All business logic should be here; routes should be thin wrappers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.hr.employment_type import EmploymentType
from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.models.people.payroll.salary_assignment import SalaryStructureAssignment
from app.models.people.payroll.salary_component import SalaryComponent, SalaryComponentType
from app.models.people.payroll.salary_slip import SalarySlip
from app.models.people.payroll.salary_structure import SalaryStructure
from app.models.people.payroll.tax_band import TaxBand
from app.services.common import coerce_uuid
from app.services.people.payroll.paye_calculator import PAYECalculator
from app.templates import templates
from app.web.deps import base_context, WebAuthContext


DEFAULT_PAGE_SIZE = 20


@dataclass
class AssignmentCreateData:
    """Data for creating a salary structure assignment."""

    employee_id: UUID
    structure_id: UUID
    from_date: date
    to_date: Optional[date] = None
    base: Decimal = Decimal("0")
    variable: Decimal = Decimal("0")
    income_tax_slab: Optional[str] = None


@dataclass
class AssignmentUpdateData:
    """Data for updating a salary structure assignment."""

    structure_id: UUID
    from_date: date
    to_date: Optional[date] = None
    base: Decimal = Decimal("0")
    variable: Decimal = Decimal("0")
    income_tax_slab: Optional[str] = None


class PayrollWebService:
    """Service for payroll web views."""

    # =========================================================================
    # Salary Structure Assignments
    # =========================================================================

    def list_assignments_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render salary assignments list page."""
        org_id = coerce_uuid(auth.organization_id)
        per_page = DEFAULT_PAGE_SIZE
        offset = (page - 1) * per_page

        query = (
            db.query(SalaryStructureAssignment)
            .options(
                joinedload(SalaryStructureAssignment.employee),
                joinedload(SalaryStructureAssignment.salary_structure),
            )
            .filter(SalaryStructureAssignment.organization_id == org_id)
            .join(Employee, SalaryStructureAssignment.employee_id == Employee.employee_id)
            .join(SalaryStructure, SalaryStructureAssignment.structure_id == SalaryStructure.structure_id)
        )

        if search:
            query = query.filter(
                Employee.full_name.ilike(f"%{search}%")
                | SalaryStructure.structure_name.ilike(f"%{search}%")
            )

        total = query.count()
        assignments = (
            query.order_by(SalaryStructureAssignment.from_date.desc())
            .offset(offset)
            .limit(per_page)
            .all()
        )

        total_pages = (total + per_page - 1) // per_page

        context = base_context(request, auth, "Salary Assignments", "payroll", db=db)
        context["request"] = request
        context.update({
            "assignments": assignments,
            "search": search,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        })
        return templates.TemplateResponse(request, "people/payroll/assignments.html", context)

    def assignment_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        assignment_id: Optional[str] = None,
        employee_id: Optional[str] = None,
    ) -> HTMLResponse:
        """Render salary assignment form (new or edit)."""
        org_id = coerce_uuid(auth.organization_id)

        assignment = None
        selected_employee = None

        if assignment_id:
            # Edit mode - load existing assignment
            assignment = (
                db.query(SalaryStructureAssignment)
                .options(joinedload(SalaryStructureAssignment.employee))
                .filter(SalaryStructureAssignment.assignment_id == coerce_uuid(assignment_id))
                .first()
            )
            if not assignment or assignment.organization_id != org_id:
                return RedirectResponse(url="/people/payroll/assignments", status_code=303)
            selected_employee = assignment.employee
        elif employee_id:
            # New mode with pre-selected employee
            selected_employee = db.get(Employee, coerce_uuid(employee_id))

        # Get active employees
        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == org_id,
                Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
            )
            .order_by(Employee.employee_code)
            .all()
        )

        # Get active salary structures
        structures = (
            db.query(SalaryStructure)
            .filter(
                SalaryStructure.organization_id == org_id,
                SalaryStructure.is_active == True,
            )
            .order_by(SalaryStructure.structure_name)
            .all()
        )

        title = "Edit Salary Assignment" if assignment else "Assign Salary Structure"
        context = base_context(request, auth, title, "payroll", db=db)
        context["request"] = request
        context.update({
            "assignment": assignment,
            "employees": employees,
            "structures": structures,
            "selected_employee": selected_employee,
            "selected_employee_id": employee_id or (str(assignment.employee_id) if assignment else None),
            "default_from_date": date.today().isoformat(),
        })
        return templates.TemplateResponse(request, "people/payroll/assignment_form.html", context)

    def create_assignment(
        self,
        db: Session,
        org_id: UUID,
        data: AssignmentCreateData,
    ) -> SalaryStructureAssignment:
        """Create a new salary structure assignment.

        Automatically ends any existing current assignment for the employee.
        """
        # End any existing current assignment for this employee
        existing = (
            db.query(SalaryStructureAssignment)
            .filter(
                SalaryStructureAssignment.organization_id == org_id,
                SalaryStructureAssignment.employee_id == data.employee_id,
                SalaryStructureAssignment.to_date.is_(None),
            )
            .first()
        )
        if existing and data.from_date:
            # End the previous assignment the day before the new one starts
            existing.to_date = data.from_date - timedelta(days=1)

        assignment = SalaryStructureAssignment(
            organization_id=org_id,
            employee_id=data.employee_id,
            structure_id=data.structure_id,
            from_date=data.from_date,
            to_date=data.to_date,
            base=data.base,
            variable=data.variable,
            income_tax_slab=data.income_tax_slab,
        )

        db.add(assignment)
        return assignment

    def update_assignment(
        self,
        db: Session,
        org_id: UUID,
        assignment_id: UUID,
        data: AssignmentUpdateData,
    ) -> Optional[SalaryStructureAssignment]:
        """Update an existing salary structure assignment."""
        assignment = db.get(SalaryStructureAssignment, assignment_id)
        if not assignment or assignment.organization_id != org_id:
            return None

        assignment.structure_id = data.structure_id
        assignment.from_date = data.from_date
        assignment.to_date = data.to_date
        assignment.base = data.base
        assignment.variable = data.variable
        assignment.income_tax_slab = data.income_tax_slab

        return assignment

    def end_assignment(
        self,
        db: Session,
        org_id: UUID,
        assignment_id: UUID,
        end_date: date,
    ) -> Optional[SalaryStructureAssignment]:
        """End a salary structure assignment by setting to_date."""
        assignment = db.get(SalaryStructureAssignment, assignment_id)
        if not assignment or assignment.organization_id != org_id:
            return None

        assignment.to_date = end_date
        return assignment

    # =========================================================================
    # Salary Slips
    # =========================================================================

    def slip_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        slip_id: str,
    ) -> HTMLResponse:
        """Render salary slip detail page with PAYE breakdown."""
        org_id = coerce_uuid(auth.organization_id)
        s_id = coerce_uuid(slip_id)

        slip = db.get(SalarySlip, s_id)
        if not slip or slip.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/slips", status_code=303)

        # Calculate PAYE breakdown for display
        paye_breakdown = None
        tax_profile = None
        skip_deductions = False

        employee = db.get(Employee, slip.employee_id) if slip.employee_id else None
        structure = db.get(SalaryStructure, slip.structure_id) if slip.structure_id else None
        if employee and structure:
            employment_type = employee.employment_type
            if employment_type is None and employee.employment_type_id:
                employment_type = db.get(EmploymentType, employee.employment_type_id)

            type_code = (employment_type.type_code or "").strip().lower() if employment_type else ""
            type_name = (employment_type.type_name or "").strip().lower() if employment_type else ""
            is_contract = type_code == "contract" or type_name == "contract"
            is_contract_structure = (structure.structure_name or "").strip().lower() == "contract staff"
            skip_deductions = is_contract and is_contract_structure

        if slip.employee_id:
            tax_profile = (
                db.query(EmployeeTaxProfile)
                .filter(
                    EmployeeTaxProfile.organization_id == org_id,
                    EmployeeTaxProfile.employee_id == slip.employee_id,
                    EmployeeTaxProfile.effective_to.is_(None),
                )
                .first()
            )

            # Calculate PAYE breakdown if we have gross pay
            if slip.gross_pay > 0 and not skip_deductions:
                calculator = PAYECalculator(db)
                # Estimate basic as 60% of gross (common structure) for breakdown display
                basic_estimate = slip.gross_pay * Decimal("0.6")

                paye_breakdown = calculator.calculate(
                    organization_id=org_id,
                    gross_monthly=slip.gross_pay,
                    basic_monthly=basic_estimate,
                    annual_rent=tax_profile.annual_rent if tax_profile else Decimal("0"),
                    rent_verified=tax_profile.rent_receipt_verified if tax_profile else False,
                    pension_rate=tax_profile.pension_rate if tax_profile else Decimal("0.08"),
                    nhf_rate=tax_profile.nhf_rate if tax_profile else Decimal("0.025"),
                    nhis_rate=tax_profile.nhis_rate if tax_profile else Decimal("0"),
                )

        context = base_context(request, auth, "Salary Slip", "payroll", db=db)
        context["request"] = request
        context.update({
            "slip": slip,
            "paye_breakdown": paye_breakdown,
            "tax_profile": tax_profile,
        })
        return templates.TemplateResponse(request, "people/payroll/slip_detail.html", context)

    # =========================================================================
    # Tax Bands
    # =========================================================================

    def list_tax_bands_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render tax bands list page."""
        org_id = coerce_uuid(auth.organization_id)

        bands = (
            db.query(TaxBand)
            .filter(TaxBand.organization_id == org_id, TaxBand.is_active == True)
            .order_by(TaxBand.sequence)
            .all()
        )

        context = base_context(request, auth, "Tax Bands (NTA 2025)", "payroll", db=db)
        context["request"] = request
        context.update({"bands": bands})
        return templates.TemplateResponse(request, "people/payroll/tax_bands.html", context)

    def tax_calculator_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render PAYE tax calculator page."""
        org_id = coerce_uuid(auth.organization_id)

        bands = (
            db.query(TaxBand)
            .filter(TaxBand.organization_id == org_id, TaxBand.is_active == True)
            .order_by(TaxBand.sequence)
            .all()
        )

        context = base_context(request, auth, "PAYE Calculator", "payroll", db=db)
        context["request"] = request
        context.update({"bands": bands})
        return templates.TemplateResponse(request, "people/payroll/tax_calculator.html", context)

    # =========================================================================
    # Tax Profiles
    # =========================================================================

    def list_tax_profiles_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        page: int = 1,
    ) -> HTMLResponse:
        """Render tax profiles list page."""
        org_id = coerce_uuid(auth.organization_id)
        per_page = DEFAULT_PAGE_SIZE
        offset = (page - 1) * per_page

        query = (
            db.query(EmployeeTaxProfile)
            .options(joinedload(EmployeeTaxProfile.employee))
            .filter(
                EmployeeTaxProfile.organization_id == org_id,
                EmployeeTaxProfile.effective_to.is_(None),
            )
        )

        total = query.count()
        profiles = query.offset(offset).limit(per_page).all()
        total_pages = (total + per_page - 1) // per_page

        context = base_context(request, auth, "Tax Profiles", "payroll", db=db)
        context["request"] = request
        context.update({
            "profiles": profiles,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        })
        return templates.TemplateResponse(request, "people/payroll/tax_profiles.html", context)

    def tax_profile_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: Optional[str] = None,
        is_edit: bool = False,
    ) -> HTMLResponse:
        """Render tax profile form (new or edit)."""
        org_id = coerce_uuid(auth.organization_id)

        profile = None
        selected_employee = None

        if employee_id:
            emp_id = coerce_uuid(employee_id)
            selected_employee = db.get(Employee, emp_id)

            if is_edit:
                profile = (
                    db.query(EmployeeTaxProfile)
                    .filter(
                        EmployeeTaxProfile.organization_id == org_id,
                        EmployeeTaxProfile.employee_id == emp_id,
                        EmployeeTaxProfile.effective_to.is_(None),
                    )
                    .first()
                )
                if not profile:
                    return RedirectResponse(url="/people/payroll/tax/profiles", status_code=303)

        # Get employees without tax profiles (for new form)
        existing_profile_ids = (
            db.query(EmployeeTaxProfile.employee_id)
            .filter(
                EmployeeTaxProfile.organization_id == org_id,
                EmployeeTaxProfile.effective_to.is_(None),
            )
        )

        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == org_id,
                Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
            )
        )
        if not is_edit:
            employees = employees.filter(~Employee.employee_id.in_(existing_profile_ids))
        employees = employees.order_by(Employee.employee_code).all()

        title = "Edit Tax Profile" if is_edit else "New Tax Profile"
        context = base_context(request, auth, title, "payroll", db=db)
        context["request"] = request
        context.update({
            "profile": profile,
            "employees": employees,
            "selected_employee": selected_employee,
            "selected_employee_id": employee_id,
            "is_edit": is_edit,
        })
        return templates.TemplateResponse(request, "people/payroll/tax_profile_form.html", context)


# Singleton instance
payroll_web_service = PayrollWebService()
