"""
Payroll Web Service - Salary Structure operations.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import Request, UploadFile, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.payroll.salary_component import SalaryComponent
from app.models.people.payroll.salary_structure import SalaryStructure, PayrollFrequency
from app.models.people.payroll.salary_assignment import SalaryStructureAssignment
from app.services.common import coerce_uuid


def _safe_form_text(value: object) -> str:
    """Normalize form values to text for safe parsing."""
    if value is None:
        return ""
    if isinstance(value, UploadFile):
        return ""
    if isinstance(value, str):
        return value
    return str(value)
from app.templates import templates
from app.web.deps import base_context, WebAuthContext

from .base import (
    DEFAULT_PAGE_SIZE,
    parse_uuid,
    parse_date,
    parse_decimal,
    parse_bool,
    parse_payroll_frequency,
    PAYROLL_FREQUENCIES,
)


class StructureWebService:
    """Service for salary structure web views."""

    def list_structures_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str] = None,
        page: int = 1,
    ) -> Response:
        """Render salary structures list page."""
        org_id = coerce_uuid(auth.organization_id)
        per_page = DEFAULT_PAGE_SIZE
        offset = (page - 1) * per_page

        query = db.query(SalaryStructure).filter(SalaryStructure.organization_id == org_id)

        if search:
            query = query.filter(
                SalaryStructure.structure_name.ilike(f"%{search}%")
                | SalaryStructure.structure_code.ilike(f"%{search}%")
            )

        total = query.count()
        structures = query.order_by(SalaryStructure.structure_name).offset(offset).limit(per_page).all()
        total_pages = (total + per_page - 1) // per_page

        context = base_context(request, auth, "Salary Structures", "payroll", db=db)
        context["request"] = request
        context.update({
            "structures": structures,
            "search": search,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        })
        return templates.TemplateResponse(request, "people/payroll/structures.html", context)

    def structure_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> Response:
        """Render new salary structure form."""
        org_id = coerce_uuid(auth.organization_id)

        components = (
            db.query(SalaryComponent)
            .filter(
                SalaryComponent.organization_id == org_id,
                SalaryComponent.is_active == True,
            )
            .order_by(SalaryComponent.display_order)
            .all()
        )

        context = base_context(request, auth, "New Salary Structure", "payroll", db=db)
        context["request"] = request
        context.update({
            "structure": None,
            "components": components,
            "frequencies": PAYROLL_FREQUENCIES,
            "form_data": {},
            "errors": {},
        })
        return templates.TemplateResponse(request, "people/payroll/structure_form.html", context)

    async def create_structure_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Create new salary structure."""
        org_id = coerce_uuid(auth.organization_id)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        structure_code = _safe_form_text(form.get("structure_code")).strip()
        structure_name = _safe_form_text(form.get("structure_name")).strip()
        description = _safe_form_text(form.get("description")).strip()
        frequency = _safe_form_text(form.get("frequency") or "MONTHLY").strip()
        is_active = parse_bool(_safe_form_text(form.get("is_active")), True)

        try:
            structure = SalaryStructure(
                organization_id=org_id,
                structure_code=structure_code,
                structure_name=structure_name,
                description=description or None,
                payroll_frequency=PayrollFrequency(frequency.upper()),
                is_active=is_active,
            )

            db.add(structure)
            db.commit()
            return RedirectResponse(url=f"/people/payroll/structures/{structure.structure_id}", status_code=303)

        except Exception as e:
            db.rollback()

            components = (
                db.query(SalaryComponent)
                .filter(
                    SalaryComponent.organization_id == org_id,
                    SalaryComponent.is_active == True,
                )
                .order_by(SalaryComponent.display_order)
                .all()
            )

            context = base_context(request, auth, "New Salary Structure", "payroll", db=db)
            context["request"] = request
            context.update({
                "structure": None,
                "components": components,
                "frequencies": PAYROLL_FREQUENCIES,
                "form_data": {
                    "structure_code": structure_code,
                    "structure_name": structure_name,
                    "description": description,
                    "frequency": frequency,
                    "is_active": is_active,
                },
                "error": str(e),
                "errors": {},
            })
            return templates.TemplateResponse(request, "people/payroll/structure_form.html", context)

    def structure_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        structure_id: str,
    ) -> HTMLResponse:
        """Render salary structure detail page."""
        org_id = coerce_uuid(auth.organization_id)
        s_id = parse_uuid(structure_id)

        if not s_id:
            return RedirectResponse(url="/people/payroll/structures", status_code=303)

        structure = db.get(SalaryStructure, s_id)
        if not structure or structure.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/structures", status_code=303)

        context = base_context(request, auth, structure.structure_name, "payroll", db=db)
        context["request"] = request
        context.update({"structure": structure})
        return templates.TemplateResponse(request, "people/payroll/structure_detail.html", context)

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
                Employee.employee_code.ilike(f"%{search}%")
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

    def assignment_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: Optional[str] = None,
    ) -> HTMLResponse:
        """Render new assignment form."""
        org_id = coerce_uuid(auth.organization_id)

        selected_employee = None
        if employee_id:
            selected_employee = db.get(Employee, parse_uuid(employee_id))

        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == org_id,
                Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
            )
            .order_by(Employee.employee_code)
            .all()
        )

        structures = (
            db.query(SalaryStructure)
            .filter(
                SalaryStructure.organization_id == org_id,
                SalaryStructure.is_active == True,
            )
            .order_by(SalaryStructure.structure_name)
            .all()
        )

        context = base_context(request, auth, "Assign Salary Structure", "payroll", db=db)
        context["request"] = request
        context.update({
            "assignment": None,
            "employees": employees,
            "structures": structures,
            "selected_employee": selected_employee,
            "selected_employee_id": employee_id,
            "default_from_date": date.today().isoformat(),
            "form_data": {},
            "errors": {},
        })
        return templates.TemplateResponse(request, "people/payroll/assignment_form.html", context)

    async def create_assignment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Create salary structure assignment."""
        org_id = coerce_uuid(auth.organization_id)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        employee_id = _safe_form_text(form.get("employee_id")).strip()
        structure_id = _safe_form_text(form.get("structure_id")).strip()
        from_date_str = _safe_form_text(form.get("from_date")).strip()
        to_date_str = _safe_form_text(form.get("to_date")).strip()
        base_amount = (form.get("base") or "0").strip()
        variable_amount = (form.get("variable") or "0").strip()
        income_tax_slab = (form.get("income_tax_slab") or "").strip()

        try:
            from_date = parse_date(from_date_str)
            to_date = parse_date(to_date_str) if to_date_str else None

            # End any existing current assignment
            existing = (
                db.query(SalaryStructureAssignment)
                .filter(
                    SalaryStructureAssignment.organization_id == org_id,
                    SalaryStructureAssignment.employee_id == parse_uuid(employee_id),
                    SalaryStructureAssignment.to_date.is_(None),
                )
                .first()
            )
            if existing and from_date:
                existing.to_date = from_date - timedelta(days=1)

            assignment = SalaryStructureAssignment(
                organization_id=org_id,
                employee_id=parse_uuid(employee_id),
                structure_id=parse_uuid(structure_id),
                from_date=from_date,
                to_date=to_date,
                base=parse_decimal(base_amount) or Decimal("0"),
                variable=parse_decimal(variable_amount) or Decimal("0"),
                income_tax_slab=income_tax_slab or None,
            )

            db.add(assignment)
            db.commit()
            return RedirectResponse(url="/people/payroll/assignments", status_code=303)

        except Exception as e:
            db.rollback()
            return self._render_assignment_form_with_error(
                request, auth, db, str(e), {
                    "employee_id": employee_id,
                    "structure_id": structure_id,
                    "from_date": from_date_str,
                    "to_date": to_date_str,
                    "base": base_amount,
                    "variable": variable_amount,
                    "income_tax_slab": income_tax_slab,
                }
            )

    def assignment_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        assignment_id: str,
    ) -> HTMLResponse:
        """Render edit assignment form."""
        org_id = coerce_uuid(auth.organization_id)
        a_id = parse_uuid(assignment_id)

        if not a_id:
            return RedirectResponse(url="/people/payroll/assignments", status_code=303)

        assignment = (
            db.query(SalaryStructureAssignment)
            .options(joinedload(SalaryStructureAssignment.employee))
            .filter(SalaryStructureAssignment.assignment_id == a_id)
            .first()
        )

        if not assignment or assignment.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/assignments", status_code=303)

        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == org_id,
                Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
            )
            .order_by(Employee.employee_code)
            .all()
        )

        structures = (
            db.query(SalaryStructure)
            .filter(
                SalaryStructure.organization_id == org_id,
                SalaryStructure.is_active == True,
            )
            .order_by(SalaryStructure.structure_name)
            .all()
        )

        context = base_context(request, auth, "Edit Salary Assignment", "payroll", db=db)
        context["request"] = request
        context.update({
            "assignment": assignment,
            "employees": employees,
            "structures": structures,
            "selected_employee": assignment.employee,
            "selected_employee_id": str(assignment.employee_id),
            "form_data": {},
            "errors": {},
        })
        return templates.TemplateResponse(request, "people/payroll/assignment_form.html", context)

    async def update_assignment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        assignment_id: str,
    ) -> HTMLResponse:
        """Update salary structure assignment."""
        org_id = coerce_uuid(auth.organization_id)
        a_id = parse_uuid(assignment_id)

        if not a_id:
            return RedirectResponse(url="/people/payroll/assignments", status_code=303)

        assignment = db.get(SalaryStructureAssignment, a_id)
        if not assignment or assignment.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/assignments", status_code=303)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        structure_id = (form.get("structure_id") or "").strip()
        from_date_str = (form.get("from_date") or "").strip()
        to_date_str = (form.get("to_date") or "").strip()
        base_amount = (form.get("base") or "0").strip()
        variable_amount = (form.get("variable") or "0").strip()
        income_tax_slab = (form.get("income_tax_slab") or "").strip()

        try:
            assignment.structure_id = parse_uuid(structure_id)
            assignment.from_date = parse_date(from_date_str)
            assignment.to_date = parse_date(to_date_str) if to_date_str else None
            assignment.base = parse_decimal(base_amount) or Decimal("0")
            assignment.variable = parse_decimal(variable_amount) or Decimal("0")
            assignment.income_tax_slab = income_tax_slab or None

            db.commit()
            return RedirectResponse(url="/people/payroll/assignments", status_code=303)

        except Exception as e:
            db.rollback()
            return self._render_assignment_form_with_error(
                request, auth, db, str(e), {
                    "structure_id": structure_id,
                    "from_date": from_date_str,
                    "to_date": to_date_str,
                    "base": base_amount,
                    "variable": variable_amount,
                    "income_tax_slab": income_tax_slab,
                },
                assignment=assignment,
            )

    def end_assignment_response(
        self,
        auth: WebAuthContext,
        db: Session,
        assignment_id: str,
        end_date: Optional[str] = None,
    ) -> RedirectResponse:
        """End salary structure assignment."""
        org_id = coerce_uuid(auth.organization_id)
        a_id = parse_uuid(assignment_id)

        if a_id:
            assignment = db.get(SalaryStructureAssignment, a_id)
            if assignment and assignment.organization_id == org_id:
                assignment.to_date = parse_date(end_date) or date.today()
                db.commit()

        return RedirectResponse(url="/people/payroll/assignments", status_code=303)

    def _render_assignment_form_with_error(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        error: str,
        form_data: dict,
        assignment: Optional[SalaryStructureAssignment] = None,
    ) -> HTMLResponse:
        """Render assignment form with error."""
        org_id = coerce_uuid(auth.organization_id)

        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == org_id,
                Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
            )
            .order_by(Employee.employee_code)
            .all()
        )

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
            "selected_employee": assignment.employee if assignment else None,
            "selected_employee_id": form_data.get("employee_id") or (str(assignment.employee_id) if assignment else None),
            "default_from_date": date.today().isoformat(),
            "form_data": form_data,
            "error": error,
            "errors": {},
        })
        return templates.TemplateResponse(request, "people/payroll/assignment_form.html", context)
