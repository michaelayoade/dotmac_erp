"""HR Web Service - Employee and organization web view methods.

Provides view-focused data and operations for HR web routes.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from starlette.datastructures import UploadFile

from app.models.auth import Session as AuthSession
from app.models.auth import SessionStatus, UserCredential
from app.models.finance.core_org.cost_center import CostCenter
from app.models.finance.core_org.location import Location
from app.models.people.hr import (
    Department,
    Designation,
    Employee,
    EmployeeGrade,
    EmployeeStatus,
    EmploymentType,
)
from app.models.people.hr.employee import SalaryMode
from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.models.people.payroll.salary_assignment import SalaryStructureAssignment
from app.models.person import Gender, Person
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.attendance.attendance_service import AttendanceService
from app.services.people.hr import (
    DepartmentFilters,
    DesignationFilters,
    EmployeeCreateData,
    EmployeeFilters,
    EmployeeGradeFilters,
    EmployeeService,
    EmployeeUpdateData,
    EmploymentTypeFilters,
    OrganizationService,
    TerminationData,
)
from app.services.people.hr.web.constants import DEFAULT_PAGE_SIZE, DROPDOWN_LIMIT
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class HRWebService:
    """Service for HR web views."""

    # =========================================================================
    # Employees
    # =========================================================================

    def list_employees_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        status: str | None = None,
        department_id: str | None = None,
        designation_id: str | None = None,
        date_of_joining_from: str | None = None,
        date_of_joining_to: str | None = None,
        date_of_leaving_from: str | None = None,
        date_of_leaving_to: str | None = None,
        page: int = 1,
        success: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        """Render employee list page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        org_svc = OrganizationService(db, org_id)

        # Parse status filter
        status_filter = None
        if status:
            try:
                status_filter = EmployeeStatus(status.upper())
            except ValueError:
                pass

        filters = EmployeeFilters(
            search=search,
            status=status_filter,
            department_id=coerce_uuid(department_id) if department_id else None,
            designation_id=coerce_uuid(designation_id) if designation_id else None,
            date_of_joining_from=self._parse_date(date_of_joining_from or ""),
            date_of_joining_to=self._parse_date(date_of_joining_to or ""),
            date_of_leaving_from=self._parse_date(date_of_leaving_from or ""),
            date_of_leaving_to=self._parse_date(date_of_leaving_to or ""),
        )
        pagination = PaginationParams.from_page(page, DEFAULT_PAGE_SIZE)

        # Use eager_load=True to avoid N+1 queries (loads person, dept, desig in bulk)
        result = svc.list_employees(filters, pagination, eager_load=True)
        stats = svc.get_employee_stats()

        # Get departments for filter dropdown
        dept_result = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        )
        departments = dept_result.items

        desig_result = org_svc.list_designations(
            DesignationFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        )
        designations = desig_result.items

        manager_result = svc.list_employees(
            EmployeeFilters(include_deleted=False),
            PaginationParams(limit=DROPDOWN_LIMIT),
            eager_load=True,
        )
        managers = []
        for mgr in manager_result.items:
            person = mgr.person
            full_name = (
                f"{person.first_name or ''} {person.last_name or ''}".strip()
                if person
                else ""
            )
            managers.append(
                {
                    "employee_id": mgr.employee_id,
                    "employee_code": mgr.employee_code,
                    "full_name": full_name,
                }
            )

        # Build employee view data - relationships already loaded via eager_load
        employees_view = []
        for emp in result.items:
            person = emp.person
            dept = emp.department
            desig = emp.designation
            status_value = emp.status.value if emp.status else "UNKNOWN"

            employees_view.append(
                {
                    "employee_id": emp.employee_id,
                    "employee_code": emp.employee_code,
                    "person_name": f"{person.first_name or ''} {person.last_name or ''}".strip()
                    if person
                    else "",
                    "email": person.email if person else "",
                    "department_name": dept.department_name if dept else "",
                    "designation_name": desig.designation_name if desig else "",
                    "date_of_joining": emp.date_of_joining,
                    "status": status_value,
                    "status_class": self._status_class(emp.status),
                }
            )

        context = {
            **base_context(request, auth, "Employees", "employees"),
            "employees": employees_view,
            "stats": stats,
            "departments": departments,
            "designations": designations,
            "managers": managers,
            "search": search or "",
            "status": status or "",
            "department_id": department_id or "",
            "designation_id": designation_id or "",
            "date_of_joining_from": date_of_joining_from or "",
            "date_of_joining_to": date_of_joining_to or "",
            "date_of_leaving_from": date_of_leaving_from or "",
            "date_of_leaving_to": date_of_leaving_to or "",
            "page": page,
            "total_pages": result.total_pages,
            "total_count": result.total,
            "total": result.total,
            "limit": pagination.limit,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
            "success": success,
            "error": error,
        }

        return templates.TemplateResponse(
            request,
            "people/hr/employees.html",
            context,
        )

    def employee_stats_response(
        self,
        auth: WebAuthContext,
        db: Session,
    ) -> dict:
        """Return employee stats for dashboard widgets."""
        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        return svc.get_employee_stats()

    async def create_employee_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse | HTMLResponse:
        """Handle new employee form submission."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        # Person fields
        first_name = self._form_str(form, "first_name")
        last_name = self._form_str(form, "last_name")
        email = self._form_str(form, "email")
        phone = self._form_str(form, "phone")
        date_of_birth = self._form_str(form, "date_of_birth")
        gender = self._form_str(form, "gender")
        address_line1 = self._form_str(form, "address_line1")
        address_line2 = self._form_str(form, "address_line2")
        city = self._form_str(form, "city")
        region = self._form_str(form, "region")
        postal_code = self._form_str(form, "postal_code")
        country_code = self._form_str(form, "country_code")
        # Employee fields
        employee_code = self._form_str(form, "employee_code")
        department_id = self._form_str(form, "department_id")
        designation_id = self._form_str(form, "designation_id")
        employment_type_id = self._form_str(form, "employment_type_id")
        grade_id = self._form_str(form, "grade_id")
        reports_to_id = self._form_str(form, "reports_to_id")
        expense_approver_id = self._form_str(form, "expense_approver_id")
        assigned_location_id = self._form_str(form, "assigned_location_id")
        default_shift_type_id = self._form_str(form, "default_shift_type_id")
        linked_person_id = self._form_str(form, "linked_person_id")
        cost_center_id = self._form_str(form, "cost_center_id")
        date_of_joining = self._form_str(form, "date_of_joining")
        probation_end_date = self._form_str(form, "probation_end_date")
        confirmation_date = self._form_str(form, "confirmation_date")
        notes = self._form_str(form, "notes")
        status = self._form_str(form, "status") or "DRAFT"
        # Personal contact & emergency
        personal_email = self._form_str(form, "personal_email")
        personal_phone = self._form_str(form, "personal_phone")
        emergency_contact_name = self._form_str(form, "emergency_contact_name")
        emergency_contact_phone = self._form_str(form, "emergency_contact_phone")
        # Bank details
        bank_name = self._form_str(form, "bank_name")
        bank_account_name = self._form_str(form, "bank_account_name")
        bank_account_number = self._form_str(form, "bank_account_number")
        bank_branch_code = self._form_str(form, "bank_branch_code")
        ctc_raw = self._form_str(form, "ctc")
        salary_mode_raw = self._form_str(form, "salary_mode")
        ctc = self._parse_decimal(ctc_raw)
        salary_mode = self._parse_salary_mode(salary_mode_raw)
        ctc_raw = self._form_str(form, "ctc")
        salary_mode_raw = self._form_str(form, "salary_mode")
        ctc = self._parse_decimal(ctc_raw)
        salary_mode = self._parse_salary_mode(salary_mode_raw)
        ctc_raw = self._form_str(form, "ctc")
        salary_mode_raw = self._form_str(form, "salary_mode")
        ctc = self._parse_decimal(ctc_raw)
        salary_mode = self._parse_salary_mode(salary_mode_raw)
        ctc = self._parse_decimal(self._form_str(form, "ctc"))
        salary_mode = self._parse_salary_mode(self._form_str(form, "salary_mode"))
        ctc_raw = self._form_str(form, "ctc")
        salary_mode_raw = self._form_str(form, "salary_mode")
        ctc = self._parse_decimal(ctc_raw)
        salary_mode = self._parse_salary_mode(salary_mode_raw)

        if (
            not linked_person_id and (not first_name or not last_name or not email)
        ) or not date_of_joining:
            errors = {
                "first_name": "Required" if not first_name else "",
                "last_name": "Required" if not last_name else "",
                "email": "Required" if not email else "",
                "date_of_joining": "Required" if not date_of_joining else "",
            }
            return self.employee_new_form_response(
                request,
                auth,
                db,
                error="First name, last name, email, and date of joining are required.",
                form_data={
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "phone": phone,
                    "date_of_birth": date_of_birth,
                    "gender": gender,
                    "address_line1": address_line1,
                    "address_line2": address_line2,
                    "city": city,
                    "region": region,
                    "postal_code": postal_code,
                    "country_code": country_code,
                    "employee_code": employee_code,
                    "department_id": department_id,
                    "designation_id": designation_id,
                    "employment_type_id": employment_type_id,
                    "grade_id": grade_id,
                    "reports_to_id": reports_to_id,
                    "expense_approver_id": expense_approver_id,
                    "assigned_location_id": assigned_location_id,
                    "default_shift_type_id": default_shift_type_id,
                    "linked_person_id": linked_person_id,
                    "cost_center_id": cost_center_id,
                    "date_of_joining": date_of_joining,
                    "probation_end_date": probation_end_date,
                    "status": status,
                    "bank_name": bank_name,
                    "bank_account_name": bank_account_name,
                    "bank_account_number": bank_account_number,
                    "bank_branch_code": bank_branch_code,
                    "ctc": ctc_raw,
                    "salary_mode": salary_mode_raw,
                    "notes": notes,
                },
                errors=errors,
            )

        org_id = coerce_uuid(auth.organization_id)

        joining_date = self._parse_date(date_of_joining)
        dob = self._parse_date(date_of_birth)
        probation_date = self._parse_date(probation_end_date)
        confirm_date = self._parse_date(confirmation_date)

        # Parse status
        status_enum = EmployeeStatus.DRAFT
        if status:
            try:
                status_enum = EmployeeStatus(status.upper())
            except ValueError:
                pass

        person: Person | None = None

        # Check if person with this email already exists
        existing_person = (
            db.query(Person)
            .filter(
                Person.email == email,
                Person.organization_id == org_id,
            )
            .first()
        )

        if existing_person:
            # Check if they already have an employee record
            svc = EmployeeService(db, org_id)
            existing_emp = svc.get_employee_by_person(existing_person.id)
            if existing_emp:
                return self.employee_new_form_response(
                    request,
                    auth,
                    db,
                    error=f"A person with email '{email}' already has an employee record.",
                    form_data={
                        "first_name": first_name,
                        "last_name": last_name,
                        "email": email,
                        "employee_code": employee_code,
                        "department_id": department_id,
                        "designation_id": designation_id,
                        "assigned_location_id": assigned_location_id,
                        "default_shift_type_id": default_shift_type_id,
                        "expense_approver_id": expense_approver_id,
                        "linked_person_id": linked_person_id,
                        "date_of_joining": date_of_joining,
                        "status": status,
                        "bank_name": bank_name,
                        "bank_account_name": bank_account_name,
                        "bank_account_number": bank_account_number,
                        "bank_branch_code": bank_branch_code,
                        "ctc": ctc_raw,
                        "salary_mode": salary_mode_raw,
                    },
                )
            person = existing_person
        else:
            if linked_person_id:
                person = db.get(Person, coerce_uuid(linked_person_id))
                if not person or person.organization_id != org_id:
                    return self.employee_new_form_response(
                        request,
                        auth,
                        db,
                        error="Selected user account not found for this organization.",
                        form_data={
                            "first_name": first_name,
                            "last_name": last_name,
                            "email": email,
                            "phone": phone,
                            "date_of_birth": date_of_birth,
                            "gender": gender,
                            "address_line1": address_line1,
                            "address_line2": address_line2,
                            "city": city,
                            "region": region,
                            "postal_code": postal_code,
                            "country_code": country_code,
                            "employee_code": employee_code,
                            "department_id": department_id,
                            "designation_id": designation_id,
                            "employment_type_id": employment_type_id,
                            "grade_id": grade_id,
                            "reports_to_id": reports_to_id,
                            "expense_approver_id": expense_approver_id,
                            "assigned_location_id": assigned_location_id,
                            "default_shift_type_id": default_shift_type_id,
                            "linked_person_id": linked_person_id,
                            "cost_center_id": cost_center_id,
                            "date_of_joining": date_of_joining,
                            "probation_end_date": probation_end_date,
                            "status": status,
                            "bank_name": bank_name,
                            "bank_account_name": bank_account_name,
                            "bank_account_number": bank_account_number,
                            "bank_branch_code": bank_branch_code,
                            "ctc": ctc_raw,
                            "salary_mode": salary_mode_raw,
                            "notes": notes,
                        },
                    )
            else:
                # Create new Person
                person = Person(
                    organization_id=org_id,
                    first_name=first_name,
                    last_name=last_name,
                    email=email.lower(),
                    phone=phone or None,
                    date_of_birth=dob,
                    gender=Gender(gender) if gender else Gender.unknown,
                    address_line1=address_line1 or None,
                    address_line2=address_line2 or None,
                    city=city or None,
                    region=region or None,
                    postal_code=postal_code or None,
                    country_code=country_code or None,
                )
                db.add(person)
                db.flush()

        # Create Employee linked to Person
        svc = EmployeeService(db, org_id)
        data = EmployeeCreateData(
            employee_number=employee_code if employee_code else None,
            department_id=coerce_uuid(department_id) if department_id else None,
            designation_id=coerce_uuid(designation_id) if designation_id else None,
            employment_type_id=coerce_uuid(employment_type_id)
            if employment_type_id
            else None,
            grade_id=coerce_uuid(grade_id) if grade_id else None,
            reports_to_id=coerce_uuid(reports_to_id) if reports_to_id else None,
            expense_approver_id=coerce_uuid(expense_approver_id)
            if expense_approver_id
            else None,
            assigned_location_id=coerce_uuid(assigned_location_id)
            if assigned_location_id
            else None,
            default_shift_type_id=coerce_uuid(default_shift_type_id)
            if default_shift_type_id
            else None,
            cost_center_id=coerce_uuid(cost_center_id) if cost_center_id else None,
            date_of_joining=joining_date,
            probation_end_date=probation_date,
            confirmation_date=confirm_date,
            status=status_enum,
            personal_email=personal_email or None,
            personal_phone=personal_phone or None,
            emergency_contact_name=emergency_contact_name or None,
            emergency_contact_phone=emergency_contact_phone or None,
            bank_name=bank_name,
            bank_account_name=bank_account_name,
            bank_account_number=bank_account_number,
            bank_sort_code=bank_branch_code,
            ctc=ctc,
            salary_mode=salary_mode,
            notes=notes or None,
        )

        if person is None:
            raise HTTPException(status_code=400, detail="Person not found")
        employee = svc.create_employee(person.id, data)
        db.commit()

        return RedirectResponse(
            url=f"/people/hr/employees/{employee.employee_id}?saved=1",
            status_code=303,
        )

    async def update_employee_response(
        self,
        request: Request,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Handle employee update form submission."""
        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        employee_code = self._form_str(form, "employee_code")
        department_id = self._form_str(form, "department_id")
        designation_id = self._form_str(form, "designation_id")
        employment_type_id = self._form_str(form, "employment_type_id")
        grade_id = self._form_str(form, "grade_id")
        reports_to_id = self._form_str(form, "reports_to_id")
        expense_approver_id = self._form_str(form, "expense_approver_id")
        assigned_location_id = self._form_str(form, "assigned_location_id")
        default_shift_type_id = self._form_str(form, "default_shift_type_id")
        linked_person_id = self._form_str(form, "linked_person_id")
        cost_center_id = self._form_str(form, "cost_center_id")
        date_of_joining = self._form_str(form, "date_of_joining")
        probation_end_date = self._form_str(form, "probation_end_date")
        confirmation_date = self._form_str(form, "confirmation_date")
        notes = self._form_str(form, "notes")
        status = self._form_str(form, "status")
        # Personal contact & emergency
        personal_email = self._form_str(form, "personal_email")
        personal_phone = self._form_str(form, "personal_phone")
        emergency_contact_name = self._form_str(form, "emergency_contact_name")
        emergency_contact_phone = self._form_str(form, "emergency_contact_phone")
        # Bank details
        bank_name = self._form_str(form, "bank_name")
        bank_account_name = self._form_str(form, "bank_account_name")
        bank_account_number = self._form_str(form, "bank_account_number")
        bank_branch_code = self._form_str(form, "bank_branch_code")
        ctc_raw = self._form_str(form, "ctc")
        salary_mode_raw = self._form_str(form, "salary_mode")
        ctc = self._parse_decimal(ctc_raw)
        salary_mode = self._parse_salary_mode(salary_mode_raw)

        status_enum = None
        if status:
            try:
                status_enum = EmployeeStatus(status.upper())
            except ValueError:
                pass

        joining_date = self._parse_date(date_of_joining)
        probation_date = self._parse_date(probation_end_date)
        confirm_date = self._parse_date(confirmation_date)

        provided_fields = {
            "employee_number",
            "department_id",
            "designation_id",
            "employment_type_id",
            "grade_id",
            "reports_to_id",
            "expense_approver_id",
            "cost_center_id",
            "assigned_location_id",
            "default_shift_type_id",
            "date_of_joining",
            "probation_end_date",
            "confirmation_date",
            "status",
            "personal_email",
            "personal_phone",
            "emergency_contact_name",
            "emergency_contact_phone",
            "bank_name",
            "bank_account_name",
            "bank_account_number",
            "bank_sort_code",
            "ctc",
            "salary_mode",
            "notes",
        }

        data = EmployeeUpdateData(
            employee_number=employee_code if employee_code else None,
            department_id=coerce_uuid(department_id) if department_id else None,
            designation_id=coerce_uuid(designation_id) if designation_id else None,
            employment_type_id=coerce_uuid(employment_type_id)
            if employment_type_id
            else None,
            grade_id=coerce_uuid(grade_id) if grade_id else None,
            reports_to_id=coerce_uuid(reports_to_id) if reports_to_id else None,
            expense_approver_id=coerce_uuid(expense_approver_id)
            if expense_approver_id
            else None,
            assigned_location_id=coerce_uuid(assigned_location_id)
            if assigned_location_id
            else None,
            default_shift_type_id=coerce_uuid(default_shift_type_id)
            if default_shift_type_id
            else None,
            cost_center_id=coerce_uuid(cost_center_id) if cost_center_id else None,
            date_of_joining=joining_date,
            probation_end_date=probation_date,
            confirmation_date=confirm_date,
            status=status_enum,
            personal_email=personal_email or None,
            personal_phone=personal_phone or None,
            emergency_contact_name=emergency_contact_name or None,
            emergency_contact_phone=emergency_contact_phone or None,
            bank_name=bank_name or None,
            bank_account_name=bank_account_name or None,
            bank_account_number=bank_account_number or None,
            bank_sort_code=bank_branch_code or None,
            ctc=ctc,
            salary_mode=salary_mode,
            notes=notes or None,
            provided_fields=provided_fields,
        )

        if linked_person_id:
            svc.link_employee_to_person(
                coerce_uuid(employee_id),
                coerce_uuid(linked_person_id),
            )

        svc.update_employee(coerce_uuid(employee_id), data)
        db.commit()

        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}?saved=1",
            status_code=303,
        )

    def activate_employee_response(
        self,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Activate an employee."""
        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        svc.activate_employee(employee_id)
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}?saved=1", status_code=303
        )

    async def suspend_employee_response(
        self,
        request: Request,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Suspend an employee."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()
        reason = self._form_str(form, "reason")

        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        svc.suspend_employee(employee_id, reason=reason or None)
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}?saved=1", status_code=303
        )

    def set_employee_on_leave_response(
        self,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Set an employee on leave."""
        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        svc.set_on_leave(employee_id)
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}?saved=1", status_code=303
        )

    async def resign_employee_response(
        self,
        request: Request,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse | HTMLResponse:
        """Record employee resignation."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()
        date_of_leaving = self._form_str(form, "date_of_leaving")

        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)

        leaving_date = self._parse_date(date_of_leaving)

        if leaving_date:
            svc.resign_employee(employee_id, leaving_date)
            db.commit()
            return RedirectResponse(
                url=f"/people/hr/employees/{employee_id}?saved=1", status_code=303
            )

        employee = svc.get_employee(employee_id)
        context = self._employee_detail_context(request, auth, db, employee)
        context.update(
            {
                "employee": employee,
                "error": "Please provide a valid resignation date.",
            }
        )
        return templates.TemplateResponse(
            request, "people/hr/employee_detail.html", context
        )

    async def terminate_employee_response(
        self,
        request: Request,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse | HTMLResponse:
        """Terminate an employee."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()
        date_of_leaving = self._form_str(form, "date_of_leaving")
        reason = self._form_str(form, "reason")

        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)

        leaving_date = self._parse_date(date_of_leaving)

        if leaving_date:
            svc.terminate_employee(
                employee_id,
                TerminationData(
                    date_of_leaving=leaving_date,
                    reason=reason or None,
                ),
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/hr/employees/{employee_id}?saved=1", status_code=303
            )

        employee = svc.get_employee(employee_id)
        context = self._employee_detail_context(request, auth, db, employee)
        context.update(
            {
                "employee": employee,
                "error": "Please provide a valid termination date.",
            }
        )
        return templates.TemplateResponse(
            request, "people/hr/employee_detail.html", context
        )

    async def toggle_user_credential_response(
        self,
        request: Request,
        employee_id: UUID,
        credential_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse | HTMLResponse:
        """Enable/disable a user credential linked to an employee."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        employee = svc.get_employee(employee_id)

        if not employee.person_id:
            context = self._employee_detail_context(request, auth, db, employee)
            context.update(
                {
                    "employee": employee,
                    "error": "This employee is not linked to a user account.",
                }
            )
            return templates.TemplateResponse(
                request, "people/hr/employee_detail.html", context
            )

        credential = (
            db.query(UserCredential)
            .filter(UserCredential.id == credential_id)
            .filter(UserCredential.person_id == employee.person_id)
            .first()
        )
        if not credential:
            context = self._employee_detail_context(request, auth, db, employee)
            context.update(
                {
                    "employee": employee,
                    "error": "User credential not found for this employee.",
                }
            )
            return templates.TemplateResponse(
                request, "people/hr/employee_detail.html", context
            )

        credential.is_active = not bool(credential.is_active)

        # If disabling, revoke active sessions for immediate lockout.
        if not credential.is_active:
            now = datetime.now(UTC)
            active_sessions = (
                db.query(AuthSession)
                .filter(AuthSession.person_id == employee.person_id)
                .filter(AuthSession.status == SessionStatus.active)
                .filter(AuthSession.revoked_at.is_(None))
                .filter(AuthSession.expires_at > now)
                .all()
            )
            session_ids = [s.id for s in active_sessions]
            for session in active_sessions:
                session.status = SessionStatus.revoked
                session.revoked_at = now

            db.commit()

            if session_ids:
                from app.services.auth_dependencies import invalidate_session_cache

                for session_id in session_ids:
                    invalidate_session_cache(session_id)
        else:
            db.commit()

        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}?saved=1",
            status_code=303,
        )

    def _employee_detail_context(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee: Employee,
    ) -> dict:
        """Build employee detail page context."""
        org_id = coerce_uuid(auth.organization_id)

        person = db.get(Person, employee.person_id)
        dept = (
            db.get(Department, employee.department_id)
            if employee.department_id
            else None
        )
        desig = (
            db.get(Designation, employee.designation_id)
            if employee.designation_id
            else None
        )
        grade = db.get(EmployeeGrade, employee.grade_id) if employee.grade_id else None
        emp_type = (
            db.get(EmploymentType, employee.employment_type_id)
            if employee.employment_type_id
            else None
        )
        manager = None
        if employee.reports_to_id:
            manager_emp = db.scalar(
                select(Employee).where(
                    Employee.employee_id == employee.reports_to_id,
                    Employee.organization_id == org_id,
                    Employee.is_deleted == False,
                )
            )
            if manager_emp:
                manager_person = db.get(Person, manager_emp.person_id)
                manager = {
                    "employee_id": manager_emp.employee_id,
                    "name": (
                        f"{manager_person.first_name or ''} {manager_person.last_name or ''}".strip()
                        if manager_person
                        else ""
                    ),
                }
        expense_approver = None
        if employee.expense_approver_id:
            approver_emp = db.scalar(
                select(Employee).where(
                    Employee.employee_id == employee.expense_approver_id,
                    Employee.organization_id == org_id,
                    Employee.is_deleted == False,
                )
            )
            if approver_emp:
                approver_person = db.get(Person, approver_emp.person_id)
                expense_approver = {
                    "employee_id": approver_emp.employee_id,
                    "name": (
                        f"{approver_person.first_name or ''} {approver_person.last_name or ''}".strip()
                        if approver_person
                        else ""
                    ),
                }

        credentials = []
        if employee.person_id:
            credentials = (
                db.query(UserCredential)
                .filter(UserCredential.person_id == employee.person_id)
                .order_by(UserCredential.created_at.asc())
                .all()
            )

        # Fetch salary structure assignments for this employee (eager load structure)
        salary_assignments = (
            db.query(SalaryStructureAssignment)
            .options(joinedload(SalaryStructureAssignment.salary_structure))
            .filter(
                SalaryStructureAssignment.organization_id == org_id,
                SalaryStructureAssignment.employee_id == employee.employee_id,
            )
            .order_by(SalaryStructureAssignment.from_date.desc())
            .all()
        )

        # Fetch tax profile for this employee
        tax_profile = (
            db.query(EmployeeTaxProfile)
            .filter(
                EmployeeTaxProfile.organization_id == org_id,
                EmployeeTaxProfile.employee_id == employee.employee_id,
                EmployeeTaxProfile.effective_to.is_(None),
            )
            .first()
        )

        # Fetch onboarding record for this employee
        from app.services.people.hr.lifecycle import LifecycleService

        lifecycle_svc = LifecycleService(db)
        onboarding = lifecycle_svc.get_onboarding_for_employee(
            org_id, employee.employee_id
        )

        return {
            **base_context(request, auth, "Employee Details", "employees"),
            "employee": employee,
            "person": person,
            "department": dept,
            "designation": desig,
            "grade": grade,
            "employment_type": emp_type,
            "manager": manager,
            "expense_approver": expense_approver,
            "credentials": credentials,
            "salary_assignments": salary_assignments,
            "tax_profile": tax_profile,
            "onboarding": onboarding,
        }

    def employee_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: str,
        saved: bool = False,
    ) -> HTMLResponse:
        """Render employee detail page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)

        employee = svc.get_employee(coerce_uuid(employee_id))
        context = self._employee_detail_context(request, auth, db, employee)
        context["saved"] = saved

        return templates.TemplateResponse(
            request,
            "people/hr/employee_detail.html",
            context,
        )

    def org_chart_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render organization chart page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        chart = svc.get_org_chart()

        context = {
            **base_context(request, auth, "Org Chart", "employees"),
            "chart": chart,
        }

        return templates.TemplateResponse(
            request,
            "people/hr/org_chart.html",
            context,
        )

    def employee_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        error: str | None = None,
        form_data: dict | None = None,
        errors: dict | None = None,
    ) -> HTMLResponse:
        """Render new employee form.

        Args:
            request: FastAPI request.
            auth: Authentication context.
            db: Database session.
            error: Top-level error message to display.
            form_data: Previously submitted form data (for re-populating on error).
            errors: Field-level validation errors.
        """
        org_id = coerce_uuid(auth.organization_id)
        org_svc = OrganizationService(db, org_id)

        # Get dropdown options
        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
        designations = org_svc.list_designations(
            DesignationFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
        employment_types = org_svc.list_employment_types(
            EmploymentTypeFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
        grades = org_svc.list_employee_grades(
            EmployeeGradeFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
        managers = (
            EmployeeService(db, org_id)
            .list_employees(
                EmployeeFilters(status=EmployeeStatus.ACTIVE),
                PaginationParams(limit=DROPDOWN_LIMIT),
                eager_load=True,
            )
            .items
        )
        cost_centers = (
            db.query(CostCenter)
            .filter(
                CostCenter.organization_id == org_id,
                CostCenter.is_active.is_(True),
            )
            .order_by(CostCenter.cost_center_code)
            .all()
        )
        locations = (
            db.query(Location)
            .filter(
                Location.organization_id == org_id,
                Location.is_active.is_(True),
            )
            .order_by(Location.location_name)
            .all()
        )
        shift_types = (
            AttendanceService(db)
            .list_shift_types(
                org_id,
                is_active=True,
                pagination=PaginationParams(limit=DROPDOWN_LIMIT),
            )
            .items
        )
        user_rows = (
            db.query(UserCredential, Person)
            .join(Person, UserCredential.person_id == Person.id)
            .filter(Person.organization_id == org_id)
            .order_by(Person.first_name, Person.last_name)
            .all()
        )
        user_options = {}
        for cred, user_person in user_rows:
            label = f"{user_person.name} ({user_person.email})"
            if cred.username:
                label = f"{label} - {cred.username}"
            user_options[str(user_person.id)] = {
                "person_id": user_person.id,
                "label": label,
            }
        user_accounts = list(user_options.values())

        context = {
            **base_context(request, auth, "New Employee", "employees"),
            "employee": None,
            "person": None,
            "departments": departments,
            "designations": designations,
            "employment_types": employment_types,
            "grades": grades,
            "managers": managers,
            "cost_centers": cost_centers,
            "locations": locations,
            "shift_types": shift_types,
            "user_accounts": user_accounts,
            "statuses": [s.value for s in EmployeeStatus],
            "salary_modes": [m.value for m in SalaryMode],
            "genders": [g.value for g in Gender],
            "error": error,
            "errors": errors or {},
            "form_data": form_data or {},
        }

        return templates.TemplateResponse(
            request,
            "people/hr/employee_form.html",
            context,
        )

    @staticmethod
    def _form_str(form: Any, key: str) -> str:
        """Normalize form value to a trimmed string."""
        value = form.get(key) if form is not None else None
        if value is None or isinstance(value, UploadFile):
            return ""
        return str(value).strip()

    @staticmethod
    def _parse_date(value: str) -> date | None:
        """Parse a date string in YYYY-MM-DD format."""
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None

    @staticmethod
    def _parse_decimal(value: str) -> Decimal | None:
        """Parse a decimal value from form input."""
        if not value:
            return None
        try:
            return Decimal(value.replace(",", ""))
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _parse_salary_mode(value: str) -> SalaryMode | None:
        """Parse salary mode enum from form input."""
        if not value:
            return None
        try:
            return SalaryMode(value.upper())
        except ValueError:
            return None

    def employee_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: str,
    ) -> HTMLResponse:
        """Render edit employee form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        org_svc = OrganizationService(db, org_id)

        employee = svc.get_employee(coerce_uuid(employee_id))
        person = db.get(Person, employee.person_id)

        # Get dropdown options
        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
        designations = org_svc.list_designations(
            DesignationFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
        employment_types = org_svc.list_employment_types(
            EmploymentTypeFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
        grades = org_svc.list_employee_grades(
            EmployeeGradeFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
        managers = (
            EmployeeService(db, org_id)
            .list_employees(
                EmployeeFilters(status=EmployeeStatus.ACTIVE),
                PaginationParams(limit=DROPDOWN_LIMIT),
                eager_load=True,
            )
            .items
        )
        cost_centers = (
            db.query(CostCenter)
            .filter(
                CostCenter.organization_id == org_id,
                CostCenter.is_active.is_(True),
            )
            .order_by(CostCenter.cost_center_code)
            .all()
        )
        locations = (
            db.query(Location)
            .filter(
                Location.organization_id == org_id,
                Location.is_active.is_(True),
            )
            .order_by(Location.location_name)
            .all()
        )
        shift_types = (
            AttendanceService(db)
            .list_shift_types(
                org_id,
                is_active=True,
                pagination=PaginationParams(limit=DROPDOWN_LIMIT),
            )
            .items
        )
        user_rows = (
            db.query(UserCredential, Person)
            .join(Person, UserCredential.person_id == Person.id)
            .filter(Person.organization_id == org_id)
            .order_by(Person.first_name, Person.last_name)
            .all()
        )
        user_options = {}
        for cred, user_person in user_rows:
            label = f"{user_person.name} ({user_person.email})"
            if cred.username:
                label = f"{label} - {cred.username}"
            user_options[str(user_person.id)] = {
                "person_id": user_person.id,
                "label": label,
            }
        if person and str(person.id) not in user_options:
            user_options[str(person.id)] = {
                "person_id": person.id,
                "label": f"{person.name} ({person.email})",
            }
        user_accounts = list(user_options.values())

        context = {
            **base_context(request, auth, "Edit Employee", "employees"),
            "employee": employee,
            "person": person,
            "departments": departments,
            "designations": designations,
            "employment_types": employment_types,
            "grades": grades,
            "managers": managers,
            "cost_centers": cost_centers,
            "locations": locations,
            "shift_types": shift_types,
            "user_accounts": user_accounts,
            "statuses": [s.value for s in EmployeeStatus],
            "salary_modes": [m.value for m in SalaryMode],
            "genders": [g.value for g in Gender],
            "errors": {},
            "form_data": {},
        }

        return templates.TemplateResponse(
            request,
            "people/hr/employee_form.html",
            context,
        )

    # =========================================================================
    # Departments
    # =========================================================================

    def list_departments_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        page: int = 1,
        is_active: bool | None = None,
    ) -> HTMLResponse:
        """Render department list page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)

        filters = DepartmentFilters(search=search, is_active=is_active)
        pagination = PaginationParams.from_page(page, DEFAULT_PAGE_SIZE)
        result = svc.list_departments(filters, pagination)

        # Count employees per department in bulk
        dept_employee_counts = svc.get_department_headcounts_bulk(
            [dept.department_id for dept in result.items]
        )

        context = {
            **base_context(request, auth, "Departments", "departments"),
            "departments": result.items,
            "employee_counts": dept_employee_counts,
            "search": search or "",
            "is_active": "true"
            if is_active is True
            else "false"
            if is_active is False
            else "",
            "page": page,
            "total_pages": result.total_pages,
            "total_count": result.total,
            "total": result.total,
            "limit": pagination.limit,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }

        return templates.TemplateResponse(
            request,
            "people/hr/departments.html",
            context,
        )

    def department_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        department_id: str | None = None,
    ) -> HTMLResponse:
        """Render department form (new or edit)."""
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)
        emp_svc = EmployeeService(db, org_id)

        department = None
        if department_id:
            department = svc.get_department(coerce_uuid(department_id))

        # Get parent department options (exclude current dept to prevent cycles)
        all_depts = svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
        parent_options = [
            d
            for d in all_depts
            if not department or d.department_id != department.department_id
        ]

        # Get active employees for department head dropdown
        employee_options = emp_svc.list_employees(
            EmployeeFilters(status=EmployeeStatus.ACTIVE),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items

        title = "Edit Department" if department else "New Department"
        context = {
            **base_context(request, auth, title, "departments"),
            "department": department,
            "parent_options": parent_options,
            "employee_options": employee_options,
            "errors": {},
        }

        return templates.TemplateResponse(
            request,
            "people/hr/department_form.html",
            context,
        )

    def department_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        department_id: str,
        page: int = 1,
    ) -> HTMLResponse:
        """Render department detail page."""
        org_id = coerce_uuid(auth.organization_id)
        org_svc = OrganizationService(db, org_id)
        emp_svc = EmployeeService(db, org_id)

        department = org_svc.get_department(coerce_uuid(department_id))
        headcount = org_svc.get_department_headcount(department.department_id)

        filters = EmployeeFilters(department_id=department.department_id)
        pagination = PaginationParams.from_page(page, DEFAULT_PAGE_SIZE)
        result = emp_svc.list_employees(filters, pagination, eager_load=True)

        context = {
            **base_context(request, auth, department.department_name, "departments"),
            "department": department,
            "headcount": headcount,
            "employees": result.items,
            "page": page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }

        return templates.TemplateResponse(
            request,
            "people/hr/department_detail.html",
            context,
        )

    # =========================================================================
    # Designations
    # =========================================================================

    def list_designations_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render designation list page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)

        filters = DesignationFilters(search=search)
        pagination = PaginationParams.from_page(page, DEFAULT_PAGE_SIZE)
        result = svc.list_designations(filters, pagination)

        context = {
            **base_context(request, auth, "Designations", "designations"),
            "designations": result.items,
            "search": search or "",
            "page": page,
            "total_pages": result.total_pages,
            "total_count": result.total,
            "total": result.total,
            "limit": pagination.limit,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }

        return templates.TemplateResponse(
            request,
            "people/hr/designations.html",
            context,
        )

    def designation_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        designation_id: str | None = None,
    ) -> HTMLResponse:
        """Render designation form (new or edit)."""
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)

        designation = None
        if designation_id:
            designation = svc.get_designation(coerce_uuid(designation_id))

        title = "Edit Designation" if designation else "New Designation"
        context = {
            **base_context(request, auth, title, "designations"),
            "designation": designation,
            "errors": {},
        }

        return templates.TemplateResponse(
            request,
            "people/hr/designation_form.html",
            context,
        )

    # =========================================================================
    # Employment Types
    # =========================================================================

    def list_employment_types_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render employment types list page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)

        filters = EmploymentTypeFilters(search=search)
        pagination = PaginationParams.from_page(page, DEFAULT_PAGE_SIZE)
        result = svc.list_employment_types(filters, pagination)

        context = {
            **base_context(request, auth, "Employment Types", "employment-types"),
            "employment_types": result.items,
            "search": search or "",
            "page": page,
            "total_pages": result.total_pages,
            "total_count": result.total,
            "total": result.total,
            "limit": pagination.limit,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }

        return templates.TemplateResponse(
            request,
            "people/hr/employment_types.html",
            context,
        )

    def employment_type_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employment_type_id: str | None = None,
    ) -> HTMLResponse:
        """Render employment type form (new or edit)."""
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)

        employment_type = None
        if employment_type_id:
            employment_type = svc.get_employment_type(coerce_uuid(employment_type_id))

        title = "Edit Employment Type" if employment_type else "New Employment Type"
        context = {
            **base_context(request, auth, title, "employment-types"),
            "employment_type": employment_type,
            "errors": {},
        }

        return templates.TemplateResponse(
            request,
            "people/hr/employment_type_form.html",
            context,
        )

    # =========================================================================
    # Employee Grades
    # =========================================================================

    def list_grades_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render employee grades list page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)

        filters = EmployeeGradeFilters(search=search)
        pagination = PaginationParams.from_page(page, DEFAULT_PAGE_SIZE)
        result = svc.list_employee_grades(filters, pagination)

        context = {
            **base_context(request, auth, "Employee Grades", "grades"),
            "grades": result.items,
            "search": search or "",
            "page": page,
            "total_pages": result.total_pages,
            "total_count": result.total,
            "total": result.total,
            "limit": pagination.limit,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }

        return templates.TemplateResponse(
            request,
            "people/hr/grades.html",
            context,
        )

    def grade_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        grade_id: str | None = None,
    ) -> HTMLResponse:
        """Render employee grade form (new or edit)."""
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)

        grade = None
        if grade_id:
            grade = svc.get_employee_grade(coerce_uuid(grade_id))

        title = "Edit Employee Grade" if grade else "New Employee Grade"
        context = {
            **base_context(request, auth, title, "grades"),
            "grade": grade,
            "errors": {},
        }

        return templates.TemplateResponse(
            request,
            "people/hr/grade_form.html",
            context,
        )

    # =========================================================================
    # Helpers
    # =========================================================================

    def _status_class(self, status: EmployeeStatus) -> str:
        """Get CSS class for employee status badge."""
        return {
            EmployeeStatus.DRAFT: "bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300",
            EmployeeStatus.ACTIVE: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
            EmployeeStatus.ON_LEAVE: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
            EmployeeStatus.SUSPENDED: "bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300",
            EmployeeStatus.RESIGNED: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
            EmployeeStatus.TERMINATED: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
            EmployeeStatus.RETIRED: "bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300",
        }.get(status, "bg-slate-100 text-slate-700")


# Singleton instance
hr_web_service = HRWebService()
