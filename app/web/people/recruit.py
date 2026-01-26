"""
Recruitment web routes.

Lists job openings, applicants, interviews, and job offers with full CRUD.
All business logic is delegated to the recruit_web_service.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.people.recruit.web import recruit_web_service
from app.web.deps import WebAuthContext, get_db, require_hr_access


router = APIRouter(prefix="/recruit", tags=["people-recruit-web"])


# ─────────────────────────────────────────────────────────────────────────────
# Redirects
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", include_in_schema=False)
def recruit_root() -> RedirectResponse:
    return RedirectResponse(url="/people/recruit/jobs")


@router.get("/job-openings", include_in_schema=False)
def job_openings_redirect() -> RedirectResponse:
    return RedirectResponse(url="/people/recruit/jobs")


@router.get("/job-openings/{job_opening_id}", include_in_schema=False)
def job_opening_alias(job_opening_id: str) -> RedirectResponse:
    return RedirectResponse(url=f"/people/recruit/jobs/{job_opening_id}")


@router.get("/job-openings/{job_opening_id}/edit", include_in_schema=False)
def job_opening_edit_alias(job_opening_id: str) -> RedirectResponse:
    return RedirectResponse(url=f"/people/recruit/jobs/{job_opening_id}/edit")


# ─────────────────────────────────────────────────────────────────────────────
# Job Openings
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/jobs", response_class=HTMLResponse)
def list_job_openings(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    department_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Job openings list page."""
    return recruit_web_service.list_job_openings_response(
        request, auth, db, search, status, department_id, page
    )


@router.get("/jobs/new", response_class=HTMLResponse)
def new_job_opening_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New job opening form."""
    return recruit_web_service.job_opening_new_form_response(request, auth, db)


@router.post("/jobs/new", response_class=HTMLResponse)
async def create_job_opening(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new job opening."""
    return await recruit_web_service.create_job_opening_response(request, auth, db)


@router.get("/jobs/{job_opening_id}", response_class=HTMLResponse)
def job_opening_detail(
    request: Request,
    job_opening_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Job opening detail page."""
    return recruit_web_service.job_opening_detail_response(request, auth, db, job_opening_id)


@router.get("/jobs/{job_opening_id}/edit", response_class=HTMLResponse)
def edit_job_opening_form(
    request: Request,
    job_opening_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit job opening form."""
    return recruit_web_service.job_opening_edit_form_response(request, auth, db, job_opening_id)


@router.post("/jobs/{job_opening_id}/edit", response_class=HTMLResponse)
async def update_job_opening(
    request: Request,
    job_opening_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a job opening."""
    return await recruit_web_service.update_job_opening_response(request, auth, db, job_opening_id)


@router.post("/jobs/{job_opening_id}/publish")
def publish_job_opening(
    job_opening_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Publish a job opening."""
    return recruit_web_service.publish_job_opening_response(auth, db, job_opening_id)


@router.post("/jobs/{job_opening_id}/hold")
def hold_job_opening(
    job_opening_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Put a job opening on hold."""
    return recruit_web_service.hold_job_opening_response(auth, db, job_opening_id)


@router.post("/jobs/{job_opening_id}/reopen")
def reopen_job_opening(
    job_opening_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Reopen a job opening."""
    return recruit_web_service.reopen_job_opening_response(auth, db, job_opening_id)


@router.post("/jobs/{job_opening_id}/close")
def close_job_opening(
    job_opening_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Close a job opening."""
    return recruit_web_service.close_job_opening_response(auth, db, job_opening_id)


# ─────────────────────────────────────────────────────────────────────────────
# Applicants
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/applicants", response_class=HTMLResponse)
def list_applicants(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    job_opening_id: Optional[str] = None,
    source: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Job applicants list page."""
    return recruit_web_service.list_applicants_response(
        request, auth, db, search, status, job_opening_id, source, page
    )


@router.get("/applicants/new", response_class=HTMLResponse)
def new_applicant_form(
    request: Request,
    job_opening_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New applicant form."""
    return recruit_web_service.applicant_new_form_response(request, auth, db, job_opening_id)


@router.post("/applicants/new", response_class=HTMLResponse)
async def create_applicant(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new applicant."""
    return await recruit_web_service.create_applicant_response(request, auth, db)


@router.get("/applicants/{applicant_id}", response_class=HTMLResponse)
def applicant_detail(
    request: Request,
    applicant_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Applicant detail page."""
    return recruit_web_service.applicant_detail_response(request, auth, db, applicant_id)


@router.get("/applicants/{applicant_id}/edit", response_class=HTMLResponse)
def edit_applicant_form(
    request: Request,
    applicant_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit applicant form."""
    return recruit_web_service.applicant_edit_form_response(request, auth, db, applicant_id)


@router.post("/applicants/{applicant_id}/edit", response_class=HTMLResponse)
async def update_applicant(
    request: Request,
    applicant_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update an applicant."""
    return await recruit_web_service.update_applicant_response(request, auth, db, applicant_id)


@router.post("/applicants/{applicant_id}/advance")
async def advance_applicant_status(
    request: Request,
    applicant_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Advance applicant through pipeline."""
    return await recruit_web_service.advance_applicant_response(request, auth, db, applicant_id)


@router.post("/applicants/{applicant_id}/reject")
async def reject_applicant(
    request: Request,
    applicant_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Reject an applicant."""
    return await recruit_web_service.reject_applicant_response(request, auth, db, applicant_id)


@router.post("/applicants/{applicant_id}/delete")
def delete_applicant(
    applicant_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete an applicant."""
    return recruit_web_service.delete_applicant_response(auth, db, applicant_id)


# ─────────────────────────────────────────────────────────────────────────────
# Interviews
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/interviews", response_class=HTMLResponse)
def list_interviews(
    request: Request,
    status: Optional[str] = None,
    job_opening_id: Optional[str] = None,
    applicant_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Interviews list page."""
    return recruit_web_service.list_interviews_response(
        request, auth, db, status, job_opening_id, applicant_id, start_date, end_date, page
    )


@router.get("/interviews/new", response_class=HTMLResponse)
def new_interview_form(
    request: Request,
    applicant_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New interview form."""
    return recruit_web_service.interview_new_form_response(request, auth, db, applicant_id)


@router.post("/interviews/new", response_class=HTMLResponse)
async def create_interview(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Schedule a new interview."""
    return await recruit_web_service.create_interview_response(request, auth, db)


@router.get("/interviews/{interview_id}", response_class=HTMLResponse)
def interview_detail(
    request: Request,
    interview_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Interview detail page."""
    return recruit_web_service.interview_detail_response(request, auth, db, interview_id)


@router.get("/interviews/{interview_id}/edit", response_class=HTMLResponse)
def edit_interview_form(
    request: Request,
    interview_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit interview form."""
    return recruit_web_service.interview_edit_form_response(request, auth, db, interview_id)


@router.post("/interviews/{interview_id}/edit", response_class=HTMLResponse)
async def update_interview(
    request: Request,
    interview_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update an interview."""
    return await recruit_web_service.update_interview_response(request, auth, db, interview_id)


@router.post("/interviews/{interview_id}/cancel")
async def cancel_interview(
    request: Request,
    interview_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Cancel an interview."""
    return await recruit_web_service.cancel_interview_response(request, auth, db, interview_id)


@router.post("/interviews/{interview_id}/feedback")
async def record_interview_feedback(
    request: Request,
    interview_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Record interview feedback."""
    return await recruit_web_service.record_interview_feedback_response(request, auth, db, interview_id)


# ─────────────────────────────────────────────────────────────────────────────
# Job Offers
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/offers", response_class=HTMLResponse)
def list_job_offers(
    request: Request,
    status: Optional[str] = None,
    job_opening_id: Optional[str] = None,
    applicant_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Job offers list page."""
    return recruit_web_service.list_offers_response(
        request, auth, db, status, job_opening_id, applicant_id, page
    )


@router.get("/offers/new", response_class=HTMLResponse)
def new_offer_form(
    request: Request,
    applicant_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New job offer form."""
    return recruit_web_service.offer_new_form_response(request, auth, db, applicant_id)


@router.post("/offers/new", response_class=HTMLResponse)
async def create_offer(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new job offer."""
    return await recruit_web_service.create_offer_response(request, auth, db)


@router.get("/offers/{offer_id}", response_class=HTMLResponse)
def offer_detail(
    request: Request,
    offer_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Job offer detail page."""
    return recruit_web_service.offer_detail_response(request, auth, db, offer_id)


@router.get("/offers/{offer_id}/edit", response_class=HTMLResponse)
def edit_offer_form(
    request: Request,
    offer_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit job offer form."""
    return recruit_web_service.offer_edit_form_response(request, auth, db, offer_id)


@router.post("/offers/{offer_id}/edit", response_class=HTMLResponse)
async def update_offer(
    request: Request,
    offer_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a job offer."""
    return await recruit_web_service.update_offer_response(request, auth, db, offer_id)


@router.post("/offers/{offer_id}/extend")
def extend_offer(
    offer_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Extend offer to candidate."""
    return recruit_web_service.extend_offer_response(auth, db, offer_id)


@router.post("/offers/{offer_id}/accept")
def accept_offer(
    offer_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Mark offer as accepted."""
    return recruit_web_service.accept_offer_response(auth, db, offer_id)


@router.post("/offers/{offer_id}/decline")
async def decline_offer(
    request: Request,
    offer_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Mark offer as declined."""
    return await recruit_web_service.decline_offer_response(request, auth, db, offer_id)


@router.post("/offers/{offer_id}/withdraw")
async def withdraw_offer(
    request: Request,
    offer_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Withdraw an offer."""
    return await recruit_web_service.withdraw_offer_response(request, auth, db, offer_id)


# ─────────────────────────────────────────────────────────────────────────────
# Recruitment Reports
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/reports/pipeline", response_class=HTMLResponse)
def report_pipeline(
    request: Request,
    job_opening_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Recruitment pipeline report."""
    return recruit_web_service.pipeline_report_response(request, auth, db, job_opening_id)


@router.get("/reports/time-to-hire", response_class=HTMLResponse)
def report_time_to_hire(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Time to hire report."""
    return recruit_web_service.time_to_hire_report_response(request, auth, db, start_date, end_date)


@router.get("/reports/sources", response_class=HTMLResponse)
def report_sources(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Applicant source analysis report."""
    return recruit_web_service.source_analysis_report_response(request, auth, db, start_date, end_date)


@router.get("/reports/overview", response_class=HTMLResponse)
def report_overview(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Recruitment overview report."""
    return recruit_web_service.overview_report_response(request, auth, db, start_date, end_date)
