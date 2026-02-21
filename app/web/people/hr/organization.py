"""Organization routes - Departments, Designations, Employment Types, Grades."""

from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import (
    DepartmentCreateData,
    DepartmentFilters,
    DepartmentUpdateData,
    DesignationCreateData,
    DesignationUpdateData,
    EmployeeGradeCreateData,
    EmployeeGradeUpdateData,
    EmploymentTypeCreateData,
    EmploymentTypeUpdateData,
    OrganizationService,
)
from app.services.people.hr.web import hr_web_service
from app.services.people.hr.web.employee_web import DROPDOWN_LIMIT
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_hr_access

from ._common import _parse_bool

router = APIRouter()


def _form_str(form: Any, key: str) -> str:
    """Normalize form value to a trimmed string."""
    value = form.get(key)
    if value is None:
        return ""
    return str(value).strip()


# =============================================================================
# Departments
# =============================================================================


@router.get("/departments", response_class=HTMLResponse)
def list_departments(
    request: Request,
    search: str | None = None,
    is_active: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Department list page."""
    filter_is_active: bool | None = None
    if is_active is None:
        filter_is_active = True
    else:
        text = str(is_active).strip().lower()
        if text in {"true", "1", "yes", "active"}:
            filter_is_active = True
        elif text in {"false", "0", "no", "inactive"}:
            filter_is_active = False

    return hr_web_service.list_departments_response(
        request,
        auth,
        db,
        search,
        page,
        filter_is_active,
    )


@router.get("/departments/new", response_class=HTMLResponse)
def new_department_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New department form page."""
    return hr_web_service.department_form_response(request, auth, db)


@router.get("/departments/{department_id}/edit", response_class=HTMLResponse)
def edit_department_form(
    request: Request,
    department_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit department form page."""
    return hr_web_service.department_form_response(request, auth, db, department_id)


@router.get("/departments/{department_id}", response_class=HTMLResponse)
def view_department(
    request: Request,
    department_id: str,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Department detail page."""
    return hr_web_service.department_detail_response(
        request,
        auth,
        db,
        department_id,
        page,
    )


@router.post("/departments/new")
async def create_department(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle new department form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    department_code = _form_str(form, "department_code")
    department_name = _form_str(form, "department_name")
    description = _form_str(form, "description")
    parent_department_id = _form_str(form, "parent_department_id")
    head_id = _form_str(form, "head_id")
    is_active = _parse_bool(form.get("is_active"), True)

    if not department_code or not department_name:
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id, auth.principal)
        all_depts = svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
        context = {
            **base_context(request, auth, "New Department", "departments"),
            "department": SimpleNamespace(
                department_code=department_code,
                department_name=department_name,
                description=description,
                parent_department_id=coerce_uuid(parent_department_id)
                if parent_department_id
                else None,
                head_id=coerce_uuid(head_id) if head_id else None,
                is_active=is_active,
            ),
            "parent_options": all_depts,
            "employee_options": [],
            "errors": {
                "department_code": "Required" if not department_code else "",
                "department_name": "Required" if not department_name else "",
            },
            "error": "Department code and name are required.",
        }
        return templates.TemplateResponse(
            request,
            "people/hr/department_form.html",
            context,
        )

    org_id = coerce_uuid(auth.organization_id)
    svc = OrganizationService(db, org_id)

    data = DepartmentCreateData(
        department_code=department_code,
        department_name=department_name,
        description=description or None,
        parent_department_id=coerce_uuid(parent_department_id)
        if parent_department_id
        else None,
        head_id=coerce_uuid(head_id) if head_id else None,
        is_active=is_active,
    )

    svc.create_department(data)

    return RedirectResponse(
        url="/people/hr/departments?success=Record+saved+successfully", status_code=303
    )


@router.post("/departments/{department_id}/edit")
async def update_department(
    request: Request,
    department_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle department update form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    department_code = _form_str(form, "department_code")
    department_name = _form_str(form, "department_name")
    description = _form_str(form, "description")
    parent_department_id = _form_str(form, "parent_department_id")
    head_id = _form_str(form, "head_id")
    is_active = _parse_bool(form.get("is_active"), True)

    if not department_code or not department_name:
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)
        department = svc.get_department(coerce_uuid(department_id))
        all_depts = svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
        parent_options = [
            d for d in all_depts if d.department_id != department.department_id
        ]
        context = {
            **base_context(request, auth, "Edit Department", "departments"),
            "department": SimpleNamespace(
                department_id=department.department_id,
                department_code=department_code or department.department_code,
                department_name=department_name or department.department_name,
                description=description or department.description,
                parent_department_id=coerce_uuid(parent_department_id)
                if parent_department_id
                else department.parent_department_id,
                head_id=coerce_uuid(head_id) if head_id else department.head_id,
                is_active=is_active,
            ),
            "parent_options": parent_options,
            "employee_options": [],
            "errors": {
                "department_code": "Required" if not department_code else "",
                "department_name": "Required" if not department_name else "",
            },
            "error": "Department code and name are required.",
        }
        return templates.TemplateResponse(
            request,
            "people/hr/department_form.html",
            context,
        )

    org_id = coerce_uuid(auth.organization_id)
    svc = OrganizationService(db, org_id)

    data = DepartmentUpdateData(
        department_code=department_code,
        department_name=department_name,
        description=description or None,
        parent_department_id=coerce_uuid(parent_department_id)
        if parent_department_id
        else None,
        head_id=coerce_uuid(head_id) if head_id else None,
        is_active=is_active,
    )

    svc.update_department(coerce_uuid(department_id), data)

    return RedirectResponse(
        url="/people/hr/departments?success=Record+saved+successfully", status_code=303
    )


# =============================================================================
# Designations
# =============================================================================


@router.get("/designations", response_class=HTMLResponse)
def list_designations(
    request: Request,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Designation list page."""
    return hr_web_service.list_designations_response(request, auth, db, search, page)


@router.get("/designations/new", response_class=HTMLResponse)
def new_designation_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New designation form page."""
    return hr_web_service.designation_form_response(request, auth, db)


@router.get("/designations/{designation_id}/edit", response_class=HTMLResponse)
def edit_designation_form(
    request: Request,
    designation_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit designation form page."""
    return hr_web_service.designation_form_response(request, auth, db, designation_id)


@router.post("/designations/new")
async def create_designation(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle new designation form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    designation_code = _form_str(form, "designation_code")
    designation_name = _form_str(form, "designation_name")
    description = _form_str(form, "description")
    is_active = _parse_bool(form.get("is_active"), True)

    if not designation_code or not designation_name:
        context = {
            **base_context(request, auth, "New Designation", "designations"),
            "designation": SimpleNamespace(
                designation_code=designation_code,
                designation_name=designation_name,
                description=description,
                is_active=is_active,
            ),
            "errors": {
                "designation_code": "Required" if not designation_code else "",
                "designation_name": "Required" if not designation_name else "",
            },
            "error": "Designation code and name are required.",
        }
        return templates.TemplateResponse(
            request,
            "people/hr/designation_form.html",
            context,
        )

    org_id = coerce_uuid(auth.organization_id)
    svc = OrganizationService(db, org_id)

    data = DesignationCreateData(
        designation_code=designation_code,
        designation_name=designation_name,
        description=description or None,
        is_active=is_active,
    )

    svc.create_designation(data)

    return RedirectResponse(
        url="/people/hr/designations?success=Record+created+successfully",
        status_code=303,
    )


@router.post("/designations/{designation_id}/edit")
async def update_designation(
    request: Request,
    designation_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle designation update form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    designation_code = _form_str(form, "designation_code")
    designation_name = _form_str(form, "designation_name")
    description = _form_str(form, "description")
    is_active = _parse_bool(form.get("is_active"), True)

    if not designation_code or not designation_name:
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)
        designation = svc.get_designation(coerce_uuid(designation_id))
        context = {
            **base_context(request, auth, "Edit Designation", "designations"),
            "designation": SimpleNamespace(
                designation_id=designation.designation_id,
                designation_code=designation_code or designation.designation_code,
                designation_name=designation_name or designation.designation_name,
                description=description or designation.description,
                is_active=is_active,
            ),
            "errors": {
                "designation_code": "Required" if not designation_code else "",
                "designation_name": "Required" if not designation_name else "",
            },
            "error": "Designation code and name are required.",
        }
        return templates.TemplateResponse(
            request,
            "people/hr/designation_form.html",
            context,
        )

    org_id = coerce_uuid(auth.organization_id)
    svc = OrganizationService(db, org_id)

    data = DesignationUpdateData(
        designation_code=designation_code,
        designation_name=designation_name,
        description=description or None,
        is_active=is_active,
    )

    svc.update_designation(coerce_uuid(designation_id), data)

    return RedirectResponse(
        url="/people/hr/designations?success=Record+saved+successfully", status_code=303
    )


# =============================================================================
# Employment Types
# =============================================================================


@router.get("/employment-types", response_class=HTMLResponse)
def list_employment_types(
    request: Request,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Employment types list page."""
    return hr_web_service.list_employment_types_response(
        request, auth, db, search, page
    )


@router.get("/employment-types/new", response_class=HTMLResponse)
def new_employment_type_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New employment type form page."""
    return hr_web_service.employment_type_form_response(request, auth, db)


@router.get("/employment-types/{employment_type_id}/edit", response_class=HTMLResponse)
def edit_employment_type_form(
    request: Request,
    employment_type_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit employment type form page."""
    return hr_web_service.employment_type_form_response(
        request, auth, db, employment_type_id
    )


@router.post("/employment-types/new")
async def create_employment_type(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle new employment type form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    type_code = _form_str(form, "type_code")
    type_name = _form_str(form, "type_name")
    description = _form_str(form, "description")
    is_active = _parse_bool(form.get("is_active"), True)

    if not type_code or not type_name:
        context = {
            **base_context(request, auth, "New Employment Type", "employment-types"),
            "employment_type": SimpleNamespace(
                type_code=type_code,
                type_name=type_name,
                description=description,
                is_active=is_active,
            ),
            "errors": {
                "type_code": "Required" if not type_code else "",
                "type_name": "Required" if not type_name else "",
            },
            "error": "Type code and name are required.",
        }
        return templates.TemplateResponse(
            request,
            "people/hr/employment_type_form.html",
            context,
        )

    org_id = coerce_uuid(auth.organization_id)
    svc = OrganizationService(db, org_id)

    data = EmploymentTypeCreateData(
        type_code=type_code,
        type_name=type_name,
        description=description or None,
        is_active=is_active,
    )

    svc.create_employment_type(data)

    return RedirectResponse(
        url="/people/hr/employment-types?success=Record+created+successfully",
        status_code=303,
    )


@router.post("/employment-types/{employment_type_id}/edit")
async def update_employment_type(
    request: Request,
    employment_type_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle employment type update form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    type_code = _form_str(form, "type_code")
    type_name = _form_str(form, "type_name")
    description = _form_str(form, "description")
    is_active = _parse_bool(form.get("is_active"), True)

    if not type_code or not type_name:
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)
        employment_type = svc.get_employment_type(coerce_uuid(employment_type_id))
        context = {
            **base_context(request, auth, "Edit Employment Type", "employment-types"),
            "employment_type": SimpleNamespace(
                employment_type_id=employment_type.employment_type_id,
                type_code=type_code or employment_type.type_code,
                type_name=type_name or employment_type.type_name,
                description=description or employment_type.description,
                is_active=is_active,
            ),
            "errors": {
                "type_code": "Required" if not type_code else "",
                "type_name": "Required" if not type_name else "",
            },
            "error": "Type code and name are required.",
        }
        return templates.TemplateResponse(
            request,
            "people/hr/employment_type_form.html",
            context,
        )

    org_id = coerce_uuid(auth.organization_id)
    svc = OrganizationService(db, org_id)

    data = EmploymentTypeUpdateData(
        type_code=type_code,
        type_name=type_name,
        description=description or None,
        is_active=is_active,
    )

    svc.update_employment_type(coerce_uuid(employment_type_id), data)

    return RedirectResponse(
        url="/people/hr/employment-types?success=Record+saved+successfully",
        status_code=303,
    )


# =============================================================================
# Employee Grades
# =============================================================================


@router.get("/grades", response_class=HTMLResponse)
def list_grades(
    request: Request,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Employee grades list page."""
    return hr_web_service.list_grades_response(request, auth, db, search, page)


@router.get("/grades/new", response_class=HTMLResponse)
def new_grade_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New grade form page."""
    return hr_web_service.grade_form_response(request, auth, db)


@router.get("/grades/{grade_id}/edit", response_class=HTMLResponse)
def edit_grade_form(
    request: Request,
    grade_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit grade form page."""
    return hr_web_service.grade_form_response(request, auth, db, grade_id)


@router.post("/grades/new")
async def create_grade(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle new grade form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    grade_code = _form_str(form, "grade_code")
    grade_name = _form_str(form, "grade_name")
    description = _form_str(form, "description")
    rank_value = _form_str(form, "rank")
    min_salary_value = _form_str(form, "min_salary")
    max_salary_value = _form_str(form, "max_salary")
    is_active = _parse_bool(form.get("is_active"), True)

    errors = {
        "grade_code": "Required" if not grade_code else "",
        "grade_name": "Required" if not grade_name else "",
        "rank": "",
        "min_salary": "",
        "max_salary": "",
    }

    rank = 0
    if rank_value:
        try:
            rank = int(rank_value)
        except ValueError:
            errors["rank"] = "Invalid number"

    min_salary = None
    if min_salary_value:
        try:
            min_salary = Decimal(min_salary_value)
        except (InvalidOperation, ValueError):
            errors["min_salary"] = "Invalid amount"

    max_salary = None
    if max_salary_value:
        try:
            max_salary = Decimal(max_salary_value)
        except (InvalidOperation, ValueError):
            errors["max_salary"] = "Invalid amount"

    if (
        errors["grade_code"]
        or errors["grade_name"]
        or errors["rank"]
        or errors["min_salary"]
        or errors["max_salary"]
    ):
        context = {
            **base_context(request, auth, "New Employee Grade", "grades"),
            "grade": SimpleNamespace(
                grade_code=grade_code,
                grade_name=grade_name,
                description=description,
                rank=rank_value,
                min_salary=min_salary_value,
                max_salary=max_salary_value,
                is_active=is_active,
            ),
            "errors": errors,
            "error": "Grade code and name are required.",
        }
        if errors["rank"] or errors["min_salary"] or errors["max_salary"]:
            context["error"] = "Please fix the highlighted fields."
        return templates.TemplateResponse(
            request,
            "people/hr/grade_form.html",
            context,
        )

    org_id = coerce_uuid(auth.organization_id)
    svc = OrganizationService(db, org_id)

    data = EmployeeGradeCreateData(
        grade_code=grade_code,
        grade_name=grade_name,
        description=description or None,
        rank=rank,
        min_salary=min_salary,
        max_salary=max_salary,
        is_active=is_active,
    )

    svc.create_employee_grade(data)

    return RedirectResponse(
        url="/people/hr/grades?success=Record+saved+successfully", status_code=303
    )


@router.post("/grades/{grade_id}/edit")
async def update_grade(
    request: Request,
    grade_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle grade update form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    grade_code = _form_str(form, "grade_code")
    grade_name = _form_str(form, "grade_name")
    description = _form_str(form, "description")
    rank_value = _form_str(form, "rank")
    min_salary_value = _form_str(form, "min_salary")
    max_salary_value = _form_str(form, "max_salary")
    is_active = _parse_bool(form.get("is_active"), True)

    errors = {
        "grade_code": "Required" if not grade_code else "",
        "grade_name": "Required" if not grade_name else "",
        "rank": "",
        "min_salary": "",
        "max_salary": "",
    }

    rank = 0
    if rank_value:
        try:
            rank = int(rank_value)
        except ValueError:
            errors["rank"] = "Invalid number"

    min_salary = None
    if min_salary_value:
        try:
            min_salary = Decimal(min_salary_value)
        except (InvalidOperation, ValueError):
            errors["min_salary"] = "Invalid amount"

    max_salary = None
    if max_salary_value:
        try:
            max_salary = Decimal(max_salary_value)
        except (InvalidOperation, ValueError):
            errors["max_salary"] = "Invalid amount"

    if (
        errors["grade_code"]
        or errors["grade_name"]
        or errors["rank"]
        or errors["min_salary"]
        or errors["max_salary"]
    ):
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)
        grade = svc.get_employee_grade(coerce_uuid(grade_id))
        context = {
            **base_context(request, auth, "Edit Employee Grade", "grades"),
            "grade": SimpleNamespace(
                grade_id=grade.grade_id,
                grade_code=grade_code or grade.grade_code,
                grade_name=grade_name or grade.grade_name,
                description=description or grade.description,
                rank=rank_value or grade.rank,
                min_salary=min_salary_value or grade.min_salary,
                max_salary=max_salary_value or grade.max_salary,
                is_active=is_active,
            ),
            "errors": errors,
            "error": "Grade code and name are required.",
        }
        if errors["rank"] or errors["min_salary"] or errors["max_salary"]:
            context["error"] = "Please fix the highlighted fields."
        return templates.TemplateResponse(
            request,
            "people/hr/grade_form.html",
            context,
        )

    org_id = coerce_uuid(auth.organization_id)
    svc = OrganizationService(db, org_id)

    data = EmployeeGradeUpdateData(
        grade_code=grade_code,
        grade_name=grade_name,
        description=description or None,
        rank=rank,
        min_salary=min_salary,
        max_salary=max_salary,
        is_active=is_active,
    )

    svc.update_employee_grade(coerce_uuid(grade_id), data)

    return RedirectResponse(
        url="/people/hr/grades?success=Record+saved+successfully", status_code=303
    )
