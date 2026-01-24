"""Employee lifecycle routes - onboarding, offboarding, bulk operations."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.people.hr import Employee
from app.models.people.hr.checklist_template import ChecklistTemplate, ChecklistTemplateType
from app.services.people.hr import EmployeeService, BulkUpdateData
from app.services.people.hr.lifecycle import LifecycleService
from app.services.people.hr.web import hr_web_service
from app.services.common import coerce_uuid, ValidationError
from app.templates import templates
from app.web.deps import base_context, get_db, require_hr_access, WebAuthContext


router = APIRouter(tags=["lifecycle"])


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    """Parse a string value to boolean."""
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "on", "yes"}


@router.get("/employees/{employee_id}/onboarding/new", response_class=HTMLResponse)
def new_onboarding_form(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Render form to create onboarding checklist for employee."""
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
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    template_id = (form.get("template_id") or "").strip()
    notes = (form.get("notes") or "").strip()

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
# Promotions
# =============================================================================


@router.get("/promotions", response_class=HTMLResponse)
def list_promotions(
    request: Request,
    employee_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List all promotions."""
    from app.services.common import PaginationParams

    org_id = coerce_uuid(auth.organization_id)
    lifecycle_svc = LifecycleService(db)
    pagination = PaginationParams.from_page(page, per_page=20)

    emp_uuid = coerce_uuid(employee_id) if employee_id else None
    result = lifecycle_svc.list_promotions(org_id, employee_id=emp_uuid, pagination=pagination)

    context = base_context(request, auth, "Promotions", "employees", db=db)
    context.update({
        "promotions": result.items,
        "employee_id": employee_id,
        "page": result.page,
        "total_pages": result.total_pages,
        "has_prev": result.has_prev,
        "has_next": result.has_next,
    })
    return templates.TemplateResponse(request, "people/hr/promotions.html", context)


@router.get("/employees/{employee_id}/promotions/new", response_class=HTMLResponse)
def new_promotion_form(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Render form to record a promotion for an employee."""
    from app.services.people.hr.organization import OrganizationService

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)
    org_svc = OrganizationService(db, org_id)
    employee = svc.get_employee(employee_id)

    designations = org_svc.list_designations(org_id).items
    departments = org_svc.list_departments(org_id).items

    context = base_context(request, auth, "Record Promotion", "employees", db=db)
    context.update({
        "employee": employee,
        "designations": designations,
        "departments": departments,
        "form_data": {},
        "errors": {},
    })
    return templates.TemplateResponse(request, "people/hr/promotion_form.html", context)


@router.post("/employees/{employee_id}/promotions/new")
async def create_promotion(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a promotion record for an employee."""
    from datetime import date as date_type

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    promotion_date_str = (form.get("promotion_date") or "").strip()
    new_designation_id = (form.get("new_designation_id") or "").strip()
    new_department_id = (form.get("new_department_id") or "").strip()
    new_reports_to_id = (form.get("new_reports_to_id") or "").strip()
    notes = (form.get("notes") or "").strip()

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)
    lifecycle_svc = LifecycleService(db)
    employee = svc.get_employee(employee_id)

    try:
        promotion_date = date_type.fromisoformat(promotion_date_str) if promotion_date_str else date_type.today()
    except ValueError:
        promotion_date = date_type.today()

    # Build promotion details
    details = []

    if new_designation_id:
        current_designation = employee.designation.designation_name if employee.designation else "-"
        from app.models.people.hr import Designation
        new_desig = db.get(Designation, coerce_uuid(new_designation_id))
        if new_desig:
            details.append({
                "property_name": "Designation",
                "current_value": current_designation,
                "new_value": new_desig.designation_name,
            })
            # Update employee record
            employee.designation_id = coerce_uuid(new_designation_id)

    if new_department_id:
        current_department = employee.department.department_name if employee.department else "-"
        from app.models.people.hr import Department
        new_dept = db.get(Department, coerce_uuid(new_department_id))
        if new_dept:
            details.append({
                "property_name": "Department",
                "current_value": current_department,
                "new_value": new_dept.department_name,
            })
            # Update employee record
            employee.department_id = coerce_uuid(new_department_id)

    if new_reports_to_id:
        current_manager = employee.reports_to.full_name if employee.reports_to else "-"
        new_manager = db.get(Employee, coerce_uuid(new_reports_to_id))
        if new_manager:
            details.append({
                "property_name": "Reports To",
                "current_value": current_manager,
                "new_value": new_manager.full_name,
            })
            # Update employee record
            employee.reports_to_id = coerce_uuid(new_reports_to_id)

    lifecycle_svc.create_promotion(
        org_id,
        employee_id=employee_id,
        promotion_date=promotion_date,
        notes=notes or None,
        details=details,
    )
    db.commit()

    return RedirectResponse(url=f"/people/hr/employees/{employee_id}?success=Promotion+recorded", status_code=303)


@router.get("/promotions/{promotion_id}", response_class=HTMLResponse)
def promotion_detail(
    request: Request,
    promotion_id: UUID,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View promotion details."""
    from sqlalchemy.orm import joinedload
    from app.models.people.hr.lifecycle import EmployeePromotion

    org_id = coerce_uuid(auth.organization_id)
    lifecycle_svc = LifecycleService(db)

    try:
        promotion = lifecycle_svc.get_promotion(org_id, promotion_id)
    except Exception:
        return RedirectResponse(url="/people/hr/promotions", status_code=303)

    # Get employee info
    employee = db.get(Employee, promotion.employee_id)

    context = base_context(request, auth, "Promotion Details", "employees", db=db)
    context.update({
        "promotion": promotion,
        "employee": employee,
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/promotion_detail.html", context)


# =============================================================================
# Transfers
# =============================================================================


@router.get("/transfers", response_class=HTMLResponse)
def list_transfers(
    request: Request,
    employee_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List all transfers."""
    from app.services.common import PaginationParams

    org_id = coerce_uuid(auth.organization_id)
    lifecycle_svc = LifecycleService(db)
    pagination = PaginationParams.from_page(page, per_page=20)

    emp_uuid = coerce_uuid(employee_id) if employee_id else None
    result = lifecycle_svc.list_transfers(org_id, employee_id=emp_uuid, pagination=pagination)

    context = base_context(request, auth, "Transfers", "employees", db=db)
    context.update({
        "transfers": result.items,
        "employee_id": employee_id,
        "page": result.page,
        "total_pages": result.total_pages,
        "has_prev": result.has_prev,
        "has_next": result.has_next,
    })
    return templates.TemplateResponse(request, "people/hr/transfers.html", context)


@router.get("/employees/{employee_id}/transfers/new", response_class=HTMLResponse)
def new_transfer_form(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Render form to record a transfer for an employee."""
    from app.services.people.hr.organization import OrganizationService

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)
    org_svc = OrganizationService(db, org_id)
    employee = svc.get_employee(employee_id)

    designations = org_svc.list_designations(org_id).items
    departments = org_svc.list_departments(org_id).items

    context = base_context(request, auth, "Record Transfer", "employees", db=db)
    context.update({
        "employee": employee,
        "designations": designations,
        "departments": departments,
        "form_data": {},
        "errors": {},
    })
    return templates.TemplateResponse(request, "people/hr/transfer_form.html", context)


@router.post("/employees/{employee_id}/transfers/new")
async def create_transfer(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a transfer record for an employee."""
    from datetime import date as date_type

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    transfer_date_str = (form.get("transfer_date") or "").strip()
    new_department_id = (form.get("new_department_id") or "").strip()
    new_designation_id = (form.get("new_designation_id") or "").strip()
    new_reports_to_id = (form.get("new_reports_to_id") or "").strip()
    new_branch = (form.get("new_branch") or "").strip()
    notes = (form.get("notes") or "").strip()

    org_id = coerce_uuid(auth.organization_id)
    svc = EmployeeService(db, org_id)
    lifecycle_svc = LifecycleService(db)
    employee = svc.get_employee(employee_id)

    try:
        transfer_date = date_type.fromisoformat(transfer_date_str) if transfer_date_str else date_type.today()
    except ValueError:
        transfer_date = date_type.today()

    # Build transfer details
    details = []

    if new_department_id:
        current_department = employee.department.department_name if employee.department else "-"
        from app.models.people.hr import Department
        new_dept = db.get(Department, coerce_uuid(new_department_id))
        if new_dept:
            details.append({
                "property_name": "Department",
                "current_value": current_department,
                "new_value": new_dept.department_name,
            })
            # Update employee record
            employee.department_id = coerce_uuid(new_department_id)

    if new_designation_id:
        current_designation = employee.designation.designation_name if employee.designation else "-"
        from app.models.people.hr import Designation
        new_desig = db.get(Designation, coerce_uuid(new_designation_id))
        if new_desig:
            details.append({
                "property_name": "Designation",
                "current_value": current_designation,
                "new_value": new_desig.designation_name,
            })
            # Update employee record
            employee.designation_id = coerce_uuid(new_designation_id)

    if new_reports_to_id:
        current_manager = employee.reports_to.full_name if employee.reports_to else "-"
        new_manager = db.get(Employee, coerce_uuid(new_reports_to_id))
        if new_manager:
            details.append({
                "property_name": "Reports To",
                "current_value": current_manager,
                "new_value": new_manager.full_name,
            })
            # Update employee record
            employee.reports_to_id = coerce_uuid(new_reports_to_id)

    if new_branch:
        current_branch = employee.branch or "-"
        details.append({
            "property_name": "Branch",
            "current_value": current_branch,
            "new_value": new_branch,
        })
        # Update employee record
        employee.branch = new_branch

    lifecycle_svc.create_transfer(
        org_id,
        employee_id=employee_id,
        transfer_date=transfer_date,
        notes=notes or None,
        details=details,
    )
    db.commit()

    return RedirectResponse(url=f"/people/hr/employees/{employee_id}?success=Transfer+recorded", status_code=303)


@router.get("/transfers/{transfer_id}", response_class=HTMLResponse)
def transfer_detail(
    request: Request,
    transfer_id: UUID,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View transfer details."""
    org_id = coerce_uuid(auth.organization_id)
    lifecycle_svc = LifecycleService(db)

    try:
        transfer = lifecycle_svc.get_transfer(org_id, transfer_id)
    except Exception:
        return RedirectResponse(url="/people/hr/transfers", status_code=303)

    # Get employee info
    employee = db.get(Employee, transfer.employee_id)

    context = base_context(request, auth, "Transfer Details", "employees", db=db)
    context.update({
        "transfer": transfer,
        "employee": employee,
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/transfer_detail.html", context)
