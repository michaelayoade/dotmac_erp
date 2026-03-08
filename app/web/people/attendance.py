"""
Attendance management web routes.

Attendance list and shift type configuration pages.
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.people.attendance.web import attendance_web_service
from app.web.deps import WebAuthContext, get_db, require_hr_access

router = APIRouter(prefix="/attendance", tags=["people-attendance-web"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@router.get("/records", response_class=HTMLResponse)
def attendance_overview(
    request: Request,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    employee_id: str | None = None,
    page: int = Query(default=1, ge=1),
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Attendance records list page."""
    return attendance_web_service.attendance_overview_response(
        request=request,
        auth=auth,
        db=db,
        status=status,
        start_date=start_date,
        end_date=end_date,
        employee_id=employee_id,
        page=page,
        success=success,
        error=error,
    )


@router.post("/records/{attendance_id}/delete")
def delete_attendance_record(
    attendance_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Delete an attendance record."""
    try:
        return attendance_web_service.delete_attendance_record_response(
            auth=auth,
            db=db,
            attendance_id=attendance_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/records/bulk-mark")
async def bulk_mark_attendance(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Bulk mark attendance for multiple employees."""
    return await attendance_web_service.bulk_mark_attendance_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/shifts", response_class=HTMLResponse)
def attendance_shifts(
    request: Request,
    search: str | None = None,
    is_active: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Shift type list page."""
    return attendance_web_service.attendance_shifts_response(
        request=request,
        auth=auth,
        db=db,
        search=search,
        is_active=is_active,
        page=page,
    )


@router.get("/records/new", response_class=HTMLResponse)
def new_attendance_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New attendance record form."""
    return attendance_web_service.new_attendance_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/records/new", response_class=HTMLResponse)
async def create_attendance(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new attendance record."""
    return await attendance_web_service.create_attendance_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/shifts/new", response_class=HTMLResponse)
def new_shift_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New shift type form."""
    return attendance_web_service.new_shift_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/shifts/new", response_class=HTMLResponse)
async def create_shift(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new shift type."""
    return await attendance_web_service.create_shift_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/shifts/{shift_type_id}/edit", response_class=HTMLResponse)
def edit_shift_form(
    request: Request,
    shift_type_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit shift type form."""
    return attendance_web_service.edit_shift_form_response(
        request=request,
        auth=auth,
        db=db,
        shift_type_id=shift_type_id,
    )


@router.post("/shifts/{shift_type_id}/edit", response_class=HTMLResponse)
async def update_shift(
    request: Request,
    shift_type_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a shift type."""
    return await attendance_web_service.update_shift_response(
        request=request,
        auth=auth,
        db=db,
        shift_type_id=shift_type_id,
    )


# =============================================================================
# Reports
# =============================================================================


@router.get("/reports/summary", response_class=HTMLResponse)
def attendance_summary_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    department_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Attendance summary report page."""
    return attendance_web_service.attendance_summary_report_response(
        request=request,
        auth=auth,
        db=db,
        start_date=start_date,
        end_date=end_date,
        department_id=department_id,
    )


@router.get("/reports/by-employee", response_class=HTMLResponse)
def attendance_by_employee_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    department_id: str | None = None,
    page: int = Query(1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Attendance by employee report page."""
    return attendance_web_service.attendance_by_employee_report_response(
        request=request,
        auth=auth,
        db=db,
        start_date=start_date,
        end_date=end_date,
        department_id=department_id,
        page=page,
    )


@router.get("/reports/late-early", response_class=HTMLResponse)
def attendance_late_early_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    department_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Late arrivals and early departures report page."""
    return attendance_web_service.attendance_late_early_report_response(
        request=request,
        auth=auth,
        db=db,
        start_date=start_date,
        end_date=end_date,
        department_id=department_id,
    )


@router.get("/reports/trends", response_class=HTMLResponse)
def attendance_trends_report(
    request: Request,
    months: int = Query(default=12, ge=3, le=24),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Attendance trends report page."""
    return attendance_web_service.attendance_trends_report_response(
        request=request,
        auth=auth,
        db=db,
        months=months,
    )


# =============================================================================
# Attendance Requests
# =============================================================================


@router.get("/requests", response_class=HTMLResponse)
def attendance_requests_list(
    request: Request,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = Query(default=1, ge=1),
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Attendance requests list page."""
    return attendance_web_service.attendance_requests_list_response(
        request=request,
        auth=auth,
        db=db,
        status=status,
        start_date=start_date,
        end_date=end_date,
        page=page,
        success=success,
        error=error,
    )


@router.get("/requests/new", response_class=HTMLResponse)
def attendance_request_new_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New attendance request form."""
    return attendance_web_service.attendance_request_new_form_response(
        request, auth, db
    )


@router.post("/requests/new", response_class=HTMLResponse)
async def create_attendance_request(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new attendance request."""
    return await attendance_web_service.create_attendance_request_response(
        request, auth, db
    )


@router.post("/requests/{request_id}/approve")
async def approve_attendance_request(
    request_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Approve an attendance request."""
    try:
        return attendance_web_service.approve_attendance_request_response(
            auth=auth,
            db=db,
            request_id=request_id,
        )
    except Exception as e:
        error_msg = quote(str(e))
        return RedirectResponse(
            url=f"/people/attendance/requests?error={error_msg}", status_code=303
        )


@router.post("/requests/{request_id}/reject")
async def reject_attendance_request(
    request_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Reject an attendance request."""
    try:
        return attendance_web_service.reject_attendance_request_response(
            auth=auth,
            db=db,
            request_id=request_id,
        )
    except Exception as e:
        error_msg = quote(str(e))
        return RedirectResponse(
            url=f"/people/attendance/requests?error={error_msg}", status_code=303
        )


@router.post("/requests/bulk-approve")
async def bulk_approve_attendance_requests(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Bulk approve attendance requests."""
    try:
        return await attendance_web_service.bulk_approve_attendance_requests_response(
            request=request,
            auth=auth,
            db=db,
        )
    except Exception as e:
        error_msg = quote(str(e))
        return RedirectResponse(
            url=f"/people/attendance/requests?error={error_msg}", status_code=303
        )


@router.post("/requests/bulk-reject")
async def bulk_reject_attendance_requests(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Bulk reject attendance requests."""
    try:
        return await attendance_web_service.bulk_reject_attendance_requests_response(
            request=request,
            auth=auth,
            db=db,
        )
    except Exception as e:
        error_msg = quote(str(e))
        return RedirectResponse(
            url=f"/people/attendance/requests?error={error_msg}", status_code=303
        )
