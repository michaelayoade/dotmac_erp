"""Mode-neutral People/Performance routes for weekly meeting reports."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.people.perf.web.weekly_meeting_report_web import (
    weekly_meeting_report_web_service,
)
from app.web.deps import (
    WebAuthContext,
    get_db_for_org,
    require_any_web_permission,
)

READ_PERMISSIONS = [
    "performance:weekly_reports:read",
    "performance:weekly_reports:write",
    "performance:weekly_reports:submit",
]
WRITE_PERMISSIONS = ["performance:weekly_reports:write"]
SUBMIT_PERMISSIONS = ["performance:weekly_reports:submit"]

require_report_read = require_any_web_permission(READ_PERMISSIONS)
require_report_write = require_any_web_permission(WRITE_PERMISSIONS)
require_report_submit = require_any_web_permission(SUBMIT_PERMISSIONS)
require_report_reopen = require_any_web_permission(
    ["performance:weekly_reports:reopen"]
)

router = APIRouter(
    prefix="/perf/weekly-meeting-reports", tags=["weekly-meeting-reports"]
)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def list_reports(
    request: Request,
    search: str = "",
    status: str = "",
    department_id: str = "",
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_report_read),
    db: Session = Depends(get_db_for_org),
):
    return weekly_meeting_report_web_service.list_response(
        request,
        auth,
        db,
        search=search,
        status=status,
        department_id=department_id,
        page=page,
    )


@router.get("/new", response_class=HTMLResponse)
def new_report_form(
    request: Request,
    auth: WebAuthContext = Depends(require_report_write),
    db: Session = Depends(get_db_for_org),
):
    return weekly_meeting_report_web_service.new_form_response(request, auth, db)


@router.post("/new", response_class=HTMLResponse)
async def create_report(
    request: Request,
    auth: WebAuthContext = Depends(require_report_write),
    db: Session = Depends(get_db_for_org),
):
    return await weekly_meeting_report_web_service.save_response(request, auth, db)


@router.post("/new/submit", response_class=HTMLResponse)
async def create_and_submit_report(
    request: Request,
    auth: WebAuthContext = Depends(require_report_submit),
    _write_auth: WebAuthContext = Depends(require_report_write),
    db: Session = Depends(get_db_for_org),
):
    return await weekly_meeting_report_web_service.save_response(
        request, auth, db, submit=True
    )


@router.get("/department/{department_id}/roster")
def department_roster(
    department_id: str,
    auth: WebAuthContext = Depends(require_report_write),
    db: Session = Depends(get_db_for_org),
):
    return weekly_meeting_report_web_service.roster_response(auth, db, department_id)


@router.get("/{report_id}", response_class=HTMLResponse)
def report_detail(
    request: Request,
    report_id: str,
    auth: WebAuthContext = Depends(require_report_read),
    db: Session = Depends(get_db_for_org),
):
    return weekly_meeting_report_web_service.detail_response(
        request, auth, db, report_id
    )


@router.get("/{report_id}/print", response_class=HTMLResponse)
def print_report(
    request: Request,
    report_id: str,
    auth: WebAuthContext = Depends(require_report_read),
    db: Session = Depends(get_db_for_org),
):
    return weekly_meeting_report_web_service.detail_response(
        request, auth, db, report_id, print_view=True
    )


@router.get("/{report_id}/edit", response_class=HTMLResponse)
def edit_report_form(
    request: Request,
    report_id: str,
    auth: WebAuthContext = Depends(require_report_write),
    db: Session = Depends(get_db_for_org),
):
    return weekly_meeting_report_web_service.edit_form_response(
        request, auth, db, report_id
    )


@router.post("/{report_id}/edit", response_class=HTMLResponse)
async def update_report(
    request: Request,
    report_id: str,
    auth: WebAuthContext = Depends(require_report_write),
    db: Session = Depends(get_db_for_org),
):
    return await weekly_meeting_report_web_service.save_response(
        request, auth, db, report_id=report_id
    )


@router.post("/{report_id}/submit", response_class=HTMLResponse)
async def update_and_submit_report(
    request: Request,
    report_id: str,
    auth: WebAuthContext = Depends(require_report_submit),
    _write_auth: WebAuthContext = Depends(require_report_write),
    db: Session = Depends(get_db_for_org),
):
    return await weekly_meeting_report_web_service.save_response(
        request, auth, db, report_id=report_id, submit=True
    )


@router.post("/{report_id}/refresh")
def refresh_report(
    report_id: str,
    auth: WebAuthContext = Depends(require_report_write),
    db: Session = Depends(get_db_for_org),
):
    return weekly_meeting_report_web_service.refresh_response(auth, db, report_id)


@router.post("/{report_id}/reopen")
def reopen_report(
    report_id: str,
    auth: WebAuthContext = Depends(require_report_reopen),
    db: Session = Depends(get_db_for_org),
):
    return weekly_meeting_report_web_service.reopen_response(auth, db, report_id)


@router.post("/{report_id}/retry-notification")
def retry_notification(
    report_id: str,
    auth: WebAuthContext = Depends(require_report_reopen),
    db: Session = Depends(get_db_for_org),
):
    return weekly_meeting_report_web_service.retry_notification_response(
        auth, db, report_id
    )
