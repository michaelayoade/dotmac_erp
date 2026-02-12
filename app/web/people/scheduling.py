"""
Shift Scheduling web routes.

Shift patterns, assignments, schedules, and swap requests pages.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.people.scheduling.web import scheduling_web_service
from app.web.deps import WebAuthContext, get_db, require_hr_access

router = APIRouter(prefix="/scheduling", tags=["people-scheduling-web"])


# =============================================================================
# Shift Patterns
# =============================================================================


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@router.get("/patterns", response_class=HTMLResponse)
def patterns_list(
    request: Request,
    search: str | None = None,
    is_active: str | None = None,
    success: str | None = None,
    error: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Shift patterns list page."""
    return scheduling_web_service.patterns_list_response(
        request=request,
        auth=auth,
        db=db,
        search=search,
        is_active=is_active,
        success=success,
        error=error,
        page=page,
    )


@router.get("/patterns/new", response_class=HTMLResponse)
def new_pattern_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New shift pattern form."""
    return scheduling_web_service.new_pattern_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/patterns/new", response_class=HTMLResponse)
async def create_pattern(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new shift pattern."""
    return await scheduling_web_service.create_pattern_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/patterns/{pattern_id}/edit", response_class=HTMLResponse)
def edit_pattern_form(
    request: Request,
    pattern_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit shift pattern form."""
    return scheduling_web_service.edit_pattern_form_response(
        request=request,
        auth=auth,
        db=db,
        pattern_id=pattern_id,
    )


@router.post("/patterns/{pattern_id}/edit", response_class=HTMLResponse)
async def update_pattern(
    request: Request,
    pattern_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a shift pattern."""
    return await scheduling_web_service.update_pattern_response(
        request=request,
        auth=auth,
        db=db,
        pattern_id=pattern_id,
    )


@router.post("/patterns/{pattern_id}/delete")
async def delete_pattern(
    request: Request,
    pattern_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Deactivate a shift pattern."""
    return await scheduling_web_service.delete_pattern_response(
        request=request,
        auth=auth,
        db=db,
        pattern_id=pattern_id,
    )


# =============================================================================
# Pattern Assignments
# =============================================================================


@router.get("/assignments", response_class=HTMLResponse)
def assignments_list(
    request: Request,
    department_id: str | None = None,
    success: str | None = None,
    error: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Pattern assignments list page."""
    return scheduling_web_service.assignments_list_response(
        request=request,
        auth=auth,
        db=db,
        department_id=department_id,
        success=success,
        error=error,
        page=page,
    )


@router.get("/assignments/new", response_class=HTMLResponse)
def new_assignment_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New pattern assignment form."""
    return scheduling_web_service.new_assignment_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/assignments/new", response_class=HTMLResponse)
async def create_assignment(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new pattern assignment."""
    return await scheduling_web_service.create_assignment_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/assignments/{assignment_id}/delete")
async def delete_assignment(
    request: Request,
    assignment_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Delete (end) a pattern assignment."""
    return await scheduling_web_service.delete_assignment_response(
        request=request,
        auth=auth,
        db=db,
        assignment_id=assignment_id,
    )


# =============================================================================
# Schedules
# =============================================================================


@router.get("/schedules", response_class=HTMLResponse)
def schedules_list(
    request: Request,
    department_id: str | None = None,
    year_month: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Schedules list/calendar page."""
    return scheduling_web_service.schedules_list_response(
        request=request,
        auth=auth,
        db=db,
        department_id=department_id,
        year_month=year_month,
        page=page,
    )


@router.get("/schedules/generate", response_class=HTMLResponse)
def generate_schedule_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Generate schedule form."""
    return scheduling_web_service.generate_schedule_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/schedules/generate", response_class=HTMLResponse)
async def generate_schedule(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Generate schedules for a month."""
    return await scheduling_web_service.generate_schedule_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/schedules/publish")
async def publish_schedule(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Publish schedules for a month."""
    return await scheduling_web_service.publish_schedule_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/schedules/{schedule_id}/delete")
async def delete_schedule(
    request: Request,
    schedule_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Delete a draft schedule entry."""
    return await scheduling_web_service.delete_schedule_response(
        request=request,
        auth=auth,
        db=db,
        schedule_id=schedule_id,
    )


# =============================================================================
# Swap Requests
# =============================================================================


@router.get("/swaps", response_class=HTMLResponse)
def swap_requests_list(
    request: Request,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Swap requests list page."""
    return scheduling_web_service.swap_requests_list_response(
        request=request,
        auth=auth,
        db=db,
        status=status,
        page=page,
    )


@router.post("/swaps/{request_id}/approve")
async def approve_swap_request(
    request: Request,
    request_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Approve a swap request."""
    return await scheduling_web_service.approve_swap_request_response(
        request=request,
        auth=auth,
        db=db,
        request_id=request_id,
    )


@router.post("/swaps/{request_id}/reject")
async def reject_swap_request(
    request: Request,
    request_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Reject a swap request."""
    return await scheduling_web_service.reject_swap_request_response(
        request=request,
        auth=auth,
        db=db,
        request_id=request_id,
    )
