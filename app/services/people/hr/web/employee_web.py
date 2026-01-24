"""HR Web Service - Employee and organization web view methods.

Provides view-focused data and operations for HR web routes.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.auth import UserCredential
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
from app.models.people.payroll.salary_assignment import SalaryStructureAssignment
from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.models.person import Gender, Person
from app.services.common import coerce_uuid, PaginationParams
from app.services.people.attendance.attendance_service import AttendanceService
from app.services.people.hr import (
    EmployeeService,
    OrganizationService,
    EmployeeFilters,
    EmployeeCreateData,
    EmployeeUpdateData,
    DepartmentFilters,
    DepartmentCreateData,
    DepartmentUpdateData,
    DesignationFilters,
    DesignationCreateData,
    DesignationUpdateData,
    EmploymentTypeFilters,
    EmployeeGradeFilters,
)
from app.templates import templates
from app.web.deps import base_context, WebAuthContext

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 25
DROPDOWN_LIMIT = 1000


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
        search: Optional[str] = None,
        status: Optional[str] = None,
        department_id: Optional[str] = None,
        page: int = 1,
        success: Optional[str] = None,
        error: Optional[str] = None,
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
            managers.append({
                "employee_id": mgr.employee_id,
                "employee_code": mgr.employee_code,
                "full_name": full_name,
            })

        # Build employee view data - relationships already loaded via eager_load
        employees_view = []
        for emp in result.items:
            person = emp.person
            dept = emp.department
            desig = emp.designation

            employees_view.append({
                "employee_id": emp.employee_id,
                "employee_code": emp.employee_code,
                "person_name": f"{person.first_name or ''} {person.last_name or ''}".strip() if person else "",
                "email": person.email if person else "",
                "department_name": dept.department_name if dept else "",
                "designation_name": desig.designation_name if desig else "",
                "date_of_joining": emp.date_of_joining,
                "status": emp.status.value,
                "status_class": self._status_class(emp.status),
            })

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
            "page": page,
            "total_pages": result.total_pages,
            "total": result.total,
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

    def employee_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: str,
    ) -> HTMLResponse:
        """Render employee detail page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)

        employee = svc.get_employee(coerce_uuid(employee_id))
        person = db.get(Person, employee.person_id)
        dept = db.get(Department, employee.department_id) if employee.department_id else None
        desig = db.get(Designation, employee.designation_id) if employee.designation_id else None
        grade = db.get(EmployeeGrade, employee.grade_id) if employee.grade_id else None
        emp_type = db.get(EmploymentType, employee.employment_type_id) if employee.employment_type_id else None
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
                    "name": f"{manager_person.first_name or ''} {manager_person.last_name or ''}".strip() if manager_person else "",
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
        onboarding = lifecycle_svc.get_onboarding_for_employee(org_id, employee.employee_id)

        context = {
            **base_context(request, auth, "Employee Details", "employees"),
            "employee": employee,
            "person": person,
            "department": dept,
            "designation": desig,
            "grade": grade,
            "employment_type": emp_type,
            "manager": manager,
            "credentials": credentials,
            "salary_assignments": salary_assignments,
            "tax_profile": tax_profile,
            "onboarding": onboarding,
        }

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
        error: Optional[str] = None,
        form_data: Optional[dict] = None,
        errors: Optional[dict] = None,
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
        managers = EmployeeService(db, org_id).list_employees(
            EmployeeFilters(status=EmployeeStatus.ACTIVE),
            PaginationParams(limit=DROPDOWN_LIMIT),
            eager_load=True,
        ).items
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
        shift_types = AttendanceService(db).list_shift_types(
            org_id,
            is_active=True,
            pagination=PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
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
        managers = EmployeeService(db, org_id).list_employees(
            EmployeeFilters(status=EmployeeStatus.ACTIVE),
            PaginationParams(limit=DROPDOWN_LIMIT),
            eager_load=True,
        ).items
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
        shift_types = AttendanceService(db).list_shift_types(
            org_id,
            is_active=True,
            pagination=PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
        user_rows = (
            db.query(UserCredential, Person)
            .join(Person, UserCredential.person_id == Person.id)
            .filter(Person.organization_id == org_id)
            .order_by(Person.first_name, Person.last_name)
            .all()
        )
        user_options = {}
        for cred, person in user_rows:
            label = f"{person.name} ({person.email})"
            if cred.username:
                label = f"{label} - {cred.username}"
            user_options[str(person.id)] = {
                "person_id": person.id,
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
        search: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render department list page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)

        filters = DepartmentFilters(search=search)
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
            "page": page,
            "total_pages": result.total_pages,
            "total": result.total,
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
        department_id: Optional[str] = None,
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
        parent_options = [d for d in all_depts if not department or d.department_id != department.department_id]

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

    # =========================================================================
    # Designations
    # =========================================================================

    def list_designations_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str] = None,
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
            "total": result.total,
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
        designation_id: Optional[str] = None,
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
        search: Optional[str] = None,
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
            "total": result.total,
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
        employment_type_id: Optional[str] = None,
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
        search: Optional[str] = None,
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
            "total": result.total,
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
        grade_id: Optional[str] = None,
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
