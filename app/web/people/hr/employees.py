"""Employee CRUD and management routes."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.people.hr.web import hr_web_service
from app.web.deps import get_db, require_hr_access, WebAuthContext


router = APIRouter(tags=["employees"])


@router.get("/employees", response_class=HTMLResponse)
def list_employees(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    department_id: Optional[str] = None,
    designation_id: Optional[str] = None,
    date_of_joining_from: Optional[str] = None,
    date_of_joining_to: Optional[str] = None,
    date_of_leaving_from: Optional[str] = None,
    date_of_leaving_to: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Employee list page."""
    return hr_web_service.list_employees_response(
        request,
        auth,
        db,
        search,
        status,
        department_id,
        designation_id,
        date_of_joining_from,
        date_of_joining_to,
        date_of_leaving_from,
        date_of_leaving_to,
        page,
        success,
        error,
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
    return hr_web_service.employee_stats_response(auth, db)


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
    return await hr_web_service.create_employee_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/employees/{employee_id}", response_class=HTMLResponse)
def view_employee(
    request: Request,
    employee_id: UUID,
    saved: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Employee detail page."""
    return hr_web_service.employee_detail_response(
        request, auth, db, str(employee_id), saved=bool(saved)
    )


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
    return await hr_web_service.update_employee_response(
        request=request,
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/activate")
def activate_employee(
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Activate an employee."""
    return hr_web_service.activate_employee_response(
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/suspend")
async def suspend_employee(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Suspend an employee."""
    return await hr_web_service.suspend_employee_response(
        request=request,
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/on-leave")
def set_employee_on_leave(
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Set an employee on leave."""
    return hr_web_service.set_employee_on_leave_response(
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/resign")
async def resign_employee(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Record employee resignation."""
    return await hr_web_service.resign_employee_response(
        request=request,
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/terminate")
async def terminate_employee(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Terminate an employee."""
    return await hr_web_service.terminate_employee_response(
        request=request,
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/credentials/{credential_id}/toggle")
async def toggle_employee_credential(
    request: Request,
    employee_id: UUID,
    credential_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Enable/disable a user's login credential from the employee record."""
    return await hr_web_service.toggle_user_credential_response(
        request=request,
        employee_id=employee_id,
        credential_id=credential_id,
        auth=auth,
        db=db,
    )
