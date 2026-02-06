"""
Payroll Web Service - Salary Structure operations.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from fastapi import Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.department import Department
from app.models.people.hr.designation import Designation
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.payroll.salary_assignment import SalaryStructureAssignment
from app.models.people.payroll.salary_component import SalaryComponent
from app.models.people.payroll.salary_slip import SalarySlip
from app.models.people.payroll.salary_structure import PayrollFrequency, SalaryStructure
from app.services.common import coerce_uuid
from app.services.people.payroll.payroll_service import PayrollService

logger = logging.getLogger(__name__)


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
from app.web.deps import WebAuthContext, base_context


def _normalize_form(form: Any) -> dict[str, str]:
    if form is None:
        return {}
    return {key: value if isinstance(value, str) else "" for key, value in form.items()}


from .base import (
    DEFAULT_PAGE_SIZE,
    PAYROLL_FREQUENCIES,
    parse_bool,
    parse_date,
    parse_decimal,
    parse_uuid,
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

        query = db.query(SalaryStructure).filter(
            SalaryStructure.organization_id == org_id
        )

        if search:
            query = query.filter(
                SalaryStructure.structure_name.ilike(f"%{search}%")
                | SalaryStructure.structure_code.ilike(f"%{search}%")
            )

        total = query.count()
        structures = (
            query.order_by(SalaryStructure.structure_name)
            .offset(offset)
            .limit(per_page)
            .all()
        )
        total_pages = (total + per_page - 1) // per_page

        context = base_context(request, auth, "Salary Structures", "payroll", db=db)
        context["request"] = request
        context.update(
            {
                "structures": structures,
                "search": search,
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/structures.html", context
        )

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
        context.update(
            {
                "structure": None,
                "components": components,
                "frequencies": PAYROLL_FREQUENCIES,
                "earnings_data": [{"component_id": "", "formula": ""}],
                "deductions_data": [],
                "form_data": {},
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/structure_form.html", context
        )

    def structure_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        structure_id: str,
    ) -> Response:
        """Render edit salary structure form."""
        org_id = coerce_uuid(auth.organization_id)
        s_id = parse_uuid(structure_id)

        if not s_id:
            return RedirectResponse(url="/people/payroll/structures", status_code=303)

        structure = db.get(SalaryStructure, s_id)
        if not structure or structure.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/structures", status_code=303)

        components = (
            db.query(SalaryComponent)
            .filter(
                SalaryComponent.organization_id == org_id,
                SalaryComponent.is_active == True,
            )
            .order_by(SalaryComponent.display_order)
            .all()
        )

        earnings_data = [
            {
                "component_id": str(line.component_id),
                "formula": line.formula
                if line.amount_based_on_formula
                else str(line.amount),
            }
            for line in structure.earnings
        ] or [{"component_id": "", "formula": ""}]

        deductions_data = [
            {
                "component_id": str(line.component_id),
                "formula": line.formula
                if line.amount_based_on_formula
                else str(line.amount),
            }
            for line in structure.deductions
        ]

        context = base_context(request, auth, "Edit Salary Structure", "payroll", db=db)
        context["request"] = request
        context.update(
            {
                "structure": structure,
                "components": components,
                "frequencies": PAYROLL_FREQUENCIES,
                "earnings_data": earnings_data,
                "deductions_data": deductions_data,
                "form_data": {},
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/structure_form.html", context
        )

    async def create_structure_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create new salary structure."""
        org_id = coerce_uuid(auth.organization_id)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()
        form = _normalize_form(form)

        structure_code = _safe_form_text(form.get("structure_code")).strip()
        structure_name = _safe_form_text(form.get("structure_name")).strip()
        description = _safe_form_text(form.get("description")).strip()
        frequency = _safe_form_text(
            form.get("payroll_frequency") or form.get("frequency") or "MONTHLY"
        ).strip()
        is_active = parse_bool(_safe_form_text(form.get("is_active")), True)

        try:

            def _get_list(key: str) -> list[str]:
                if hasattr(form, "getlist"):
                    return [str(v) for v in form.getlist(key)]
                return []

            earning_components = _get_list("earning_component[]") or _get_list(
                "earning_component"
            )
            earning_formulas = _get_list("earning_formula[]") or _get_list(
                "earning_formula"
            )
            deduction_components = _get_list("deduction_component[]") or _get_list(
                "deduction_component"
            )
            deduction_formulas = _get_list("deduction_formula[]") or _get_list(
                "deduction_formula"
            )

            def _build_lines(components: list[str], formulas: list[str]) -> list[dict]:
                lines: list[dict] = []
                for index, raw_component_id in enumerate(components):
                    component_id = parse_uuid(raw_component_id)
                    if not component_id:
                        continue

                    raw_formula = _safe_form_text(
                        formulas[index] if index < len(formulas) else ""
                    ).strip()
                    parsed_amount = parse_decimal(raw_formula) if raw_formula else None

                    line = {
                        "component_id": component_id,
                        "display_order": index,
                    }

                    if raw_formula and parsed_amount is None:
                        line.update(
                            {
                                "amount_based_on_formula": True,
                                "formula": raw_formula,
                            }
                        )
                    else:
                        line.update(
                            {
                                "amount": parsed_amount or Decimal("0"),
                                "amount_based_on_formula": False,
                                "formula": None,
                            }
                        )

                    lines.append(line)
                return lines

            earnings = _build_lines(earning_components, earning_formulas)
            deductions = _build_lines(deduction_components, deduction_formulas)

            svc = PayrollService(db)
            structure = svc.create_salary_structure(
                org_id,
                structure_code=structure_code,
                structure_name=structure_name,
                description=description or None,
                payroll_frequency=PayrollFrequency(frequency.upper()),
                earnings=earnings,
                deductions=deductions,
            )
            structure.is_active = is_active
            db.commit()
            return RedirectResponse(
                url=f"/people/payroll/structures/{structure.structure_id}",
                status_code=303,
            )

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

            def _form_rows(
                components_list: list[str], formulas_list: list[str]
            ) -> list[dict]:
                rows: list[dict] = []
                for index, comp_id in enumerate(components_list):
                    formula = formulas_list[index] if index < len(formulas_list) else ""
                    rows.append({"component_id": comp_id, "formula": formula})
                return rows or [{"component_id": "", "formula": ""}]

            context = base_context(
                request, auth, "New Salary Structure", "payroll", db=db
            )
            context["request"] = request
            context.update(
                {
                    "structure": None,
                    "components": components,
                    "frequencies": PAYROLL_FREQUENCIES,
                    "earnings_data": _form_rows(earning_components, earning_formulas),
                    "deductions_data": _form_rows(
                        deduction_components, deduction_formulas
                    ),
                    "form_data": {
                        "structure_code": structure_code,
                        "structure_name": structure_name,
                        "description": description,
                        "payroll_frequency": frequency,
                        "is_active": is_active,
                    },
                    "error": str(e),
                    "errors": {},
                }
            )
            return templates.TemplateResponse(
                request, "people/payroll/structure_form.html", context
            )

    def structure_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        structure_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render salary structure detail page."""
        org_id = coerce_uuid(auth.organization_id)
        s_id = parse_uuid(structure_id)

        if not s_id:
            return RedirectResponse(url="/people/payroll/structures", status_code=303)

        structure = db.get(SalaryStructure, s_id)
        if not structure or structure.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/structures", status_code=303)

        context = base_context(
            request, auth, structure.structure_name, "payroll", db=db
        )
        context["request"] = request
        context.update({"structure": structure})
        return templates.TemplateResponse(
            request, "people/payroll/structure_detail.html", context
        )

    async def update_structure_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        structure_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Update salary structure."""
        org_id = coerce_uuid(auth.organization_id)
        s_id = parse_uuid(structure_id)

        if not s_id:
            return RedirectResponse(url="/people/payroll/structures", status_code=303)

        structure = db.get(SalaryStructure, s_id)
        if not structure or structure.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/structures", status_code=303)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()
        form = _normalize_form(form)

        structure_code = _safe_form_text(form.get("structure_code")).strip()
        structure_name = _safe_form_text(form.get("structure_name")).strip()
        description = _safe_form_text(form.get("description")).strip()
        frequency = _safe_form_text(
            form.get("payroll_frequency") or form.get("frequency") or "MONTHLY"
        ).strip()
        is_active = parse_bool(_safe_form_text(form.get("is_active")), True)

        try:

            def _get_list(key: str) -> list[str]:
                if hasattr(form, "getlist"):
                    return [str(v) for v in form.getlist(key)]
                return []

            earning_components = _get_list("earning_component[]") or _get_list(
                "earning_component"
            )
            earning_formulas = _get_list("earning_formula[]") or _get_list(
                "earning_formula"
            )
            deduction_components = _get_list("deduction_component[]") or _get_list(
                "deduction_component"
            )
            deduction_formulas = _get_list("deduction_formula[]") or _get_list(
                "deduction_formula"
            )

            def _build_lines(components: list[str], formulas: list[str]) -> list[dict]:
                lines: list[dict] = []
                for index, raw_component_id in enumerate(components):
                    component_id = parse_uuid(raw_component_id)
                    if not component_id:
                        continue

                    raw_formula = _safe_form_text(
                        formulas[index] if index < len(formulas) else ""
                    ).strip()
                    parsed_amount = parse_decimal(raw_formula) if raw_formula else None

                    line = {
                        "component_id": component_id,
                        "display_order": index,
                    }

                    if raw_formula and parsed_amount is None:
                        line.update(
                            {
                                "amount_based_on_formula": True,
                                "formula": raw_formula,
                            }
                        )
                    else:
                        line.update(
                            {
                                "amount": parsed_amount or Decimal("0"),
                                "amount_based_on_formula": False,
                                "formula": None,
                            }
                        )

                    lines.append(line)
                return lines

            earnings = _build_lines(earning_components, earning_formulas)
            deductions = _build_lines(deduction_components, deduction_formulas)

            svc = PayrollService(db)
            updated = svc.update_salary_structure(
                org_id,
                structure_id=s_id,
                structure_code=structure_code,
                structure_name=structure_name,
                description=description or None,
                payroll_frequency=PayrollFrequency(frequency.upper()),
                earnings=earnings,
                deductions=deductions,
            )
            updated.is_active = is_active
            db.commit()
            return RedirectResponse(
                url=f"/people/payroll/structures/{structure_id}", status_code=303
            )

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

            def _form_rows(
                components_list: list[str], formulas_list: list[str]
            ) -> list[dict]:
                rows: list[dict] = []
                for index, comp_id in enumerate(components_list):
                    formula = formulas_list[index] if index < len(formulas_list) else ""
                    rows.append({"component_id": comp_id, "formula": formula})
                return rows or [{"component_id": "", "formula": ""}]

            context = base_context(
                request, auth, "Edit Salary Structure", "payroll", db=db
            )
            context["request"] = request
            context.update(
                {
                    "structure": structure,
                    "components": components,
                    "frequencies": PAYROLL_FREQUENCIES,
                    "earnings_data": _form_rows(earning_components, earning_formulas),
                    "deductions_data": _form_rows(
                        deduction_components, deduction_formulas
                    ),
                    "form_data": {
                        "structure_code": structure_code,
                        "structure_name": structure_name,
                        "description": description,
                        "payroll_frequency": frequency,
                        "is_active": is_active,
                    },
                    "error": str(e),
                    "errors": {},
                }
            )
            return templates.TemplateResponse(
                request, "people/payroll/structure_form.html", context
            )

    def delete_structure_response(
        self,
        auth: WebAuthContext,
        db: Session,
        structure_id: str,
    ) -> RedirectResponse:
        """Delete or deactivate salary structure."""
        org_id = coerce_uuid(auth.organization_id)
        s_id = parse_uuid(structure_id)

        if not s_id:
            return RedirectResponse(url="/people/payroll/structures", status_code=303)

        structure = db.get(SalaryStructure, s_id)
        if not structure or structure.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/structures", status_code=303)

        in_assignments = (
            db.query(SalaryStructureAssignment)
            .filter(SalaryStructureAssignment.structure_id == s_id)
            .first()
            is not None
        )
        in_slips = (
            db.query(SalarySlip).filter(SalarySlip.structure_id == s_id).first()
            is not None
        )

        try:
            if in_assignments or in_slips:
                structure.is_active = False
            else:
                db.delete(structure)
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(url="/people/payroll/structures", status_code=303)

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
        bulk_created: Optional[int] = None,
        bulk_skipped: Optional[int] = None,
    ) -> HTMLResponse | RedirectResponse:
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
            .join(
                Employee, SalaryStructureAssignment.employee_id == Employee.employee_id
            )
            .join(
                SalaryStructure,
                SalaryStructureAssignment.structure_id == SalaryStructure.structure_id,
            )
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
        context.update(
            {
                "assignments": assignments,
                "search": search,
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "bulk_created": bulk_created,
                "bulk_skipped": bulk_skipped,
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/assignments.html", context
        )

    def assignment_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: Optional[str] = None,
    ) -> HTMLResponse | RedirectResponse:
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

        context = base_context(
            request, auth, "Assign Salary Structure", "payroll", db=db
        )
        context["request"] = request
        context.update(
            {
                "assignment": None,
                "employees": employees,
                "structures": structures,
                "selected_employee": selected_employee,
                "selected_employee_id": employee_id,
                "default_from_date": date.today().isoformat(),
                "form_data": {},
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/assignment_form.html", context
        )

    async def create_assignment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create salary structure assignment."""
        org_id = coerce_uuid(auth.organization_id)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()
        form = _normalize_form(form)

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
                request,
                auth,
                db,
                str(e),
                {
                    "employee_id": employee_id,
                    "structure_id": structure_id,
                    "from_date": from_date_str,
                    "to_date": to_date_str,
                    "base": base_amount,
                    "variable": variable_amount,
                    "income_tax_slab": income_tax_slab,
                },
            )

    def assignment_bulk_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Render bulk assignment form."""
        org_id = coerce_uuid(auth.organization_id)

        departments = (
            db.query(Department)
            .filter(Department.organization_id == org_id, Department.is_active == True)
            .order_by(Department.department_name)
            .all()
        )
        designations = (
            db.query(Designation)
            .filter(
                Designation.organization_id == org_id,
                Designation.is_active == True,
                Designation.is_deleted == False,
            )
            .order_by(Designation.designation_name)
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

        context = base_context(
            request, auth, "Bulk Salary Assignment", "payroll", db=db
        )
        context["request"] = request
        context.update(
            {
                "departments": departments,
                "designations": designations,
                "structures": structures,
                "default_from_date": date.today().isoformat(),
                "form_data": {},
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/assignment_bulk_form.html", context
        )

    async def create_assignment_bulk_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create bulk salary structure assignments."""
        org_id = coerce_uuid(auth.organization_id)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()
        form = _normalize_form(form)

        department_id = _safe_form_text(form.get("department_id")).strip()
        designation_id = _safe_form_text(form.get("designation_id")).strip()
        structure_id = _safe_form_text(form.get("structure_id")).strip()
        from_date_str = _safe_form_text(form.get("from_date")).strip()
        base_amount = (form.get("base") or "0").strip()
        variable_amount = (form.get("variable") or "0").strip()
        income_tax_slab = (form.get("income_tax_slab") or "").strip()

        try:
            from_date = parse_date(from_date_str)
            if not from_date:
                raise ValueError("From date is required")
            if not structure_id:
                raise ValueError("Salary structure is required")

            emp_query = db.query(Employee.employee_id).filter(
                Employee.organization_id == org_id,
                Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
            )
            if department_id:
                emp_query = emp_query.filter(
                    Employee.department_id == parse_uuid(department_id)
                )
            if designation_id:
                emp_query = emp_query.filter(
                    Employee.designation_id == parse_uuid(designation_id)
                )
            employee_ids = [row[0] for row in emp_query.all()]

            if not employee_ids:
                raise ValueError("No employees matched the selected filters")

            active_assignments = (
                db.query(SalaryStructureAssignment.employee_id)
                .filter(
                    SalaryStructureAssignment.organization_id == org_id,
                    SalaryStructureAssignment.employee_id.in_(employee_ids),
                    SalaryStructureAssignment.from_date <= from_date,
                    or_(
                        SalaryStructureAssignment.to_date.is_(None),
                        SalaryStructureAssignment.to_date >= from_date,
                    ),
                )
                .all()
            )
            active_employee_ids = {row[0] for row in active_assignments}

            created = 0
            for employee_id in employee_ids:
                if employee_id in active_employee_ids:
                    continue
                assignment = SalaryStructureAssignment(
                    organization_id=org_id,
                    employee_id=employee_id,
                    structure_id=parse_uuid(structure_id),
                    from_date=from_date,
                    to_date=None,
                    base=parse_decimal(base_amount) or Decimal("0"),
                    variable=parse_decimal(variable_amount) or Decimal("0"),
                    income_tax_slab=income_tax_slab or None,
                )
                db.add(assignment)
                created += 1

            db.commit()
            skipped = len(employee_ids) - created
            return RedirectResponse(
                url=f"/people/payroll/assignments?bulk_created={created}&bulk_skipped={skipped}",
                status_code=303,
            )

        except Exception as e:
            db.rollback()
            return self._render_assignment_bulk_form_with_error(
                request,
                auth,
                db,
                str(e),
                {
                    "department_id": department_id,
                    "designation_id": designation_id,
                    "structure_id": structure_id,
                    "from_date": from_date_str,
                    "base": base_amount,
                    "variable": variable_amount,
                    "income_tax_slab": income_tax_slab,
                },
            )

    def _render_assignment_bulk_form_with_error(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        error: str,
        form_data: dict,
    ) -> HTMLResponse | RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)

        departments = (
            db.query(Department)
            .filter(Department.organization_id == org_id, Department.is_active == True)
            .order_by(Department.department_name)
            .all()
        )
        designations = (
            db.query(Designation)
            .filter(
                Designation.organization_id == org_id,
                Designation.is_active == True,
                Designation.is_deleted == False,
            )
            .order_by(Designation.designation_name)
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

        context = base_context(
            request, auth, "Bulk Salary Assignment", "payroll", db=db
        )
        context["request"] = request
        context.update(
            {
                "departments": departments,
                "designations": designations,
                "structures": structures,
                "default_from_date": form_data.get("from_date")
                or date.today().isoformat(),
                "form_data": form_data,
                "error": error,
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/assignment_bulk_form.html", context
        )

    def assignment_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        assignment_id: str,
    ) -> HTMLResponse | RedirectResponse:
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

        context = base_context(
            request, auth, "Edit Salary Assignment", "payroll", db=db
        )
        context["request"] = request
        context.update(
            {
                "assignment": assignment,
                "employees": employees,
                "structures": structures,
                "selected_employee": assignment.employee,
                "selected_employee_id": str(assignment.employee_id),
                "form_data": {},
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/assignment_form.html", context
        )

    async def update_assignment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        assignment_id: str,
    ) -> HTMLResponse | RedirectResponse:
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
        form = _normalize_form(form)

        structure_id = (form.get("structure_id") or "").strip()
        from_date_str = (form.get("from_date") or "").strip()
        to_date_str = (form.get("to_date") or "").strip()
        base_amount = (form.get("base") or "0").strip()
        variable_amount = (form.get("variable") or "0").strip()
        income_tax_slab = (form.get("income_tax_slab") or "").strip()

        try:
            new_structure_id = parse_uuid(structure_id)
            if new_structure_id is None:
                return RedirectResponse(
                    url="/people/payroll/assignments?error=Missing+structure",
                    status_code=303,
                )
            new_from_date = parse_date(from_date_str)
            if new_from_date is None:
                return RedirectResponse(
                    url="/people/payroll/assignments?error=Missing+from+date",
                    status_code=303,
                )
            assignment.structure_id = new_structure_id
            assignment.from_date = new_from_date
            assignment.to_date = parse_date(to_date_str) if to_date_str else None
            assignment.base = parse_decimal(base_amount) or Decimal("0")
            assignment.variable = parse_decimal(variable_amount) or Decimal("0")
            assignment.income_tax_slab = income_tax_slab or None

            db.commit()
            return RedirectResponse(url="/people/payroll/assignments", status_code=303)

        except Exception as e:
            db.rollback()
            return self._render_assignment_form_with_error(
                request,
                auth,
                db,
                str(e),
                {
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

    def delete_assignment_response(
        self,
        auth: WebAuthContext,
        db: Session,
        assignment_id: str,
    ) -> RedirectResponse:
        """Delete salary structure assignment."""
        org_id = coerce_uuid(auth.organization_id)
        a_id = parse_uuid(assignment_id)

        if not a_id:
            return RedirectResponse(url="/people/payroll/assignments", status_code=303)

        assignment = db.get(SalaryStructureAssignment, a_id)
        if not assignment or assignment.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/assignments", status_code=303)

        try:
            db.delete(assignment)
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(url="/people/payroll/assignments", status_code=303)

    def _render_assignment_form_with_error(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        error: str,
        form_data: dict,
        assignment: Optional[SalaryStructureAssignment] = None,
    ) -> HTMLResponse | RedirectResponse:
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
        context.update(
            {
                "assignment": assignment,
                "employees": employees,
                "structures": structures,
                "selected_employee": assignment.employee if assignment else None,
                "selected_employee_id": form_data.get("employee_id")
                or (str(assignment.employee_id) if assignment else None),
                "default_from_date": date.today().isoformat(),
                "form_data": form_data,
                "error": error,
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/assignment_form.html", context
        )
