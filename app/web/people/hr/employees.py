"""Employee CRUD and management routes."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.person import Person, Gender
from app.models.people.hr import EmployeeStatus as EmpStatus
from app.services.people.hr import (
    EmployeeService,
    EmployeeCreateData,
    EmployeeUpdateData,
    TerminationData,
)
from app.services.people.hr.web import hr_web_service
from app.services.common import coerce_uuid
from app.templates import templates
from app.web.deps import base_context, get_db, require_hr_access, WebAuthContext


router = APIRouter(tags=["employees"])


@router.get("/employees", response_class=HTMLResponse)
def list_employees(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    department_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Employee list page."""
    return hr_web_service.list_employees_response(
        request, auth, db, search, status, department_id, page, success, error
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
    confirmation_date = (form.get("confirmation_date") or "").strip()
    notes = (form.get("notes") or "").strip()
    status = (form.get("status") or "DRAFT").strip()
    # Personal contact & emergency
    personal_email = (form.get("personal_email") or "").strip()
    personal_phone = (form.get("personal_phone") or "").strip()
    emergency_contact_name = (form.get("emergency_contact_name") or "").strip()
    emergency_contact_phone = (form.get("emergency_contact_phone") or "").strip()
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

    # Parse dates
    try:
        joining_date = datetime.strptime(date_of_joining, "%Y-%m-%d").date() if date_of_joining else None
    except ValueError:
        joining_date = None

    try:
        dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date() if date_of_birth else None
    except ValueError:
        dob = None

    try:
        probation_date = datetime.strptime(probation_end_date, "%Y-%m-%d").date() if probation_end_date else None
    except ValueError:
        probation_date = None

    try:
        confirm_date = datetime.strptime(confirmation_date, "%Y-%m-%d").date() if confirmation_date else None
    except ValueError:
        confirm_date = None

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
    confirmation_date = (form.get("confirmation_date") or "").strip()
    notes = (form.get("notes") or "").strip()
    status = (form.get("status") or "").strip()
    # Personal contact & emergency
    personal_email = (form.get("personal_email") or "").strip()
    personal_phone = (form.get("personal_phone") or "").strip()
    emergency_contact_name = (form.get("emergency_contact_name") or "").strip()
    emergency_contact_phone = (form.get("emergency_contact_phone") or "").strip()
    # Bank details
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

    try:
        joining_date = datetime.strptime(date_of_joining, "%Y-%m-%d").date() if date_of_joining else None
    except ValueError:
        joining_date = None

    try:
        probation_date = datetime.strptime(probation_end_date, "%Y-%m-%d").date() if probation_end_date else None
    except ValueError:
        probation_date = None

    try:
        confirm_date = datetime.strptime(confirmation_date, "%Y-%m-%d").date() if confirmation_date else None
    except ValueError:
        confirm_date = None

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
    context.update({
        "employee": employee,
        "error": "Please provide a valid resignation date.",
    })
    return templates.TemplateResponse(request, "people/hr/employee_detail.html", context)


@router.post("/employees/{employee_id}/terminate")
async def terminate_employee(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Terminate an employee."""
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
    context.update({
        "employee": employee,
        "error": "Please provide a valid termination date.",
    })
    return templates.TemplateResponse(request, "people/hr/employee_detail.html", context)
