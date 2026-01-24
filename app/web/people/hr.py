"""
HR Web Routes.

HTML template routes for Employees, Departments, and Designations.
"""

from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.finance.core_org.location import Location, LocationType
from app.models.people.hr import Employee
from app.services.people.hr.web import hr_web_service
from app.services.people.hr import (
    EmployeeService,
    OrganizationService,
    EmployeeCreateData,
    EmployeeUpdateData,
    TerminationData,
    DepartmentFilters,
    DepartmentCreateData,
    DepartmentUpdateData,
    DesignationCreateData,
    DesignationUpdateData,
    EmploymentTypeCreateData,
    EmploymentTypeUpdateData,
    EmployeeGradeCreateData,
    EmployeeGradeUpdateData,
    BulkUpdateData,
)
from app.services.common import PaginationParams, ValidationError, coerce_uuid
from app.services.people.hr.web.employee_web import DEFAULT_PAGE_SIZE, DROPDOWN_LIMIT
from app.templates import templates
from app.web.deps import base_context, get_db, require_hr_access, WebAuthContext


router = APIRouter(prefix="/hr", tags=["hr-web"])


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "on", "yes"}


def _parse_location_type(value: Optional[str]) -> Optional[LocationType]:
    if not value:
        return None
    try:
        return LocationType(value)
    except ValueError:
        return None


# =============================================================================
# Employees
# =============================================================================


@router.get("/employees", response_class=HTMLResponse)
def list_employees(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    department_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Employee list page."""
    return hr_web_service.list_employees_response(
        request, auth, db, search, status, department_id, page
    )


@router.get("/employees/org-chart", response_class=HTMLResponse)
def view_org_chart(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Organization chart page."""
    return hr_web_service.org_chart_response(request, auth, db)


@router.get("/employees/stats")
def employee_stats(
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Employee stats endpoint for dashboards."""
    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)
    return svc.get_employee_stats()


@router.get("/employees/new", response_class=HTMLResponse)
def new_employee_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New employee form page."""
    return hr_web_service.employee_new_form_response(request, auth, db)


@router.post("/employees/new")
async def create_employee(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle new employee form submission."""
    from datetime import datetime
    from app.models.person import Gender, Person
    from app.models.people.hr import EmployeeStatus as EmpStatus

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    # Person fields
    first_name = (form.get("first_name") or "").strip()
    last_name = (form.get("last_name") or "").strip()
    email = (form.get("email") or "").strip()
    phone = (form.get("phone") or "").strip()
    date_of_birth = (form.get("date_of_birth") or "").strip()
    gender = (form.get("gender") or "").strip()
    address_line1 = (form.get("address_line1") or "").strip()
    address_line2 = (form.get("address_line2") or "").strip()
    city = (form.get("city") or "").strip()
    region = (form.get("region") or "").strip()
    postal_code = (form.get("postal_code") or "").strip()
    country_code = (form.get("country_code") or "").strip()
    # Employee fields
    employee_code = (form.get("employee_code") or "").strip()
    department_id = (form.get("department_id") or "").strip()
    designation_id = (form.get("designation_id") or "").strip()
    employment_type_id = (form.get("employment_type_id") or "").strip()
    grade_id = (form.get("grade_id") or "").strip()
    reports_to_id = (form.get("reports_to_id") or "").strip()
    assigned_location_id = (form.get("assigned_location_id") or "").strip()
    default_shift_type_id = (form.get("default_shift_type_id") or "").strip()
    linked_person_id = (form.get("linked_person_id") or "").strip()
    cost_center_id = (form.get("cost_center_id") or "").strip()
    date_of_joining = (form.get("date_of_joining") or "").strip()
    probation_end_date = (form.get("probation_end_date") or "").strip()
    notes = (form.get("notes") or "").strip()
    status = (form.get("status") or "DRAFT").strip()
    # Bank details
    bank_name = (form.get("bank_name") or "").strip()
    bank_account_name = (form.get("bank_account_name") or "").strip()
    bank_account_number = (form.get("bank_account_number") or "").strip()
    bank_branch_code = (form.get("bank_branch_code") or "").strip()

    if (not linked_person_id and (not first_name or not last_name or not email)) or not date_of_joining:
        errors = {
            "first_name": "Required" if not first_name else "",
            "last_name": "Required" if not last_name else "",
            "email": "Required" if not email else "",
            "date_of_joining": "Required" if not date_of_joining else "",
        }
        return hr_web_service.employee_new_form_response(
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
                "notes": notes,
            },
            errors=errors,
        )

    org_id = coerce_uuid(auth.organization_id)

    # Parse date
    try:
        joining_date = datetime.strptime(date_of_joining, "%Y-%m-%d").date() if date_of_joining else None
    except ValueError:
        joining_date = None

    try:
        dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date() if date_of_birth else None
    except ValueError:
        dob = None

    try:
        probation_date = (
            datetime.strptime(probation_end_date, "%Y-%m-%d").date()
            if probation_end_date
            else None
        )
    except ValueError:
        probation_date = None

    # Parse status
    status_enum = EmpStatus.DRAFT
    if status:
        try:
            status_enum = EmpStatus(status.upper())
        except ValueError:
            pass

    # Check if person with this email already exists
    existing_person = db.query(Person).filter(
        Person.email == email,
        Person.organization_id == org_id,
    ).first()

    if existing_person:
        # Check if they already have an employee record
        svc = EmployeeService(db, org_id)
        existing_emp = svc.get_employee_by_person(existing_person.id)
        if existing_emp:
            # Return form with error
            return hr_web_service.employee_new_form_response(
                request, auth, db,
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
                    "linked_person_id": linked_person_id,
                    "date_of_joining": date_of_joining,
                    "status": status,
                    "bank_name": bank_name,
                    "bank_account_name": bank_account_name,
                    "bank_account_number": bank_account_number,
                    "bank_branch_code": bank_branch_code,
                }
            )
        person = existing_person
    else:
        if linked_person_id:
            person = db.get(Person, coerce_uuid(linked_person_id))
            if not person or person.organization_id != org_id:
                return hr_web_service.employee_new_form_response(
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
        employment_type_id=coerce_uuid(employment_type_id) if employment_type_id else None,
        grade_id=coerce_uuid(grade_id) if grade_id else None,
        reports_to_id=coerce_uuid(reports_to_id) if reports_to_id else None,
        assigned_location_id=coerce_uuid(assigned_location_id) if assigned_location_id else None,
        default_shift_type_id=coerce_uuid(default_shift_type_id) if default_shift_type_id else None,
        cost_center_id=coerce_uuid(cost_center_id) if cost_center_id else None,
        date_of_joining=joining_date,
        probation_end_date=probation_date,
        status=status_enum,
        bank_name=bank_name,
        bank_account_name=bank_account_name,
        bank_account_number=bank_account_number,
        bank_sort_code=bank_branch_code,
        notes=notes or None,
    )

    employee = svc.create_employee(person.id, data)
    db.commit()

    return RedirectResponse(
        url=f"/people/hr/employees/{employee.employee_id}",
        status_code=303,
    )


@router.get("/employees/{employee_id}", response_class=HTMLResponse)
def view_employee(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Employee detail page."""
    return hr_web_service.employee_detail_response(request, auth, db, str(employee_id))


@router.get("/employees/{employee_id}/edit", response_class=HTMLResponse)
def edit_employee_form(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit employee form page."""
    return hr_web_service.employee_edit_form_response(request, auth, db, str(employee_id))


@router.post("/employees/{employee_id}/edit")
async def update_employee(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle employee update form submission."""
    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)

    from app.models.people.hr import EmployeeStatus as EmpStatus

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    employee_code = (form.get("employee_code") or "").strip()
    department_id = (form.get("department_id") or "").strip()
    designation_id = (form.get("designation_id") or "").strip()
    employment_type_id = (form.get("employment_type_id") or "").strip()
    grade_id = (form.get("grade_id") or "").strip()
    reports_to_id = (form.get("reports_to_id") or "").strip()
    assigned_location_id = (form.get("assigned_location_id") or "").strip()
    default_shift_type_id = (form.get("default_shift_type_id") or "").strip()
    linked_person_id = (form.get("linked_person_id") or "").strip()
    cost_center_id = (form.get("cost_center_id") or "").strip()
    date_of_joining = (form.get("date_of_joining") or "").strip()
    probation_end_date = (form.get("probation_end_date") or "").strip()
    notes = (form.get("notes") or "").strip()
    status = (form.get("status") or "").strip()
    bank_name = (form.get("bank_name") or "").strip()
    bank_account_name = (form.get("bank_account_name") or "").strip()
    bank_account_number = (form.get("bank_account_number") or "").strip()
    bank_branch_code = (form.get("bank_branch_code") or "").strip()

    status_enum = None
    if status:
        try:
            status_enum = EmpStatus(status.upper())
        except ValueError:
            pass

    from datetime import datetime

    try:
        joining_date = datetime.strptime(date_of_joining, "%Y-%m-%d").date() if date_of_joining else None
    except ValueError:
        joining_date = None

    try:
        probation_date = (
            datetime.strptime(probation_end_date, "%Y-%m-%d").date()
            if probation_end_date
            else None
        )
    except ValueError:
        probation_date = None

    provided_fields = {
        "employee_number",
        "department_id",
        "designation_id",
        "employment_type_id",
        "grade_id",
        "reports_to_id",
        "cost_center_id",
        "assigned_location_id",
        "default_shift_type_id",
        "date_of_joining",
        "probation_end_date",
        "status",
        "bank_name",
        "bank_account_name",
        "bank_account_number",
        "bank_sort_code",
        "notes",
    }

    data = EmployeeUpdateData(
        employee_number=employee_code if employee_code else None,
        department_id=coerce_uuid(department_id) if department_id else None,
        designation_id=coerce_uuid(designation_id) if designation_id else None,
        employment_type_id=coerce_uuid(employment_type_id) if employment_type_id else None,
        grade_id=coerce_uuid(grade_id) if grade_id else None,
        reports_to_id=coerce_uuid(reports_to_id) if reports_to_id else None,
        assigned_location_id=coerce_uuid(assigned_location_id) if assigned_location_id else None,
        default_shift_type_id=coerce_uuid(default_shift_type_id) if default_shift_type_id else None,
        cost_center_id=coerce_uuid(cost_center_id) if cost_center_id else None,
        date_of_joining=joining_date,
        probation_end_date=probation_date,
        status=status_enum,
        bank_name=bank_name or None,
        bank_account_name=bank_account_name or None,
        bank_account_number=bank_account_number or None,
        bank_sort_code=bank_branch_code or None,
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
        url=f"/people/hr/employees/{employee_id}",
        status_code=303,
    )


@router.post("/employees/{employee_id}/activate")
def activate_employee(
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Activate an employee."""
    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)
    svc.activate_employee(employee_id)
    db.commit()
    return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)


@router.post("/employees/{employee_id}/suspend")
async def suspend_employee(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Suspend an employee."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    reason = (form.get("reason") or "").strip()

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)
    svc.suspend_employee(employee_id, reason=reason or None)
    db.commit()
    return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)


@router.post("/employees/{employee_id}/on-leave")
def set_employee_on_leave(
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Set an employee on leave."""
    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)
    svc.set_on_leave(employee_id)
    db.commit()
    return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)


@router.post("/employees/{employee_id}/resign")
async def resign_employee(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Record employee resignation."""
    from datetime import datetime

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    date_of_leaving = (form.get("date_of_leaving") or "").strip()

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)

    try:
        leaving_date = datetime.strptime(date_of_leaving, "%Y-%m-%d").date()
    except ValueError:
        leaving_date = None

    if leaving_date:
        svc.resign_employee(employee_id, leaving_date)
        db.commit()
        return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)

    employee = svc.get_employee(employee_id)
    context = hr_web_service.employee_detail_response(request, auth, db, str(employee_id)).context
    context.update(
        {
            "employee": employee,
            "error": "Please provide a valid resignation date.",
        }
    )
    return templates.TemplateResponse(request, "people/hr/employee_detail.html", context)


@router.post("/employees/{employee_id}/terminate")
async def terminate_employee(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Terminate an employee."""
    from datetime import datetime

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    date_of_leaving = (form.get("date_of_leaving") or "").strip()
    reason = (form.get("reason") or "").strip()

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)

    try:
        leaving_date = datetime.strptime(date_of_leaving, "%Y-%m-%d").date()
    except ValueError:
        leaving_date = None

    if leaving_date:
        svc.terminate_employee(
            employee_id,
            TerminationData(
                date_of_leaving=leaving_date,
                reason=reason or None,
            ),
        )
        db.commit()
        return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)

    employee = svc.get_employee(employee_id)
    context = hr_web_service.employee_detail_response(request, auth, db, str(employee_id)).context
    context.update(
        {
            "employee": employee,
            "error": "Please provide a valid termination date.",
        }
    )
    return templates.TemplateResponse(request, "people/hr/employee_detail.html", context)


@router.post("/employees/{employee_id}/user-credentials")
async def create_employee_user_credentials(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create user credentials for an employee."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    username = (form.get("username") or "").strip()
    password = (form.get("password") or "").strip()
    must_change = _parse_bool(form.get("must_change_password"), False)

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)

    try:
        svc.create_user_credentials_for_employee(
            employee_id,
            username=username or None,
            password=password or None,
            must_change_password=must_change,
        )
        db.commit()
        return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)
    except ValidationError as exc:
        db.rollback()
        context = hr_web_service.employee_detail_response(
            request, auth, db, str(employee_id)
        ).context
        context["user_access_error"] = str(exc)
        return templates.TemplateResponse(
            request, "people/hr/employee_detail.html", context
        )


@router.post("/employees/{employee_id}/link-user")
async def link_employee_user(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Link an employee to an existing user (Person)."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    person_id = (form.get("person_id") or "").strip()
    if not person_id:
        context = hr_web_service.employee_detail_response(
            request, auth, db, str(employee_id)
        ).context
        context["user_access_error"] = "Person ID is required."
        return templates.TemplateResponse(
            request, "people/hr/employee_detail.html", context
        )

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)

    try:
        person_uuid = coerce_uuid(person_id, raise_http=False)
    except Exception:
        context = hr_web_service.employee_detail_response(
            request, auth, db, str(employee_id)
        ).context
        context["user_access_error"] = "Invalid Person ID."
        return templates.TemplateResponse(
            request, "people/hr/employee_detail.html", context
        )

    try:
        svc.link_employee_to_person(employee_id, person_uuid)
        db.commit()
        return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)
    except ValidationError as exc:
        db.rollback()
        context = hr_web_service.employee_detail_response(
            request, auth, db, str(employee_id)
        ).context
        context["user_access_error"] = str(exc)
        return templates.TemplateResponse(
            request, "people/hr/employee_detail.html", context
        )


@router.get("/people/search")
def search_people(
    query: str = Query("", min_length=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Search people by name or email for linking users."""
    org_id = coerce_uuid(auth.organization_id)
    search_term = f"%{query.strip()}%"
    linked_people_subq = (
        select(Employee.person_id)
        .where(Employee.organization_id == org_id)
        .where(Employee.is_deleted == False)
    )
    results = (
        db.query(Person)
        .filter(Person.organization_id == org_id)
        .filter(Person.id.not_in(linked_people_subq))
        .filter(
            (Person.first_name.ilike(search_term))
            | (Person.last_name.ilike(search_term))
            | (Person.email.ilike(search_term))
        )
        .order_by(Person.first_name.asc())
        .limit(10)
        .all()
    )
    payload = [
        {
            "id": str(person.id),
            "name": f"{person.first_name or ''} {person.last_name or ''}".strip(),
            "email": person.email,
        }
        for person in results
    ]
    return JSONResponse(payload)


@router.post("/employees/bulk-update")
async def bulk_update_employees(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Bulk update employees."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    employee_ids = form.getlist("employee_ids")
    department_id = (form.get("department_id") or "").strip()
    designation_id = (form.get("designation_id") or "").strip()
    reports_to_id = (form.get("reports_to_id") or "").strip()
    status = (form.get("status") or "").strip()

    if not employee_ids:
        return RedirectResponse(url="/people/hr/employees", status_code=303)

    status_enum = None
    if status:
        from app.models.people.hr import EmployeeStatus as EmpStatus

        try:
            status_enum = EmpStatus(status.upper())
        except ValueError:
            status_enum = None

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)

    data = BulkUpdateData(
        ids=[coerce_uuid(emp_id) for emp_id in employee_ids],
        department_id=coerce_uuid(department_id) if department_id else None,
        designation_id=coerce_uuid(designation_id) if designation_id else None,
        reports_to_id=coerce_uuid(reports_to_id) if reports_to_id else None,
        status=status_enum,
    )

    svc.bulk_update(data)
    db.commit()
    return RedirectResponse(url="/people/hr/employees", status_code=303)


@router.post("/employees/bulk-delete")
async def bulk_delete_employees(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Bulk delete employees."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    employee_ids = form.getlist("employee_ids")
    if not employee_ids:
        return RedirectResponse(url="/people/hr/employees", status_code=303)

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)
    svc.bulk_delete([coerce_uuid(emp_id) for emp_id in employee_ids])
    db.commit()
    return RedirectResponse(url="/people/hr/employees", status_code=303)


# =============================================================================
# Departments
# =============================================================================


@router.get("/departments", response_class=HTMLResponse)
def list_departments(
    request: Request,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Department list page."""
    return hr_web_service.list_departments_response(request, auth, db, search, page)


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

    department_code = (form.get("department_code") or "").strip()
    department_name = (form.get("department_name") or "").strip()
    description = (form.get("description") or "").strip()
    parent_department_id = (form.get("parent_department_id") or "").strip()
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
                is_active=is_active,
            ),
            "parent_options": all_depts,
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
        parent_department_id=coerce_uuid(parent_department_id) if parent_department_id else None,
        is_active=is_active,
    )

    dept = svc.create_department(data)
    db.commit()

    return RedirectResponse(url="/people/hr/departments", status_code=303)


# =============================================================================
# Locations (Branches)
# =============================================================================


@router.get("/locations", response_class=HTMLResponse)
def list_locations(
    request: Request,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Location list page."""
    org_id = coerce_uuid(auth.organization_id)
    query = db.query(Location).filter(Location.organization_id == org_id)
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            Location.location_code.ilike(search_term)
            | Location.location_name.ilike(search_term)
        )

    total = query.count()
    limit = DEFAULT_PAGE_SIZE
    offset = (page - 1) * limit
    items = (
        query.order_by(Location.location_name)
        .offset(offset)
        .limit(limit)
        .all()
    )

    total_pages = (total + limit - 1) // limit if total else 1

    context = {
        **base_context(request, auth, "Branches", "locations"),
        "locations": items,
        "search": search or "",
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }

    return templates.TemplateResponse(
        request,
        "people/hr/locations.html",
        context,
    )


@router.get("/locations/new", response_class=HTMLResponse)
def new_location_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New location form page."""
    context = {
        **base_context(request, auth, "New Branch", "locations"),
        "location": None,
        "location_types": [t.value for t in LocationType],
        "errors": {},
    }
    return templates.TemplateResponse(
        request,
        "people/hr/location_form.html",
        context,
    )


@router.get("/locations/{location_id}/edit", response_class=HTMLResponse)
def edit_location_form(
    request: Request,
    location_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit location form page."""
    org_id = coerce_uuid(auth.organization_id)
    location = db.get(Location, coerce_uuid(location_id))
    if not location or location.organization_id != org_id:
        return RedirectResponse(url="/people/hr/locations", status_code=303)

    context = {
        **base_context(request, auth, "Edit Branch", "locations"),
        "location": location,
        "location_types": [t.value for t in LocationType],
        "errors": {},
    }
    return templates.TemplateResponse(
        request,
        "people/hr/location_form.html",
        context,
    )


@router.post("/locations/new")
async def create_location(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle new location form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    location_code = (form.get("location_code") or "").strip()
    location_name = (form.get("location_name") or "").strip()
    location_type = (form.get("location_type") or "").strip()
    address_line_1 = (form.get("address_line_1") or "").strip()
    address_line_2 = (form.get("address_line_2") or "").strip()
    city = (form.get("city") or "").strip()
    state_province = (form.get("state_province") or "").strip()
    postal_code = (form.get("postal_code") or "").strip()
    country_code = (form.get("country_code") or "").strip()
    latitude_value = (form.get("latitude") or "").strip()
    longitude_value = (form.get("longitude") or "").strip()
    radius_value = (form.get("geofence_radius_m") or "").strip()
    geofence_enabled = _parse_bool(form.get("geofence_enabled"), True)
    is_active = _parse_bool(form.get("is_active"), True)

    errors = {}
    if not location_code:
        errors["location_code"] = "Required"
    if not location_name:
        errors["location_name"] = "Required"

    latitude = None
    longitude = None
    geofence_radius_m = 500

    if latitude_value:
        try:
            latitude = Decimal(latitude_value)
        except (InvalidOperation, ValueError):
            errors["latitude"] = "Invalid latitude"
    if longitude_value:
        try:
            longitude = Decimal(longitude_value)
        except (InvalidOperation, ValueError):
            errors["longitude"] = "Invalid longitude"
    if radius_value:
        try:
            geofence_radius_m = int(radius_value)
        except (TypeError, ValueError):
            errors["geofence_radius_m"] = "Invalid radius"

    if errors:
        context = {
            **base_context(request, auth, "New Branch", "locations"),
            "location": SimpleNamespace(
                location_code=location_code,
                location_name=location_name,
                location_type=location_type or None,
                address_line_1=address_line_1 or None,
                address_line_2=address_line_2 or None,
                city=city or None,
                state_province=state_province or None,
                postal_code=postal_code or None,
                country_code=country_code or None,
                latitude=latitude,
                longitude=longitude,
                geofence_radius_m=geofence_radius_m,
                geofence_enabled=geofence_enabled,
                is_active=is_active,
            ),
            "location_types": [t.value for t in LocationType],
            "errors": errors,
            "error": "Location code and name are required.",
        }
        return templates.TemplateResponse(
            request,
            "people/hr/location_form.html",
            context,
        )

    org_id = coerce_uuid(auth.organization_id)
    location = Location(
        organization_id=org_id,
        location_code=location_code,
        location_name=location_name,
        location_type=_parse_location_type(location_type),
        address_line_1=address_line_1 or None,
        address_line_2=address_line_2 or None,
        city=city or None,
        state_province=state_province or None,
        postal_code=postal_code or None,
        country_code=country_code or None,
        latitude=latitude,
        longitude=longitude,
        geofence_radius_m=geofence_radius_m,
        geofence_enabled=geofence_enabled,
        is_active=is_active,
    )
    db.add(location)
    db.commit()

    return RedirectResponse(url="/people/hr/locations", status_code=303)


@router.post("/locations/{location_id}/edit")
async def update_location(
    request: Request,
    location_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle location update form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    location_code = (form.get("location_code") or "").strip()
    location_name = (form.get("location_name") or "").strip()
    location_type = (form.get("location_type") or "").strip()
    address_line_1 = (form.get("address_line_1") or "").strip()
    address_line_2 = (form.get("address_line_2") or "").strip()
    city = (form.get("city") or "").strip()
    state_province = (form.get("state_province") or "").strip()
    postal_code = (form.get("postal_code") or "").strip()
    country_code = (form.get("country_code") or "").strip()
    latitude_value = (form.get("latitude") or "").strip()
    longitude_value = (form.get("longitude") or "").strip()
    radius_value = (form.get("geofence_radius_m") or "").strip()
    geofence_enabled = _parse_bool(form.get("geofence_enabled"), True)
    is_active = _parse_bool(form.get("is_active"), True)

    errors = {}
    if not location_code:
        errors["location_code"] = "Required"
    if not location_name:
        errors["location_name"] = "Required"

    latitude = None
    longitude = None
    geofence_radius_m = 500

    if latitude_value:
        try:
            latitude = Decimal(latitude_value)
        except (InvalidOperation, ValueError):
            errors["latitude"] = "Invalid latitude"
    if longitude_value:
        try:
            longitude = Decimal(longitude_value)
        except (InvalidOperation, ValueError):
            errors["longitude"] = "Invalid longitude"
    if radius_value:
        try:
            geofence_radius_m = int(radius_value)
        except (TypeError, ValueError):
            errors["geofence_radius_m"] = "Invalid radius"

    org_id = coerce_uuid(auth.organization_id)
    location = db.get(Location, coerce_uuid(location_id))
    if not location or location.organization_id != org_id:
        return RedirectResponse(url="/people/hr/locations", status_code=303)

    if errors:
        context = {
            **base_context(request, auth, "Edit Branch", "locations"),
            "location": SimpleNamespace(
                location_id=location.location_id,
                location_code=location_code or location.location_code,
                location_name=location_name or location.location_name,
                location_type=location_type or (location.location_type.value if location.location_type else None),
                address_line_1=address_line_1 or location.address_line_1,
                address_line_2=address_line_2 or location.address_line_2,
                city=city or location.city,
                state_province=state_province or location.state_province,
                postal_code=postal_code or location.postal_code,
                country_code=country_code or location.country_code,
                latitude=latitude if latitude_value else location.latitude,
                longitude=longitude if longitude_value else location.longitude,
                geofence_radius_m=geofence_radius_m if radius_value else location.geofence_radius_m,
                geofence_enabled=geofence_enabled,
                is_active=is_active,
            ),
            "location_types": [t.value for t in LocationType],
            "errors": errors,
            "error": "Location code and name are required.",
        }
        return templates.TemplateResponse(
            request,
            "people/hr/location_form.html",
            context,
        )

    location.location_code = location_code
    location.location_name = location_name
    location.location_type = _parse_location_type(location_type)
    location.address_line_1 = address_line_1 or None
    location.address_line_2 = address_line_2 or None
    location.city = city or None
    location.state_province = state_province or None
    location.postal_code = postal_code or None
    location.country_code = country_code or None
    location.latitude = latitude
    location.longitude = longitude
    location.geofence_radius_m = geofence_radius_m
    location.geofence_enabled = geofence_enabled
    location.is_active = is_active

    db.commit()

    return RedirectResponse(url="/people/hr/locations", status_code=303)


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

    department_code = (form.get("department_code") or "").strip()
    department_name = (form.get("department_name") or "").strip()
    description = (form.get("description") or "").strip()
    parent_department_id = (form.get("parent_department_id") or "").strip()
    is_active = _parse_bool(form.get("is_active"), True)

    if not department_code or not department_name:
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)
        department = svc.get_department(coerce_uuid(department_id))
        all_depts = svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items
        parent_options = [d for d in all_depts if d.department_id != department.department_id]
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
                is_active=is_active,
            ),
            "parent_options": parent_options,
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
        parent_department_id=coerce_uuid(parent_department_id) if parent_department_id else None,
        is_active=is_active,
    )

    svc.update_department(coerce_uuid(department_id), data)
    db.commit()

    return RedirectResponse(url="/people/hr/departments", status_code=303)


# =============================================================================
# Designations
# =============================================================================


@router.get("/designations", response_class=HTMLResponse)
def list_designations(
    request: Request,
    search: Optional[str] = None,
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

    designation_code = (form.get("designation_code") or "").strip()
    designation_name = (form.get("designation_name") or "").strip()
    description = (form.get("description") or "").strip()
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

    desig = svc.create_designation(data)
    db.commit()

    return RedirectResponse(url="/people/hr/designations", status_code=303)


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

    designation_code = (form.get("designation_code") or "").strip()
    designation_name = (form.get("designation_name") or "").strip()
    description = (form.get("description") or "").strip()
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
    db.commit()

    return RedirectResponse(url="/people/hr/designations", status_code=303)


# =============================================================================
# Employment Types
# =============================================================================


@router.get("/employment-types", response_class=HTMLResponse)
def list_employment_types(
    request: Request,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Employment types list page."""
    return hr_web_service.list_employment_types_response(request, auth, db, search, page)


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
    return hr_web_service.employment_type_form_response(request, auth, db, employment_type_id)


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

    type_code = (form.get("type_code") or "").strip()
    type_name = (form.get("type_name") or "").strip()
    description = (form.get("description") or "").strip()
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
    db.commit()

    return RedirectResponse(url="/people/hr/employment-types", status_code=303)


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

    type_code = (form.get("type_code") or "").strip()
    type_name = (form.get("type_name") or "").strip()
    description = (form.get("description") or "").strip()
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
    db.commit()

    return RedirectResponse(url="/people/hr/employment-types", status_code=303)


# =============================================================================
# Employee Grades
# =============================================================================


@router.get("/grades", response_class=HTMLResponse)
def list_grades(
    request: Request,
    search: Optional[str] = None,
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

    grade_code = (form.get("grade_code") or "").strip()
    grade_name = (form.get("grade_name") or "").strip()
    description = (form.get("description") or "").strip()
    rank_value = (form.get("rank") or "").strip()
    min_salary_value = (form.get("min_salary") or "").strip()
    max_salary_value = (form.get("max_salary") or "").strip()
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

    if errors["grade_code"] or errors["grade_name"] or errors["rank"] or errors["min_salary"] or errors["max_salary"]:
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
    db.commit()

    return RedirectResponse(url="/people/hr/grades", status_code=303)


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

    grade_code = (form.get("grade_code") or "").strip()
    grade_name = (form.get("grade_name") or "").strip()
    description = (form.get("description") or "").strip()
    rank_value = (form.get("rank") or "").strip()
    min_salary_value = (form.get("min_salary") or "").strip()
    max_salary_value = (form.get("max_salary") or "").strip()
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

    if errors["grade_code"] or errors["grade_name"] or errors["rank"] or errors["min_salary"] or errors["max_salary"]:
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
    db.commit()

    return RedirectResponse(url="/people/hr/grades", status_code=303)
