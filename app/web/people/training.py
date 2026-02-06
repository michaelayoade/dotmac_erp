"""
Training web routes.

Lists training programs and events with full CRUD operations.
All business logic is delegated to the training_web_service.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.people.training.web import training_web_service
from app.web.deps import WebAuthContext, get_db, require_hr_access


router = APIRouter(prefix="/training", tags=["people-training-web"])


# ─────────────────────────────────────────────────────────────────────────────
# Training Programs
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/programs", response_class=HTMLResponse)
def list_programs(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Training programs list page."""
    return training_web_service.list_programs_response(
        request, auth, db, search, status, category, page
    )


@router.get("/programs/new", response_class=HTMLResponse)
def new_program_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New training program form."""
    return training_web_service.program_new_form_response(request, auth, db)


@router.post("/programs/new", response_class=HTMLResponse)
async def create_program(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new training program."""
    return await training_web_service.create_program_response(request, auth, db)


@router.get("/programs/{program_id}", response_class=HTMLResponse)
def view_program(
    request: Request,
    program_id: str,
    success: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View training program detail."""
    return training_web_service.program_detail_response(
        request, auth, db, program_id, success
    )


@router.get("/programs/{program_id}/edit", response_class=HTMLResponse)
def edit_program_form(
    request: Request,
    program_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit training program form."""
    return training_web_service.program_edit_form_response(
        request, auth, db, program_id
    )


@router.post("/programs/{program_id}/edit", response_class=HTMLResponse)
async def update_program(
    request: Request,
    program_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a training program."""
    return await training_web_service.update_program_response(
        request, auth, db, program_id
    )


@router.post("/programs/{program_id}/activate", response_class=HTMLResponse)
def activate_program(
    program_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Activate a training program."""
    return training_web_service.activate_program_response(auth, db, program_id)


@router.post("/programs/{program_id}/retire", response_class=HTMLResponse)
def retire_program(
    program_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Retire a training program."""
    return training_web_service.retire_program_response(auth, db, program_id)


# ─────────────────────────────────────────────────────────────────────────────
# Training Events
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/events", response_class=HTMLResponse)
def list_events(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    program_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Training events list page."""
    return training_web_service.list_events_response(
        request, auth, db, search, status, program_id, start_date, end_date, page
    )


@router.get("/events/new", response_class=HTMLResponse)
def new_event_form(
    request: Request,
    program_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New training event form."""
    return training_web_service.event_new_form_response(request, auth, db, program_id)


@router.post("/events/new", response_class=HTMLResponse)
async def create_event(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new training event."""
    return await training_web_service.create_event_response(request, auth, db)


@router.get("/events/{event_id}", response_class=HTMLResponse)
def view_event(
    request: Request,
    event_id: str,
    success: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View training event detail."""
    return training_web_service.event_detail_response(
        request, auth, db, event_id, success
    )


@router.get("/events/{event_id}/edit", response_class=HTMLResponse)
def edit_event_form(
    request: Request,
    event_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit training event form."""
    return training_web_service.event_edit_form_response(request, auth, db, event_id)


@router.post("/events/{event_id}/edit", response_class=HTMLResponse)
async def update_event(
    request: Request,
    event_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a training event."""
    return await training_web_service.update_event_response(request, auth, db, event_id)


@router.post("/events/{event_id}/schedule", response_class=HTMLResponse)
def schedule_event(
    event_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Schedule a draft event."""
    return training_web_service.schedule_event_response(auth, db, event_id)


@router.post("/events/{event_id}/start", response_class=HTMLResponse)
def start_event(
    event_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Start a training event."""
    return training_web_service.start_event_response(auth, db, event_id)


@router.post("/events/{event_id}/complete", response_class=HTMLResponse)
def complete_event(
    event_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Complete a training event."""
    return training_web_service.complete_event_response(auth, db, event_id)


@router.post("/events/{event_id}/cancel", response_class=HTMLResponse)
def cancel_event(
    event_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Cancel a training event."""
    return training_web_service.cancel_event_response(auth, db, event_id)


# ─────────────────────────────────────────────────────────────────────────────
# Training Reports
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/reports/completion", response_class=HTMLResponse)
def report_completion(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Training completion rates report."""
    return training_web_service.completion_report_response(
        request, auth, db, start_date, end_date
    )


@router.get("/reports/by-department", response_class=HTMLResponse)
def report_by_department(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Training participation by department report."""
    return training_web_service.by_department_report_response(
        request, auth, db, start_date, end_date
    )


@router.get("/reports/cost-analysis", response_class=HTMLResponse)
def report_cost_analysis(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Training cost analysis report."""
    return training_web_service.cost_analysis_report_response(
        request, auth, db, start_date, end_date
    )


@router.get("/reports/effectiveness", response_class=HTMLResponse)
def report_effectiveness(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Training effectiveness/feedback report."""
    return training_web_service.effectiveness_report_response(
        request, auth, db, start_date, end_date
    )
