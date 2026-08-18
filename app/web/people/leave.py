"""
Leave management web routes.

Provides list pages and CRUD forms for leave types, allocations, applications, and holiday lists.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.people.leave.web import leave_web_service
from app.web.deps import get_db_for_org, WebAuthContext, require_hr_access

router = APIRouter(prefix="/leave", tags=["people-leave-web"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def leave_overview(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Leave overview page."""
    return leave_web_service.leave_overview_response(request, auth, db)


@router.get("/types", response_class=HTMLResponse)
def leave_types(
    request: Request,
    search: str | None = None,
    is_active: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Leave types list page."""
    return leave_web_service.leave_types_response(
        request=request,
        auth=auth,
        db=db,
        search=search,
        is_active=is_active,
        page=page,
    )


@router.get("/applications", response_class=HTMLResponse)
def leave_applications(
    request: Request,
    employee_search: str | None = None,
    employee_id: str | None = None,
    leave_type_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = Query(default=1, ge=1),
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Leave applications list page."""
    return leave_web_service.leave_applications_response(
        request=request,
        auth=auth,
        db=db,
        employee_search=employee_search,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        page=page,
        success=success,
        error=error,
    )


@router.get("/allocations", response_class=HTMLResponse)
def leave_allocations(
    request: Request,
    employee_search: str | None = None,
    employee_id: str | None = None,
    leave_type_id: str | None = None,
    year: str | None = None,
    is_active: str | None = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1),
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Leave allocations list page."""
    return leave_web_service.leave_allocations_response(
        request=request,
        auth=auth,
        db=db,
        employee_search=employee_search,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        year=year,
        is_active=is_active,
        page=page,
        per_page=per_page,
        success=success,
        error=error,
    )


@router.get("/holidays", response_class=HTMLResponse)
def leave_holidays(
    request: Request,
    year: int | None = None,
    is_active: bool | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Holiday lists page."""
    return leave_web_service.leave_holidays_response(
        request=request,
        auth=auth,
        db=db,
        year=year,
        is_active=is_active,
        page=page,
    )


# =============================================================================
# Leave Types CRUD
# =============================================================================


@router.get("/types/new", response_class=HTMLResponse)
def new_leave_type_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New leave type form."""
    return leave_web_service.new_leave_type_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/types/new")
async def create_leave_type(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle leave type creation."""
    return await leave_web_service.create_leave_type_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/types/{leave_type_id}/edit", response_class=HTMLResponse)
def edit_leave_type_form(
    request: Request,
    leave_type_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit leave type form."""
    return leave_web_service.edit_leave_type_form_response(
        request=request,
        leave_type_id=leave_type_id,
        auth=auth,
        db=db,
    )


@router.post("/types/{leave_type_id}/edit")
async def update_leave_type(
    request: Request,
    leave_type_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle leave type update."""
    return await leave_web_service.update_leave_type_response(
        request=request,
        leave_type_id=leave_type_id,
        auth=auth,
        db=db,
    )


# =============================================================================
# Leave Allocations CRUD
# =============================================================================


@router.get("/allocations/new", response_class=HTMLResponse)
def new_allocation_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New leave allocation form."""
    return leave_web_service.new_allocation_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/allocations/new")
async def create_allocation(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle allocation creation."""
    return await leave_web_service.create_allocation_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/allocations/{allocation_id}", response_class=HTMLResponse)
def view_allocation(
    request: Request,
    allocation_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """View allocation details."""
    return leave_web_service.view_allocation_response(
        request=request,
        allocation_id=allocation_id,
        auth=auth,
        db=db,
        success=success,
        error=error,
    )


@router.get("/allocations/{allocation_id}/edit", response_class=HTMLResponse)
def edit_allocation_form(
    request: Request,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit allocation form."""
    return leave_web_service.edit_allocation_form_response(
        request=request,
        allocation_id=allocation_id,
        auth=auth,
        db=db,
    )


@router.post("/allocations/{allocation_id}/edit")
async def update_allocation(
    request: Request,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle allocation update."""
    return await leave_web_service.update_allocation_response(
        request=request,
        allocation_id=allocation_id,
        auth=auth,
        db=db,
    )


@router.post("/allocations/{allocation_id}/delete")
async def delete_allocation(
    request: Request,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete an allocation."""
    return leave_web_service.delete_allocation_response(
        allocation_id=allocation_id,
        auth=auth,
        db=db,
    )


@router.post("/allocations/{allocation_id}/encash")
async def encash_allocation(
    request: Request,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Process leave encashment for an allocation."""
    return await leave_web_service.encash_allocation_response(
        request=request,
        allocation_id=allocation_id,
        auth=auth,
        db=db,
    )


@router.post("/allocations/bulk-create")
async def bulk_create_allocations(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk create leave allocations for multiple employees."""
    return await leave_web_service.bulk_create_allocations_response(
        request=request,
        auth=auth,
        db=db,
    )


# =============================================================================
# Leave Applications CRUD
# =============================================================================


@router.get("/applications/new", response_class=HTMLResponse)
def new_application_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New leave application form."""
    return leave_web_service.new_application_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/applications/new")
async def create_application(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle application creation."""
    return await leave_web_service.create_application_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/applications/{application_id}", response_class=HTMLResponse)
def view_application(
    request: Request,
    application_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """View application details."""
    return leave_web_service.view_application_response(
        request=request,
        application_id=application_id,
        success=success,
        error=error,
        auth=auth,
        db=db,
    )


@router.get("/applications/{application_id}/edit", response_class=HTMLResponse)
def edit_application_form(
    request: Request,
    application_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit application form."""
    return leave_web_service.edit_application_form_response(
        request=request,
        application_id=application_id,
        auth=auth,
        db=db,
    )


@router.post("/applications/{application_id}/edit")
async def update_application(
    request: Request,
    application_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle application update."""
    return await leave_web_service.update_application_response(
        request=request,
        application_id=application_id,
        auth=auth,
        db=db,
    )


@router.post("/applications/{application_id}/approve")
async def approve_application(
    request: Request,
    application_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Approve a leave application."""
    return leave_web_service.approve_application_response(
        application_id=application_id,
        auth=auth,
        db=db,
    )


@router.post("/applications/{application_id}/reject")
async def reject_application(
    request: Request,
    application_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Reject a leave application."""
    return await leave_web_service.reject_application_response(
        request=request,
        application_id=application_id,
        auth=auth,
        db=db,
    )


@router.post("/applications/{application_id}/cancel")
async def cancel_application(
    request: Request,
    application_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Cancel a leave application."""
    return leave_web_service.cancel_application_response(
        application_id=application_id,
        auth=auth,
        db=db,
    )


# =============================================================================
# Holiday Lists CRUD
# =============================================================================


@router.get("/holidays/new", response_class=HTMLResponse)
def new_holiday_list_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New holiday list form."""
    return leave_web_service.new_holiday_list_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/holidays/new")
async def create_holiday_list(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle holiday list creation."""
    return await leave_web_service.create_holiday_list_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/holidays/{holiday_list_id}", response_class=HTMLResponse)
def view_holiday_list(
    request: Request,
    holiday_list_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """View holiday list details."""
    return leave_web_service.view_holiday_list_response(
        request=request,
        holiday_list_id=holiday_list_id,
        auth=auth,
        db=db,
    )


@router.get("/holidays/{holiday_list_id}/edit", response_class=HTMLResponse)
def edit_holiday_list_form(
    request: Request,
    holiday_list_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit holiday list form."""
    return leave_web_service.edit_holiday_list_form_response(
        request=request,
        holiday_list_id=holiday_list_id,
        auth=auth,
        db=db,
    )


@router.post("/holidays/{holiday_list_id}/edit")
async def update_holiday_list(
    request: Request,
    holiday_list_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle holiday list update."""
    return await leave_web_service.update_holiday_list_response(
        request=request,
        holiday_list_id=holiday_list_id,
        auth=auth,
        db=db,
    )


@router.post("/holidays/{holiday_list_id}/delete")
async def delete_holiday_list(
    request: Request,
    holiday_list_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a holiday list."""
    return leave_web_service.delete_holiday_list_response(
        holiday_list_id=holiday_list_id,
        auth=auth,
        db=db,
    )


# =============================================================================
# Reports
# =============================================================================


@router.get("/reports/balance", response_class=HTMLResponse)
def leave_balance_report(
    request: Request,
    year: int | None = None,
    department_id: str | None = None,
    employee_search: str | None = None,
    page: int = Query(1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Leave balance report page."""
    return leave_web_service.leave_balance_report_response(
        request=request,
        auth=auth,
        db=db,
        year=year,
        department_id=department_id,
        employee_search=employee_search,
        page=page,
    )


@router.get("/reports/usage", response_class=HTMLResponse)
def leave_usage_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Leave usage report page."""
    return leave_web_service.leave_usage_report_response(
        request=request,
        auth=auth,
        db=db,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/reports/calendar", response_class=HTMLResponse)
def leave_calendar_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    department_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Leave calendar report page."""
    return leave_web_service.leave_calendar_report_response(
        request=request,
        auth=auth,
        db=db,
        start_date=start_date,
        end_date=end_date,
        department_id=department_id,
    )


@router.get("/reports/trends", response_class=HTMLResponse)
def leave_trends_report(
    request: Request,
    months: int = Query(default=12, ge=3, le=24),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Leave trends report page."""
    return leave_web_service.leave_trends_report_response(
        request=request,
        auth=auth,
        db=db,
        months=months,
    )


# =============================================================================
# Bulk Operations
# =============================================================================


@router.post("/applications/bulk-approve")
async def bulk_approve_applications(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk approve leave applications."""
    return await leave_web_service.bulk_approve_applications_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/applications/bulk-reject")
async def bulk_reject_applications(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk reject leave applications."""
    return await leave_web_service.bulk_reject_applications_response(
        request=request,
        auth=auth,
        db=db,
    )
