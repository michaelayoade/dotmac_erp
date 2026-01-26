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
    DesignationFilters,
    EmploymentTypeCreateData,
    EmploymentTypeUpdateData,
    EmployeeGradeCreateData,
    EmployeeGradeUpdateData,
    BulkUpdateData,
    # Extended data services
    EmployeeDocumentService,
    EmployeeQualificationService,
    EmployeeCertificationService,
    EmployeeDependentService,
    SkillService,
    EmployeeSkillService,
    # Job description services
    CompetencyService,
    JobDescriptionService,
)
from app.models.people.hr import (
    DocumentType,
    QualificationType,
    RelationshipType,
    SkillCategory,
    CompetencyCategory,
    JobDescriptionStatus,
)
from app.services.common import PaginationParams, ValidationError, coerce_uuid
from app.services.people.hr.web.employee_web import DEFAULT_PAGE_SIZE, DROPDOWN_LIMIT
from app.templates import templates
from app.web.deps import base_context, get_db, require_hr_access, WebAuthContext


router = APIRouter(prefix="/hr", tags=["hr-web"])


def _safe_form_text(value: object | None, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    return default


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
    from datetime import datetime
    from app.models.person import Gender, Person
    from app.models.people.hr import EmployeeStatus as EmpStatus

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    # Person fields
    first_name = _safe_form_text(form.get("first_name"))
    last_name = _safe_form_text(form.get("last_name"))
    email = _safe_form_text(form.get("email"))
    phone = _safe_form_text(form.get("phone"))
    date_of_birth = _safe_form_text(form.get("date_of_birth"))
    gender = _safe_form_text(form.get("gender"))
    address_line1 = _safe_form_text(form.get("address_line1"))
    address_line2 = _safe_form_text(form.get("address_line2"))
    city = _safe_form_text(form.get("city"))
    region = _safe_form_text(form.get("region"))
    postal_code = _safe_form_text(form.get("postal_code"))
    country_code = _safe_form_text(form.get("country_code"))
    # Employee fields
    employee_code = _safe_form_text(form.get("employee_code"))
    department_id = _safe_form_text(form.get("department_id"))
    designation_id = _safe_form_text(form.get("designation_id"))
    employment_type_id = _safe_form_text(form.get("employment_type_id"))
    grade_id = _safe_form_text(form.get("grade_id"))
    reports_to_id = _safe_form_text(form.get("reports_to_id"))
    assigned_location_id = _safe_form_text(form.get("assigned_location_id"))
    default_shift_type_id = _safe_form_text(form.get("default_shift_type_id"))
    linked_person_id = _safe_form_text(form.get("linked_person_id"))
    cost_center_id = _safe_form_text(form.get("cost_center_id"))
    date_of_joining = _safe_form_text(form.get("date_of_joining"))
    probation_end_date = _safe_form_text(form.get("probation_end_date"))
    confirmation_date = _safe_form_text(form.get("confirmation_date"))
    notes = _safe_form_text(form.get("notes"))
    status = _safe_form_text(form.get("status"), "DRAFT")
    # Personal contact & emergency
    personal_email = _safe_form_text(form.get("personal_email"))
    personal_phone = _safe_form_text(form.get("personal_phone"))
    emergency_contact_name = _safe_form_text(form.get("emergency_contact_name"))
    emergency_contact_phone = _safe_form_text(form.get("emergency_contact_phone"))
    # Bank details
    bank_name = _safe_form_text(form.get("bank_name"))
    bank_account_name = _safe_form_text(form.get("bank_account_name"))
    bank_account_number = _safe_form_text(form.get("bank_account_number"))
    bank_branch_code = _safe_form_text(form.get("bank_branch_code"))

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

    try:
        confirm_date = (
            datetime.strptime(confirmation_date, "%Y-%m-%d").date()
            if confirmation_date
            else None
        )
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

    from app.models.people.hr import EmployeeStatus as EmpStatus

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    employee_code = _safe_form_text(form.get("employee_code"))
    department_id = _safe_form_text(form.get("department_id"))
    designation_id = _safe_form_text(form.get("designation_id"))
    employment_type_id = _safe_form_text(form.get("employment_type_id"))
    grade_id = _safe_form_text(form.get("grade_id"))
    reports_to_id = _safe_form_text(form.get("reports_to_id"))
    assigned_location_id = _safe_form_text(form.get("assigned_location_id"))
    default_shift_type_id = _safe_form_text(form.get("default_shift_type_id"))
    linked_person_id = _safe_form_text(form.get("linked_person_id"))
    cost_center_id = _safe_form_text(form.get("cost_center_id"))
    date_of_joining = _safe_form_text(form.get("date_of_joining"))
    probation_end_date = _safe_form_text(form.get("probation_end_date"))
    confirmation_date = _safe_form_text(form.get("confirmation_date"))
    notes = _safe_form_text(form.get("notes"))
    status = _safe_form_text(form.get("status"))
    # Personal contact & emergency
    personal_email = _safe_form_text(form.get("personal_email"))
    personal_phone = _safe_form_text(form.get("personal_phone"))
    emergency_contact_name = _safe_form_text(form.get("emergency_contact_name"))
    emergency_contact_phone = _safe_form_text(form.get("emergency_contact_phone"))
    # Bank details
    bank_name = _safe_form_text(form.get("bank_name"))
    bank_account_name = _safe_form_text(form.get("bank_account_name"))
    bank_account_number = _safe_form_text(form.get("bank_account_number"))
    bank_branch_code = _safe_form_text(form.get("bank_branch_code"))

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

    try:
        confirm_date = (
            datetime.strptime(confirmation_date, "%Y-%m-%d").date()
            if confirmation_date
            else None
        )
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
    reason = _safe_form_text(form.get("reason"))

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
    date_of_leaving = _safe_form_text(form.get("date_of_leaving"))

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
    date_of_leaving = _safe_form_text(form.get("date_of_leaving"))
    reason = _safe_form_text(form.get("reason"))

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


# =========================================================================
# Onboarding routes
# =========================================================================

@router.get("/employees/{employee_id}/onboarding/new", response_class=HTMLResponse)
def new_onboarding_form(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Render form to create onboarding checklist for employee."""
    from app.models.people.hr.checklist_template import ChecklistTemplate, ChecklistTemplateType

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)
    employee = svc.get_employee(employee_id)
    person = db.get(Person, employee.person_id)

    # Get onboarding templates
    templates_list = (
        db.query(ChecklistTemplate)
        .filter(
            ChecklistTemplate.organization_id == org_id,
            ChecklistTemplate.template_type == ChecklistTemplateType.ONBOARDING,
            ChecklistTemplate.is_active == True,
        )
        .order_by(ChecklistTemplate.template_name)
        .all()
    )

    context = base_context(request, auth, "New Onboarding", "employees", db=db)
    context["employee"] = employee
    context["person"] = person
    context["templates"] = templates_list
    return templates.TemplateResponse(request, "people/hr/onboarding_form.html", context)


@router.post("/employees/{employee_id}/onboarding/new")
async def create_onboarding(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create onboarding record for an employee."""
    from app.services.people.hr.lifecycle import LifecycleService
    from app.models.people.hr.checklist_template import ChecklistTemplate

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    template_id = _safe_form_text(form.get("template_id"))
    notes = _safe_form_text(form.get("notes"))

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)
    lifecycle_svc = LifecycleService(db)
    employee = svc.get_employee(employee_id)

    # Build activities from template if selected
    activities = []
    template_name = None
    if template_id:
        template = db.get(ChecklistTemplate, coerce_uuid(template_id))
        if template:
            template_name = template.template_name
            for item in sorted(template.items, key=lambda x: x.sequence):
                activities.append({
                    "activity_name": item.item_name,
                    "sequence": item.sequence,
                })

    lifecycle_svc.create_onboarding(
        org_id,
        employee_id=employee_id,
        date_of_joining=employee.date_of_joining,
        department_id=employee.department_id,
        designation_id=employee.designation_id,
        template_name=template_name,
        notes=notes or None,
        activities=activities,
    )
    db.commit()

    return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)


@router.post("/employees/{employee_id}/onboarding/start")
async def start_onboarding(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Start the onboarding process for an employee."""
    from app.services.people.hr.lifecycle import LifecycleService

    org_id = coerce_uuid(auth.organization_id)
    lifecycle_svc = LifecycleService(db)

    onboarding = lifecycle_svc.get_onboarding_for_employee(org_id, employee_id)
    if onboarding:
        lifecycle_svc.start_onboarding(org_id, onboarding.onboarding_id)
        db.commit()

    return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)


@router.post("/employees/{employee_id}/onboarding/activity/{activity_id}/toggle")
async def toggle_onboarding_activity(
    request: Request,
    employee_id: UUID,
    activity_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Toggle an onboarding activity completion status."""
    from app.services.people.hr.lifecycle import LifecycleService

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    completed = _parse_bool(form.get("completed"), True)

    org_id = coerce_uuid(auth.organization_id)
    lifecycle_svc = LifecycleService(db)

    onboarding = lifecycle_svc.get_onboarding_for_employee(org_id, employee_id)
    if onboarding:
        lifecycle_svc.complete_onboarding_activity(
            org_id, onboarding.onboarding_id, activity_id, completed
        )
        db.commit()

    return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)


@router.post("/employees/{employee_id}/onboarding/complete")
async def complete_onboarding(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Mark onboarding as complete."""
    from app.services.people.hr.lifecycle import LifecycleService

    org_id = coerce_uuid(auth.organization_id)
    lifecycle_svc = LifecycleService(db)

    onboarding = lifecycle_svc.get_onboarding_for_employee(org_id, employee_id)
    if onboarding:
        lifecycle_svc.complete_onboarding(org_id, onboarding.onboarding_id)
        db.commit()

    return RedirectResponse(url=f"/people/hr/employees/{employee_id}", status_code=303)


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

    username = _safe_form_text(form.get("username"))
    password = _safe_form_text(form.get("password"))
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

    person_id = _safe_form_text(form.get("person_id"))
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
    department_id = _safe_form_text(form.get("department_id"))
    designation_id = _safe_form_text(form.get("designation_id"))
    reports_to_id = _safe_form_text(form.get("reports_to_id"))
    status = _safe_form_text(form.get("status"))

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

    valid_ids = []
    for emp_id in employee_ids:
        try:
            valid_ids.append(coerce_uuid(emp_id))
        except Exception:
            pass

    if not valid_ids:
        return RedirectResponse(
            url="/people/hr/employees?error=No+valid+employees+selected",
            status_code=303
        )

    data = BulkUpdateData(
        ids=valid_ids,
        department_id=coerce_uuid(department_id) if department_id else None,
        designation_id=coerce_uuid(designation_id) if designation_id else None,
        reports_to_id=coerce_uuid(reports_to_id) if reports_to_id else None,
        status=status_enum,
    )

    svc.bulk_update(data)
    db.commit()

    from urllib.parse import quote
    success_msg = quote(f"Successfully updated {len(valid_ids)} employee(s)")
    return RedirectResponse(url=f"/people/hr/employees?success={success_msg}", status_code=303)


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

    valid_ids = []
    for emp_id in employee_ids:
        try:
            valid_ids.append(coerce_uuid(emp_id))
        except Exception:
            pass

    if not valid_ids:
        return RedirectResponse(
            url="/people/hr/employees?error=No+valid+employees+selected",
            status_code=303
        )

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)
    svc.bulk_delete(valid_ids)
    db.commit()

    from urllib.parse import quote
    success_msg = quote(f"Successfully deleted {len(valid_ids)} employee(s)")
    return RedirectResponse(url=f"/people/hr/employees?success={success_msg}", status_code=303)


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

    department_code = _safe_form_text(form.get("department_code"))
    department_name = _safe_form_text(form.get("department_name"))
    description = _safe_form_text(form.get("description"))
    parent_department_id = _safe_form_text(form.get("parent_department_id"))
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

    location_code = _safe_form_text(form.get("location_code"))
    location_name = _safe_form_text(form.get("location_name"))
    location_type = _safe_form_text(form.get("location_type"))
    address_line_1 = _safe_form_text(form.get("address_line_1"))
    address_line_2 = _safe_form_text(form.get("address_line_2"))
    city = _safe_form_text(form.get("city"))
    state_province = _safe_form_text(form.get("state_province"))
    postal_code = _safe_form_text(form.get("postal_code"))
    country_code = _safe_form_text(form.get("country_code"))
    latitude_value = _safe_form_text(form.get("latitude"))
    longitude_value = _safe_form_text(form.get("longitude"))
    radius_value = _safe_form_text(form.get("geofence_radius_m"))
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

    location_code = _safe_form_text(form.get("location_code"))
    location_name = _safe_form_text(form.get("location_name"))
    location_type = _safe_form_text(form.get("location_type"))
    address_line_1 = _safe_form_text(form.get("address_line_1"))
    address_line_2 = _safe_form_text(form.get("address_line_2"))
    city = _safe_form_text(form.get("city"))
    state_province = _safe_form_text(form.get("state_province"))
    postal_code = _safe_form_text(form.get("postal_code"))
    country_code = _safe_form_text(form.get("country_code"))
    latitude_value = _safe_form_text(form.get("latitude"))
    longitude_value = _safe_form_text(form.get("longitude"))
    radius_value = _safe_form_text(form.get("geofence_radius_m"))
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

    department_code = _safe_form_text(form.get("department_code"))
    department_name = _safe_form_text(form.get("department_name"))
    description = _safe_form_text(form.get("description"))
    parent_department_id = _safe_form_text(form.get("parent_department_id"))
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

    designation_code = _safe_form_text(form.get("designation_code"))
    designation_name = _safe_form_text(form.get("designation_name"))
    description = _safe_form_text(form.get("description"))
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

    designation_code = _safe_form_text(form.get("designation_code"))
    designation_name = _safe_form_text(form.get("designation_name"))
    description = _safe_form_text(form.get("description"))
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

    type_code = _safe_form_text(form.get("type_code"))
    type_name = _safe_form_text(form.get("type_name"))
    description = _safe_form_text(form.get("description"))
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

    type_code = _safe_form_text(form.get("type_code"))
    type_name = _safe_form_text(form.get("type_name"))
    description = _safe_form_text(form.get("description"))
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

    grade_code = _safe_form_text(form.get("grade_code"))
    grade_name = _safe_form_text(form.get("grade_name"))
    description = _safe_form_text(form.get("description"))
    rank_value = _safe_form_text(form.get("rank"))
    min_salary_value = _safe_form_text(form.get("min_salary"))
    max_salary_value = _safe_form_text(form.get("max_salary"))
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

    grade_code = _safe_form_text(form.get("grade_code"))
    grade_name = _safe_form_text(form.get("grade_name"))
    description = _safe_form_text(form.get("description"))
    rank_value = _safe_form_text(form.get("rank"))
    min_salary_value = _safe_form_text(form.get("min_salary"))
    max_salary_value = _safe_form_text(form.get("max_salary"))
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


# =============================================================================
# Employee Documents
# =============================================================================


@router.get("/employees/{employee_id}/documents", response_class=HTMLResponse)
def list_employee_documents(
    request: Request,
    employee_id: str,
    document_type: Optional[str] = None,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List documents for an employee."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)

    emp_svc = EmployeeService(db, org_id)
    doc_svc = EmployeeDocumentService(db, org_id)

    try:
        employee = emp_svc.get_employee(emp_id)
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    doc_type = DocumentType(document_type) if document_type else None
    documents = doc_svc.list_documents(emp_id, document_type=doc_type)

    context = base_context(request, auth, f"Documents - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "documents": documents,
        "document_types": list(DocumentType),
        "selected_type": document_type,
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/employee/documents.html", context)


@router.get("/employees/{employee_id}/documents/new", response_class=HTMLResponse)
def new_document_form(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New document upload form."""
    org_id = coerce_uuid(auth.organization_id)
    emp_svc = EmployeeService(db, org_id)

    try:
        employee = emp_svc.get_employee(coerce_uuid(employee_id))
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    context = base_context(request, auth, f"Upload Document - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "document_types": list(DocumentType),
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/employee/document_form.html", context)


@router.post("/employees/{employee_id}/documents/new", response_class=HTMLResponse)
def create_document(
    request: Request,
    employee_id: str,
    document_type: str = Form(...),
    document_name: str = Form(...),
    file_path: str = Form(...),
    file_name: str = Form(...),
    description: Optional[str] = Form(None),
    issue_date: Optional[str] = Form(None),
    expiry_date: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new document record."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)
    doc_svc = EmployeeDocumentService(db, org_id)

    from datetime import datetime as dt

    try:
        doc_svc.create_document(
            employee_id=emp_id,
            document_type=DocumentType(document_type),
            document_name=document_name,
            file_path=file_path,
            file_name=file_name,
            description=description or None,
            issue_date=dt.strptime(issue_date, "%Y-%m-%d").date() if issue_date else None,
            expiry_date=dt.strptime(expiry_date, "%Y-%m-%d").date() if expiry_date else None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/documents?success=Document+uploaded",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        emp_svc = EmployeeService(db, org_id)
        employee = emp_svc.get_employee(emp_id)
        context = base_context(request, auth, f"Upload Document - {employee.full_name}", "employees", db=db)
        context.update({
            "employee": employee,
            "document_types": list(DocumentType),
            "form_data": {
                "document_type": document_type,
                "document_name": document_name,
                "file_path": file_path,
                "file_name": file_name,
                "description": description,
                "issue_date": issue_date,
                "expiry_date": expiry_date,
            },
            "error": str(e),
        })
        return templates.TemplateResponse(request, "people/hr/employee/document_form.html", context)


@router.post("/employees/{employee_id}/documents/{document_id}/verify", response_class=HTMLResponse)
def verify_document(
    request: Request,
    employee_id: str,
    document_id: str,
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Verify a document."""
    org_id = coerce_uuid(auth.organization_id)
    doc_svc = EmployeeDocumentService(db, org_id)

    try:
        verifier = db.scalar(
            select(Employee).where(
                Employee.organization_id == org_id,
                Employee.person_id == auth.user_id,
            )
        )
        verifier_id = verifier.employee_id if verifier else None

        doc_svc.verify_document(
            document_id=coerce_uuid(document_id),
            verified_by_id=verifier_id,
            notes=notes,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/documents?success=Document+verified",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/documents?error={str(e)}",
            status_code=303,
        )


@router.post("/employees/{employee_id}/documents/{document_id}/delete", response_class=HTMLResponse)
def delete_document(
    request: Request,
    employee_id: str,
    document_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a document."""
    org_id = coerce_uuid(auth.organization_id)
    doc_svc = EmployeeDocumentService(db, org_id)

    try:
        doc_svc.delete_document(coerce_uuid(document_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/documents?success=Document+deleted",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/documents?error={str(e)}",
            status_code=303,
        )


# =============================================================================
# Employee Qualifications
# =============================================================================


@router.get("/employees/{employee_id}/qualifications", response_class=HTMLResponse)
def list_employee_qualifications(
    request: Request,
    employee_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List qualifications for an employee."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)

    emp_svc = EmployeeService(db, org_id)
    qual_svc = EmployeeQualificationService(db, org_id)

    try:
        employee = emp_svc.get_employee(emp_id)
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    qualifications = qual_svc.list_qualifications(emp_id)

    context = base_context(request, auth, f"Qualifications - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "qualifications": qualifications,
        "qualification_types": list(QualificationType),
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/employee/qualifications.html", context)


@router.get("/employees/{employee_id}/qualifications/new", response_class=HTMLResponse)
def new_qualification_form(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New qualification form."""
    org_id = coerce_uuid(auth.organization_id)
    emp_svc = EmployeeService(db, org_id)

    try:
        employee = emp_svc.get_employee(coerce_uuid(employee_id))
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    context = base_context(request, auth, f"Add Qualification - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "qualification_types": list(QualificationType),
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/employee/qualification_form.html", context)


@router.post("/employees/{employee_id}/qualifications/new", response_class=HTMLResponse)
def create_qualification(
    request: Request,
    employee_id: str,
    qualification_type: str = Form(...),
    qualification_name: str = Form(...),
    institution_name: str = Form(...),
    field_of_study: Optional[str] = Form(None),
    start_date: Optional[str] = Form(None),
    end_date: Optional[str] = Form(None),
    is_ongoing: Optional[str] = Form(None),
    grade: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new qualification."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)
    qual_svc = EmployeeQualificationService(db, org_id)

    from datetime import datetime as dt

    try:
        qual_svc.create_qualification(
            employee_id=emp_id,
            qualification_type=QualificationType(qualification_type),
            qualification_name=qualification_name,
            institution_name=institution_name,
            field_of_study=field_of_study or None,
            start_date=dt.strptime(start_date, "%Y-%m-%d").date() if start_date else None,
            end_date=dt.strptime(end_date, "%Y-%m-%d").date() if end_date else None,
            is_ongoing=_parse_bool(is_ongoing),
            grade=grade or None,
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/qualifications?success=Qualification+added",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/qualifications?error={str(e)}",
            status_code=303,
        )


@router.post("/employees/{employee_id}/qualifications/{qualification_id}/delete")
def delete_qualification(
    request: Request,
    employee_id: str,
    qualification_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a qualification."""
    org_id = coerce_uuid(auth.organization_id)
    qual_svc = EmployeeQualificationService(db, org_id)

    try:
        qual_svc.delete_qualification(coerce_uuid(qualification_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/qualifications?success=Qualification+deleted",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/qualifications?error={str(e)}",
            status_code=303,
        )


# =============================================================================
# Employee Certifications
# =============================================================================


@router.get("/employees/{employee_id}/certifications", response_class=HTMLResponse)
def list_employee_certifications(
    request: Request,
    employee_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List certifications for an employee."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)

    emp_svc = EmployeeService(db, org_id)
    cert_svc = EmployeeCertificationService(db, org_id)

    try:
        employee = emp_svc.get_employee(emp_id)
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    certifications = cert_svc.list_certifications(emp_id)

    context = base_context(request, auth, f"Certifications - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "certifications": certifications,
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/employee/certifications.html", context)


@router.get("/employees/{employee_id}/certifications/new", response_class=HTMLResponse)
def new_certification_form(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New certification form."""
    org_id = coerce_uuid(auth.organization_id)
    emp_svc = EmployeeService(db, org_id)

    try:
        employee = emp_svc.get_employee(coerce_uuid(employee_id))
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    context = base_context(request, auth, f"Add Certification - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/employee/certification_form.html", context)


@router.post("/employees/{employee_id}/certifications/new", response_class=HTMLResponse)
def create_certification(
    request: Request,
    employee_id: str,
    certification_name: str = Form(...),
    issuing_authority: str = Form(...),
    issue_date: str = Form(...),
    expiry_date: Optional[str] = Form(None),
    does_not_expire: Optional[str] = Form(None),
    credential_id: Optional[str] = Form(None),
    credential_url: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new certification."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)
    cert_svc = EmployeeCertificationService(db, org_id)

    from datetime import datetime as dt

    try:
        cert_svc.create_certification(
            employee_id=emp_id,
            certification_name=certification_name,
            issuing_authority=issuing_authority,
            issue_date=dt.strptime(issue_date, "%Y-%m-%d").date(),
            expiry_date=dt.strptime(expiry_date, "%Y-%m-%d").date() if expiry_date else None,
            does_not_expire=_parse_bool(does_not_expire),
            credential_id=credential_id or None,
            credential_url=credential_url or None,
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/certifications?success=Certification+added",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/certifications?error={str(e)}",
            status_code=303,
        )


@router.post("/employees/{employee_id}/certifications/{certification_id}/delete")
def delete_certification(
    request: Request,
    employee_id: str,
    certification_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a certification."""
    org_id = coerce_uuid(auth.organization_id)
    cert_svc = EmployeeCertificationService(db, org_id)

    try:
        cert_svc.delete_certification(coerce_uuid(certification_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/certifications?success=Certification+deleted",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/certifications?error={str(e)}",
            status_code=303,
        )


# =============================================================================
# Employee Dependents
# =============================================================================


@router.get("/employees/{employee_id}/dependents", response_class=HTMLResponse)
def list_employee_dependents(
    request: Request,
    employee_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List dependents for an employee."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)

    emp_svc = EmployeeService(db, org_id)
    dep_svc = EmployeeDependentService(db, org_id)

    try:
        employee = emp_svc.get_employee(emp_id)
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    dependents = dep_svc.list_dependents(emp_id)

    context = base_context(request, auth, f"Dependents - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "dependents": dependents,
        "relationship_types": list(RelationshipType),
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/employee/dependents.html", context)


@router.get("/employees/{employee_id}/dependents/new", response_class=HTMLResponse)
def new_dependent_form(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New dependent form."""
    org_id = coerce_uuid(auth.organization_id)
    emp_svc = EmployeeService(db, org_id)

    try:
        employee = emp_svc.get_employee(coerce_uuid(employee_id))
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    context = base_context(request, auth, f"Add Dependent - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "relationship_types": list(RelationshipType),
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/employee/dependent_form.html", context)


@router.post("/employees/{employee_id}/dependents/new", response_class=HTMLResponse)
def create_dependent(
    request: Request,
    employee_id: str,
    full_name: str = Form(...),
    relationship: str = Form(...),
    date_of_birth: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    is_emergency_contact: Optional[str] = Form(None),
    emergency_contact_priority: Optional[str] = Form(None),
    is_beneficiary: Optional[str] = Form(None),
    beneficiary_percentage: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new dependent."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)
    dep_svc = EmployeeDependentService(db, org_id)

    from datetime import datetime as dt

    try:
        dep_svc.create_dependent(
            employee_id=emp_id,
            full_name=full_name,
            relationship=RelationshipType(relationship),
            date_of_birth=dt.strptime(date_of_birth, "%Y-%m-%d").date() if date_of_birth else None,
            gender=gender or None,
            phone=phone or None,
            email=email or None,
            is_emergency_contact=_parse_bool(is_emergency_contact),
            emergency_contact_priority=int(emergency_contact_priority) if emergency_contact_priority else None,
            is_beneficiary=_parse_bool(is_beneficiary),
            beneficiary_percentage=float(beneficiary_percentage) if beneficiary_percentage else None,
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/dependents?success=Dependent+added",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/dependents?error={str(e)}",
            status_code=303,
        )


@router.post("/employees/{employee_id}/dependents/{dependent_id}/delete")
def delete_dependent(
    request: Request,
    employee_id: str,
    dependent_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a dependent."""
    org_id = coerce_uuid(auth.organization_id)
    dep_svc = EmployeeDependentService(db, org_id)

    try:
        dep_svc.delete_dependent(coerce_uuid(dependent_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/dependents?success=Dependent+deleted",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/dependents?error={str(e)}",
            status_code=303,
        )


# =============================================================================
# Employee Skills
# =============================================================================


@router.get("/employees/{employee_id}/skills", response_class=HTMLResponse)
def list_employee_skills(
    request: Request,
    employee_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List skills for an employee."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)

    emp_svc = EmployeeService(db, org_id)
    skill_svc = EmployeeSkillService(db, org_id)

    try:
        employee = emp_svc.get_employee(emp_id)
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    skills = skill_svc.list_employee_skills(emp_id)

    context = base_context(request, auth, f"Skills - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "employee_skills": skills,
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/employee/skills.html", context)


@router.get("/employees/{employee_id}/skills/new", response_class=HTMLResponse)
def new_skill_form(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Add skill form."""
    org_id = coerce_uuid(auth.organization_id)
    emp_svc = EmployeeService(db, org_id)
    catalog_svc = SkillService(db, org_id)

    try:
        employee = emp_svc.get_employee(coerce_uuid(employee_id))
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    skills = catalog_svc.list_skills()

    context = base_context(request, auth, f"Add Skill - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "skills": skills,
        "skill_categories": list(SkillCategory),
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/employee/skill_form.html", context)


@router.post("/employees/{employee_id}/skills/new", response_class=HTMLResponse)
def add_employee_skill(
    request: Request,
    employee_id: str,
    skill_id: str = Form(...),
    proficiency_level: int = Form(...),
    years_experience: Optional[str] = Form(None),
    is_primary: Optional[str] = Form(None),
    is_certified: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Add a skill to an employee."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)
    skill_svc = EmployeeSkillService(db, org_id)

    try:
        skill_svc.add_skill(
            employee_id=emp_id,
            skill_id=coerce_uuid(skill_id),
            proficiency_level=proficiency_level,
            years_experience=float(years_experience) if years_experience else None,
            is_primary=_parse_bool(is_primary),
            is_certified=_parse_bool(is_certified),
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/skills?success=Skill+added",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/skills?error={str(e)}",
            status_code=303,
        )


@router.post("/employees/{employee_id}/skills/{employee_skill_id}/delete")
def remove_employee_skill(
    request: Request,
    employee_id: str,
    employee_skill_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Remove a skill from an employee."""
    org_id = coerce_uuid(auth.organization_id)
    skill_svc = EmployeeSkillService(db, org_id)

    try:
        skill_svc.remove_skill(coerce_uuid(employee_skill_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/skills?success=Skill+removed",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/skills?error={str(e)}",
            status_code=303,
        )


# =============================================================================
# Skills Catalog
# =============================================================================


@router.get("/skills", response_class=HTMLResponse)
def list_skills(
    request: Request,
    category: Optional[str] = None,
    search: Optional[str] = None,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Skills catalog page."""
    org_id = coerce_uuid(auth.organization_id)
    skill_svc = SkillService(db, org_id)

    cat = SkillCategory(category) if category else None
    skills = skill_svc.list_skills(category=cat, search=search, active_only=False)

    context = base_context(request, auth, "Skills Catalog", "skills", db=db)
    context.update({
        "skills": skills,
        "categories": list(SkillCategory),
        "selected_category": category,
        "search": search,
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/skills.html", context)


@router.get("/skills/new", response_class=HTMLResponse)
def new_skill_catalog_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New skill form."""
    context = base_context(request, auth, "Add Skill", "skills", db=db)
    context.update({
        "categories": list(SkillCategory),
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/skill_catalog_form.html", context)


@router.post("/skills/new", response_class=HTMLResponse)
def create_skill(
    request: Request,
    skill_name: str = Form(...),
    category: str = Form(...),
    description: Optional[str] = Form(None),
    is_language: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new skill in the catalog."""
    org_id = coerce_uuid(auth.organization_id)
    skill_svc = SkillService(db, org_id)

    try:
        skill_svc.create_skill(
            skill_name=skill_name,
            category=SkillCategory(category),
            description=description or None,
            is_language=_parse_bool(is_language),
        )
        db.commit()
        return RedirectResponse(url="/people/hr/skills?success=Skill+created", status_code=303)
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "Add Skill", "skills", db=db)
        context.update({
            "categories": list(SkillCategory),
            "form_data": {
                "skill_name": skill_name,
                "category": category,
                "description": description,
                "is_language": is_language,
            },
            "error": str(e),
        })
        return templates.TemplateResponse(request, "people/hr/skill_catalog_form.html", context)


# =============================================================================
# Competencies
# =============================================================================


@router.get("/competencies", response_class=HTMLResponse)
def list_competencies(
    request: Request,
    category: Optional[str] = None,
    search: Optional[str] = None,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Competency catalog page."""
    org_id = coerce_uuid(auth.organization_id)
    comp_svc = CompetencyService(db, org_id)

    cat = CompetencyCategory(category) if category else None
    result = comp_svc.list_competencies(category=cat, is_active=None, search=search)

    context = base_context(request, auth, "Competencies", "competencies", db=db)
    context.update({
        "competencies": result.items,
        "categories": list(CompetencyCategory),
        "selected_category": category,
        "search": search,
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/competencies.html", context)


@router.get("/competencies/new", response_class=HTMLResponse)
def new_competency_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New competency form."""
    context = base_context(request, auth, "Add Competency", "competencies", db=db)
    context.update({
        "categories": list(CompetencyCategory),
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/competency_form.html", context)


@router.post("/competencies/new", response_class=HTMLResponse)
def create_competency(
    request: Request,
    competency_code: str = Form(...),
    competency_name: str = Form(...),
    category: str = Form(...),
    description: Optional[str] = Form(None),
    level_1_description: Optional[str] = Form(None),
    level_2_description: Optional[str] = Form(None),
    level_3_description: Optional[str] = Form(None),
    level_4_description: Optional[str] = Form(None),
    level_5_description: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new competency."""
    org_id = coerce_uuid(auth.organization_id)
    comp_svc = CompetencyService(db, org_id, auth.principal)

    try:
        comp_svc.create_competency(
            competency_code=competency_code,
            competency_name=competency_name,
            category=CompetencyCategory(category),
            description=description or None,
            level_1_description=level_1_description or None,
            level_2_description=level_2_description or None,
            level_3_description=level_3_description or None,
            level_4_description=level_4_description or None,
            level_5_description=level_5_description or None,
        )
        db.commit()
        return RedirectResponse(url="/people/hr/competencies?success=Competency+created", status_code=303)
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "Add Competency", "competencies", db=db)
        context.update({
            "categories": list(CompetencyCategory),
            "form_data": {
                "competency_code": competency_code,
                "competency_name": competency_name,
                "category": category,
                "description": description,
                "level_1_description": level_1_description,
                "level_2_description": level_2_description,
                "level_3_description": level_3_description,
                "level_4_description": level_4_description,
                "level_5_description": level_5_description,
            },
            "error": str(e),
        })
        return templates.TemplateResponse(request, "people/hr/competency_form.html", context)


@router.get("/competencies/{competency_id}", response_class=HTMLResponse)
def view_competency(
    request: Request,
    competency_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View competency detail."""
    org_id = coerce_uuid(auth.organization_id)
    comp_svc = CompetencyService(db, org_id)

    competency = comp_svc.get_competency(coerce_uuid(competency_id))
    if not competency:
        return RedirectResponse(url="/people/hr/competencies?error=Competency+not+found", status_code=303)

    context = base_context(request, auth, competency.competency_name, "competencies", db=db)
    context.update({
        "competency": competency,
    })
    return templates.TemplateResponse(request, "people/hr/competency_detail.html", context)


@router.get("/competencies/{competency_id}/edit", response_class=HTMLResponse)
def edit_competency_form(
    request: Request,
    competency_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit competency form."""
    org_id = coerce_uuid(auth.organization_id)
    comp_svc = CompetencyService(db, org_id)

    competency = comp_svc.get_competency(coerce_uuid(competency_id))
    if not competency:
        return RedirectResponse(url="/people/hr/competencies?error=Competency+not+found", status_code=303)

    context = base_context(request, auth, f"Edit {competency.competency_name}", "competencies", db=db)
    context.update({
        "competency": competency,
        "categories": list(CompetencyCategory),
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/competency_form.html", context)


@router.post("/competencies/{competency_id}/edit", response_class=HTMLResponse)
def update_competency(
    request: Request,
    competency_id: str,
    competency_code: str = Form(...),
    competency_name: str = Form(...),
    category: str = Form(...),
    description: Optional[str] = Form(None),
    level_1_description: Optional[str] = Form(None),
    level_2_description: Optional[str] = Form(None),
    level_3_description: Optional[str] = Form(None),
    level_4_description: Optional[str] = Form(None),
    level_5_description: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a competency."""
    org_id = coerce_uuid(auth.organization_id)
    comp_svc = CompetencyService(db, org_id, auth.principal)

    try:
        comp_svc.update_competency(
            coerce_uuid(competency_id),
            {
                "competency_code": competency_code,
                "competency_name": competency_name,
                "category": CompetencyCategory(category),
                "description": description or None,
                "level_1_description": level_1_description or None,
                "level_2_description": level_2_description or None,
                "level_3_description": level_3_description or None,
                "level_4_description": level_4_description or None,
                "level_5_description": level_5_description or None,
                "is_active": _parse_bool(is_active, True),
            },
        )
        db.commit()
        return RedirectResponse(url="/people/hr/competencies?success=Competency+updated", status_code=303)
    except Exception as e:
        db.rollback()
        competency = comp_svc.get_competency(coerce_uuid(competency_id))
        context = base_context(request, auth, f"Edit Competency", "competencies", db=db)
        context.update({
            "competency": competency,
            "categories": list(CompetencyCategory),
            "form_data": {
                "competency_code": competency_code,
                "competency_name": competency_name,
                "category": category,
                "description": description,
            },
            "error": str(e),
        })
        return templates.TemplateResponse(request, "people/hr/competency_form.html", context)


# =============================================================================
# Job Descriptions
# =============================================================================


@router.get("/job-descriptions", response_class=HTMLResponse)
def list_job_descriptions(
    request: Request,
    status: Optional[str] = None,
    department_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Job description list page."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id)
    org_svc = OrganizationService(db, org_id)

    jd_status = JobDescriptionStatus(status) if status else None
    dept_id = coerce_uuid(department_id) if department_id else None

    pagination = PaginationParams.from_page(page, per_page=DEFAULT_PAGE_SIZE)
    result = jd_svc.list_job_descriptions(
        status=jd_status,
        department_id=dept_id,
        search=search,
        pagination=pagination,
    )

    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "Job Descriptions", "job-descriptions", db=db)
    context.update({
        "job_descriptions": result.items,
        "pagination": result,
        "statuses": list(JobDescriptionStatus),
        "departments": departments,
        "selected_status": status,
        "selected_department_id": department_id,
        "search": search,
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/job_descriptions.html", context)


@router.get("/job-descriptions/new", response_class=HTMLResponse)
def new_job_description_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New job description form."""
    org_id = coerce_uuid(auth.organization_id)
    org_svc = OrganizationService(db, org_id)

    designations = org_svc.list_designations(
        DesignationFilters(is_active=True),
        PaginationParams(limit=200),
    ).items
    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "New Job Description", "job-descriptions", db=db)
    context.update({
        "designations": designations,
        "departments": departments,
        "statuses": list(JobDescriptionStatus),
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/job_description_form.html", context)


@router.post("/job-descriptions/new", response_class=HTMLResponse)
def create_job_description(
    request: Request,
    jd_code: str = Form(...),
    job_title: str = Form(...),
    designation_id: str = Form(...),
    department_id: Optional[str] = Form(None),
    summary: Optional[str] = Form(None),
    purpose: Optional[str] = Form(None),
    key_responsibilities: Optional[str] = Form(None),
    education_requirements: Optional[str] = Form(None),
    experience_requirements: Optional[str] = Form(None),
    min_years_experience: Optional[int] = Form(None),
    max_years_experience: Optional[int] = Form(None),
    technical_skills: Optional[str] = Form(None),
    certifications_required: Optional[str] = Form(None),
    certifications_preferred: Optional[str] = Form(None),
    work_location: Optional[str] = Form(None),
    travel_requirements: Optional[str] = Form(None),
    reports_to: Optional[str] = Form(None),
    direct_reports: Optional[str] = Form(None),
    status: str = Form("draft"),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new job description."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id, auth.principal)
    org_svc = OrganizationService(db, org_id)

    try:
        jd_svc.create_job_description(
            jd_code=jd_code,
            job_title=job_title,
            designation_id=coerce_uuid(designation_id),
            department_id=coerce_uuid(department_id) if department_id else None,
            summary=summary or None,
            purpose=purpose or None,
            key_responsibilities=key_responsibilities or None,
            education_requirements=education_requirements or None,
            experience_requirements=experience_requirements or None,
            min_years_experience=min_years_experience,
            max_years_experience=max_years_experience,
            technical_skills=technical_skills or None,
            certifications_required=certifications_required or None,
            certifications_preferred=certifications_preferred or None,
            work_location=work_location or None,
            travel_requirements=travel_requirements or None,
            reports_to=reports_to or None,
            direct_reports=direct_reports or None,
            status=JobDescriptionStatus(status),
        )
        db.commit()
        return RedirectResponse(url="/people/hr/job-descriptions?success=Job+description+created", status_code=303)
    except Exception as e:
        db.rollback()
        designations = org_svc.list_designations(
            DesignationFilters(is_active=True),
            PaginationParams(limit=200),
        ).items
        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=200),
        ).items

        context = base_context(request, auth, "New Job Description", "job-descriptions", db=db)
        context.update({
            "designations": designations,
            "departments": departments,
            "statuses": list(JobDescriptionStatus),
            "form_data": {
                "jd_code": jd_code,
                "job_title": job_title,
                "designation_id": designation_id,
                "department_id": department_id,
                "summary": summary,
                "purpose": purpose,
                "key_responsibilities": key_responsibilities,
                "education_requirements": education_requirements,
                "experience_requirements": experience_requirements,
                "min_years_experience": min_years_experience,
                "max_years_experience": max_years_experience,
                "technical_skills": technical_skills,
                "certifications_required": certifications_required,
                "certifications_preferred": certifications_preferred,
                "work_location": work_location,
                "travel_requirements": travel_requirements,
                "reports_to": reports_to,
                "direct_reports": direct_reports,
                "status": status,
            },
            "error": str(e),
        })
        return templates.TemplateResponse(request, "people/hr/job_description_form.html", context)


@router.get("/job-descriptions/{jd_id}", response_class=HTMLResponse)
def view_job_description(
    request: Request,
    jd_id: str,
    success: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View job description detail."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id)
    comp_svc = CompetencyService(db, org_id)

    jd = jd_svc.get_job_description(coerce_uuid(jd_id), load_competencies=True)
    if not jd:
        return RedirectResponse(url="/people/hr/job-descriptions?error=Job+description+not+found", status_code=303)

    # Get available competencies for adding
    all_competencies = comp_svc.list_competencies(is_active=True).items

    context = base_context(request, auth, jd.job_title, "job-descriptions", db=db)
    context.update({
        "jd": jd,
        "all_competencies": all_competencies,
        "success": success,
    })
    return templates.TemplateResponse(request, "people/hr/job_description_detail.html", context)


@router.get("/job-descriptions/{jd_id}/edit", response_class=HTMLResponse)
def edit_job_description_form(
    request: Request,
    jd_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit job description form."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id)
    org_svc = OrganizationService(db, org_id)

    jd = jd_svc.get_job_description(coerce_uuid(jd_id))
    if not jd:
        return RedirectResponse(url="/people/hr/job-descriptions?error=Job+description+not+found", status_code=303)

    designations = org_svc.list_designations(
        DesignationFilters(is_active=True),
        PaginationParams(limit=200),
    ).items
    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, f"Edit {jd.job_title}", "job-descriptions", db=db)
    context.update({
        "jd": jd,
        "designations": designations,
        "departments": departments,
        "statuses": list(JobDescriptionStatus),
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/job_description_form.html", context)


@router.post("/job-descriptions/{jd_id}/edit", response_class=HTMLResponse)
def update_job_description(
    request: Request,
    jd_id: str,
    jd_code: str = Form(...),
    job_title: str = Form(...),
    designation_id: str = Form(...),
    department_id: Optional[str] = Form(None),
    summary: Optional[str] = Form(None),
    purpose: Optional[str] = Form(None),
    key_responsibilities: Optional[str] = Form(None),
    education_requirements: Optional[str] = Form(None),
    experience_requirements: Optional[str] = Form(None),
    min_years_experience: Optional[int] = Form(None),
    max_years_experience: Optional[int] = Form(None),
    technical_skills: Optional[str] = Form(None),
    certifications_required: Optional[str] = Form(None),
    certifications_preferred: Optional[str] = Form(None),
    work_location: Optional[str] = Form(None),
    travel_requirements: Optional[str] = Form(None),
    reports_to: Optional[str] = Form(None),
    direct_reports: Optional[str] = Form(None),
    status: str = Form("draft"),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a job description."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id, auth.principal)
    org_svc = OrganizationService(db, org_id)

    try:
        jd_svc.update_job_description(
            coerce_uuid(jd_id),
            {
                "jd_code": jd_code,
                "job_title": job_title,
                "designation_id": coerce_uuid(designation_id),
                "department_id": coerce_uuid(department_id) if department_id else None,
                "summary": summary or None,
                "purpose": purpose or None,
                "key_responsibilities": key_responsibilities or None,
                "education_requirements": education_requirements or None,
                "experience_requirements": experience_requirements or None,
                "min_years_experience": min_years_experience,
                "max_years_experience": max_years_experience,
                "technical_skills": technical_skills or None,
                "certifications_required": certifications_required or None,
                "certifications_preferred": certifications_preferred or None,
                "work_location": work_location or None,
                "travel_requirements": travel_requirements or None,
                "reports_to": reports_to or None,
                "direct_reports": direct_reports or None,
                "status": JobDescriptionStatus(status),
            },
        )
        db.commit()
        return RedirectResponse(url=f"/people/hr/job-descriptions/{jd_id}?success=Job+description+updated", status_code=303)
    except Exception as e:
        db.rollback()
        jd = jd_svc.get_job_description(coerce_uuid(jd_id))
        designations = org_svc.list_designations(
            DesignationFilters(is_active=True),
            PaginationParams(limit=200),
        ).items
        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=200),
        ).items

        context = base_context(request, auth, f"Edit Job Description", "job-descriptions", db=db)
        context.update({
            "jd": jd,
            "designations": designations,
            "departments": departments,
            "statuses": list(JobDescriptionStatus),
            "form_data": {
                "jd_code": jd_code,
                "job_title": job_title,
            },
            "error": str(e),
        })
        return templates.TemplateResponse(request, "people/hr/job_description_form.html", context)


@router.post("/job-descriptions/{jd_id}/activate", response_class=HTMLResponse)
def activate_job_description(
    request: Request,
    jd_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Activate a job description."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id, auth.principal)

    try:
        jd_svc.activate_job_description(coerce_uuid(jd_id))
        db.commit()
        return RedirectResponse(url=f"/people/hr/job-descriptions/{jd_id}?success=Job+description+activated", status_code=303)
    except Exception as e:
        db.rollback()
        return RedirectResponse(url=f"/people/hr/job-descriptions/{jd_id}?error={str(e)}", status_code=303)


@router.post("/job-descriptions/{jd_id}/archive", response_class=HTMLResponse)
def archive_job_description(
    request: Request,
    jd_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Archive a job description."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id, auth.principal)

    try:
        jd_svc.archive_job_description(coerce_uuid(jd_id))
        db.commit()
        return RedirectResponse(url=f"/people/hr/job-descriptions/{jd_id}?success=Job+description+archived", status_code=303)
    except Exception as e:
        db.rollback()
        return RedirectResponse(url=f"/people/hr/job-descriptions/{jd_id}?error={str(e)}", status_code=303)


@router.post("/job-descriptions/{jd_id}/competencies", response_class=HTMLResponse)
def add_competency_to_jd(
    request: Request,
    jd_id: str,
    competency_id: str = Form(...),
    required_level: int = Form(3),
    is_mandatory: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Add a competency to a job description."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id, auth.principal)

    try:
        jd_svc.add_competency(
            coerce_uuid(jd_id),
            coerce_uuid(competency_id),
            required_level=required_level,
            is_mandatory=_parse_bool(is_mandatory, True),
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(url=f"/people/hr/job-descriptions/{jd_id}?success=Competency+added", status_code=303)
    except Exception as e:
        db.rollback()
        return RedirectResponse(url=f"/people/hr/job-descriptions/{jd_id}?error={str(e)}", status_code=303)


@router.post("/job-descriptions/{jd_id}/competencies/{competency_id}/delete", response_class=HTMLResponse)
def remove_competency_from_jd(
    request: Request,
    jd_id: str,
    competency_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Remove a competency from a job description."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id, auth.principal)

    try:
        jd_svc.remove_competency(coerce_uuid(jd_id), coerce_uuid(competency_id))
        db.commit()
        return RedirectResponse(url=f"/people/hr/job-descriptions/{jd_id}?success=Competency+removed", status_code=303)
    except Exception as e:
        db.rollback()
        return RedirectResponse(url=f"/people/hr/job-descriptions/{jd_id}?error={str(e)}", status_code=303)
