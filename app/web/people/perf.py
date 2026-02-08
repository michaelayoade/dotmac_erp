"""
Performance web routes.

Lists appraisals and KPIs with full CRUD support.
All business logic is delegated to the perf_web_service.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.people.perf.web import perf_web_service
from app.web.deps import WebAuthContext, get_db, require_hr_access

router = APIRouter(prefix="/perf", tags=["people-perf-web"])


# ─────────────────────────────────────────────────────────────────────────────
# Appraisals
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/appraisals", response_class=HTMLResponse)
def list_appraisals(
    request: Request,
    status: str | None = None,
    employee_id: str | None = None,
    cycle_id: str | None = None,
    manager_id: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appraisals list page."""
    return perf_web_service.list_appraisals_response(
        request, auth, db, status, employee_id, cycle_id, manager_id, page
    )


@router.get("/appraisals/new", response_class=HTMLResponse)
def new_appraisal_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New appraisal form."""
    return perf_web_service.appraisal_new_form_response(request, auth, db)


@router.post("/appraisals/new", response_class=HTMLResponse)
async def create_appraisal(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new appraisal."""
    return await perf_web_service.create_appraisal_response(request, auth, db)


@router.get("/appraisals/{appraisal_id}", response_class=HTMLResponse)
def appraisal_detail(
    request: Request,
    appraisal_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appraisal detail page."""
    return perf_web_service.appraisal_detail_response(
        request, auth, db, appraisal_id, success, error
    )


@router.get("/appraisals/{appraisal_id}/edit", response_class=HTMLResponse)
def edit_appraisal_form(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit appraisal form."""
    return perf_web_service.appraisal_edit_form_response(
        request, auth, db, appraisal_id
    )


@router.post("/appraisals/{appraisal_id}/edit", response_class=HTMLResponse)
async def update_appraisal(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update an appraisal."""
    return await perf_web_service.update_appraisal_response(
        request, auth, db, appraisal_id
    )


@router.post("/appraisals/{appraisal_id}/cancel")
def cancel_appraisal(
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Cancel an appraisal."""
    return perf_web_service.cancel_appraisal_response(auth, db, appraisal_id)


# ─────────────────────────────────────────────────────────────────────────────
# Appraisal Workflow
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/appraisals/{appraisal_id}/start-self-assessment")
def start_self_assessment(
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Start self-assessment phase (DRAFT -> SELF_ASSESSMENT)."""
    return perf_web_service.start_self_assessment_response(auth, db, appraisal_id)


@router.get("/appraisals/{appraisal_id}/self-assessment", response_class=HTMLResponse)
def self_assessment_form(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Self-assessment form page."""
    return perf_web_service.self_assessment_form_response(
        request, auth, db, appraisal_id
    )


@router.post("/appraisals/{appraisal_id}/self-assessment", response_class=HTMLResponse)
async def submit_self_assessment(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit self-assessment."""
    return await perf_web_service.submit_self_assessment_response(
        request, auth, db, appraisal_id
    )


@router.get("/appraisals/{appraisal_id}/manager-review", response_class=HTMLResponse)
def manager_review_form(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Manager review form page."""
    return perf_web_service.manager_review_form_response(
        request, auth, db, appraisal_id
    )


@router.post("/appraisals/{appraisal_id}/manager-review", response_class=HTMLResponse)
async def submit_manager_review(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit manager review."""
    return await perf_web_service.submit_manager_review_response(
        request, auth, db, appraisal_id
    )


@router.get("/appraisals/{appraisal_id}/calibration", response_class=HTMLResponse)
def calibration_form(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Calibration form page."""
    return perf_web_service.calibration_form_response(request, auth, db, appraisal_id)


@router.post("/appraisals/{appraisal_id}/calibration", response_class=HTMLResponse)
async def submit_calibration(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit calibration and complete appraisal."""
    return await perf_web_service.submit_calibration_response(
        request, auth, db, appraisal_id
    )


# ─────────────────────────────────────────────────────────────────────────────
# Feedback
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/feedback", response_class=HTMLResponse)
def list_feedback_requests(
    request: Request,
    appraisal_id: str | None = None,
    feedback_type: str | None = None,
    submitted: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Feedback requests list page."""
    return perf_web_service.list_feedback_response(
        request, auth, db, appraisal_id, feedback_type, submitted, page
    )


@router.get("/feedback/request", response_class=HTMLResponse)
def request_feedback_form(
    request: Request,
    appraisal_id: str = Query(...),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Request feedback form."""
    return perf_web_service.request_feedback_form_response(
        request, auth, db, appraisal_id
    )


@router.post("/feedback/request", response_class=HTMLResponse)
async def create_feedback_request(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create feedback request."""
    return await perf_web_service.create_feedback_request_response(request, auth, db)


@router.get("/feedback/{feedback_id}", response_class=HTMLResponse)
def feedback_detail(
    request: Request,
    feedback_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Feedback detail page."""
    return perf_web_service.feedback_detail_response(
        request, auth, db, feedback_id, success, error
    )


@router.get("/feedback/{feedback_id}/submit", response_class=HTMLResponse)
def submit_feedback_form(
    request: Request,
    feedback_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit feedback form."""
    return perf_web_service.submit_feedback_form_response(
        request, auth, db, feedback_id
    )


@router.post("/feedback/{feedback_id}/submit", response_class=HTMLResponse)
async def submit_feedback(
    request: Request,
    feedback_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit feedback."""
    return await perf_web_service.submit_feedback_response(
        request, auth, db, feedback_id
    )


@router.post("/feedback/{feedback_id}/delete")
def delete_feedback(
    feedback_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a feedback request."""
    return perf_web_service.delete_feedback_response(auth, db, feedback_id)


# ─────────────────────────────────────────────────────────────────────────────
# Goals & KPIs
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/goals", response_class=HTMLResponse)
def list_kpis(
    request: Request,
    status: str | None = None,
    search: str | None = None,
    employee_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """KPIs list page."""
    return perf_web_service.list_goals_response(
        request, auth, db, status, search, employee_id, start_date, end_date, page
    )


@router.get("/goals/new", response_class=HTMLResponse)
def new_kpi_form(
    request: Request,
    employee_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New KPI form."""
    return perf_web_service.goal_new_form_response(request, auth, db, employee_id)


@router.post("/goals/new", response_class=HTMLResponse)
async def create_kpi(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new KPI."""
    return await perf_web_service.create_goal_response(request, auth, db)


@router.get("/goals/{kpi_id}", response_class=HTMLResponse)
def kpi_detail(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """KPI detail page."""
    return perf_web_service.goal_detail_response(request, auth, db, kpi_id)


@router.get("/goals/{kpi_id}/edit", response_class=HTMLResponse)
def edit_kpi_form(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit KPI form."""
    return perf_web_service.goal_edit_form_response(request, auth, db, kpi_id)


@router.post("/goals/{kpi_id}/edit", response_class=HTMLResponse)
async def update_kpi(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a KPI."""
    return await perf_web_service.update_goal_response(request, auth, db, kpi_id)


@router.post("/goals/{kpi_id}/update-progress")
async def update_kpi_progress(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update KPI progress."""
    return await perf_web_service.update_goal_progress_response(
        request, auth, db, kpi_id
    )


@router.post("/goals/{kpi_id}/delete")
def delete_kpi(
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a KPI."""
    return perf_web_service.delete_goal_response(auth, db, kpi_id)


# ─────────────────────────────────────────────────────────────────────────────
# Appraisal Cycles
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/cycles", response_class=HTMLResponse)
def list_cycles(
    request: Request,
    status: str | None = None,
    year: int | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appraisal cycles list page."""
    return perf_web_service.list_cycles_response(
        request, auth, db, status, year, search, page
    )


@router.get("/cycles/new", response_class=HTMLResponse)
def new_cycle_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New appraisal cycle form."""
    return perf_web_service.cycle_new_form_response(request, auth, db)


@router.post("/cycles/new", response_class=HTMLResponse)
async def create_cycle(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new appraisal cycle."""
    return await perf_web_service.create_cycle_response(request, auth, db)


@router.get("/cycles/{cycle_id}", response_class=HTMLResponse)
def cycle_detail(
    request: Request,
    cycle_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appraisal cycle detail page."""
    return perf_web_service.cycle_detail_response(
        request, auth, db, cycle_id, success, error
    )


@router.get("/cycles/{cycle_id}/edit", response_class=HTMLResponse)
def edit_cycle_form(
    request: Request,
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit appraisal cycle form."""
    return perf_web_service.cycle_edit_form_response(request, auth, db, cycle_id)


@router.post("/cycles/{cycle_id}/edit", response_class=HTMLResponse)
async def update_cycle(
    request: Request,
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update an appraisal cycle."""
    return await perf_web_service.update_cycle_response(request, auth, db, cycle_id)


@router.post("/cycles/{cycle_id}/activate")
def activate_cycle(
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Activate an appraisal cycle."""
    return perf_web_service.activate_cycle_response(auth, db, cycle_id)


@router.post("/cycles/{cycle_id}/advance")
def advance_cycle(
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Advance cycle to next phase."""
    return perf_web_service.advance_cycle_response(auth, db, cycle_id)


@router.post("/cycles/{cycle_id}/cancel")
def cancel_cycle(
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Cancel an appraisal cycle."""
    return perf_web_service.cancel_cycle_response(auth, db, cycle_id)


@router.post("/cycles/{cycle_id}/delete")
def delete_cycle(
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete an appraisal cycle."""
    return perf_web_service.delete_cycle_response(auth, db, cycle_id)


# ─────────────────────────────────────────────────────────────────────────────
# Key Result Areas (KRAs)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/kras", response_class=HTMLResponse)
def list_kras(
    request: Request,
    search: str | None = None,
    is_active: str | None = None,
    department_id: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """KRAs list page."""
    return perf_web_service.list_kras_response(
        request, auth, db, search, is_active, department_id, page
    )


@router.get("/kras/new", response_class=HTMLResponse)
def new_kra_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New KRA form."""
    return perf_web_service.kra_new_form_response(request, auth, db)


@router.post("/kras/new", response_class=HTMLResponse)
async def create_kra(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new KRA."""
    return await perf_web_service.create_kra_response(request, auth, db)


@router.get("/kras/{kra_id}", response_class=HTMLResponse)
def kra_detail(
    request: Request,
    kra_id: str,
    success: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """KRA detail page."""
    return perf_web_service.kra_detail_response(request, auth, db, kra_id, success)


@router.get("/kras/{kra_id}/edit", response_class=HTMLResponse)
def edit_kra_form(
    request: Request,
    kra_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit KRA form."""
    return perf_web_service.kra_edit_form_response(request, auth, db, kra_id)


@router.post("/kras/{kra_id}/edit", response_class=HTMLResponse)
async def update_kra(
    request: Request,
    kra_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a KRA."""
    return await perf_web_service.update_kra_response(request, auth, db, kra_id)


@router.post("/kras/{kra_id}/toggle-active")
def toggle_kra_active(
    kra_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Toggle KRA active status."""
    return perf_web_service.toggle_kra_active_response(auth, db, kra_id)


@router.post("/kras/{kra_id}/delete")
def delete_kra(
    kra_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a KRA."""
    return perf_web_service.delete_kra_response(auth, db, kra_id)


# ─────────────────────────────────────────────────────────────────────────────
# Appraisal Templates
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/templates", response_class=HTMLResponse)
def list_templates(
    request: Request,
    search: str | None = None,
    is_active: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appraisal templates list page."""
    return perf_web_service.list_templates_response(
        request, auth, db, search, is_active, page
    )


@router.get("/templates/new", response_class=HTMLResponse)
def new_template_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New template form."""
    return perf_web_service.template_new_form_response(request, auth, db)


@router.post("/templates/new", response_class=HTMLResponse)
async def create_template(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new template."""
    return await perf_web_service.create_template_response(request, auth, db)


@router.get("/templates/{template_id}", response_class=HTMLResponse)
def template_detail(
    request: Request,
    template_id: str,
    success: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Template detail page."""
    return perf_web_service.template_detail_response(
        request, auth, db, template_id, success
    )


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def edit_template_form(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit template form."""
    return perf_web_service.template_edit_form_response(request, auth, db, template_id)


@router.post("/templates/{template_id}/edit", response_class=HTMLResponse)
async def update_template(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a template."""
    return await perf_web_service.update_template_response(
        request, auth, db, template_id
    )


@router.post("/templates/{template_id}/toggle-active")
def toggle_template_active(
    template_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Toggle template active status."""
    return perf_web_service.toggle_template_active_response(auth, db, template_id)


@router.post("/templates/{template_id}/delete")
def delete_template(
    template_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a template."""
    return perf_web_service.delete_template_response(auth, db, template_id)


# ─────────────────────────────────────────────────────────────────────────────
# Scorecards
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/scorecards", response_class=HTMLResponse)
def list_scorecards(
    request: Request,
    employee_id: str | None = None,
    cycle_id: str | None = None,
    is_finalized: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Scorecards list page."""
    return perf_web_service.list_scorecards_response(
        request, auth, db, employee_id, cycle_id, is_finalized, page
    )


@router.get("/scorecards/new", response_class=HTMLResponse)
def new_scorecard_form(
    request: Request,
    employee_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New scorecard form."""
    return perf_web_service.scorecard_new_form_response(request, auth, db, employee_id)


@router.post("/scorecards/new", response_class=HTMLResponse)
async def create_scorecard(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new scorecard."""
    return await perf_web_service.create_scorecard_response(request, auth, db)


@router.get("/scorecards/{scorecard_id}", response_class=HTMLResponse)
def scorecard_detail(
    request: Request,
    scorecard_id: str,
    success: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Scorecard detail page."""
    return perf_web_service.scorecard_detail_response(
        request, auth, db, scorecard_id, success
    )


@router.get("/scorecards/{scorecard_id}/items/{item_id}", response_class=HTMLResponse)
def scorecard_update_item_form(
    request: Request,
    scorecard_id: str,
    item_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update scorecard item form."""
    return perf_web_service.scorecard_update_item_form_response(
        request, auth, db, scorecard_id, item_id
    )


@router.post("/scorecards/{scorecard_id}/items/{item_id}", response_class=HTMLResponse)
async def update_scorecard_item(
    request: Request,
    scorecard_id: str,
    item_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a scorecard item."""
    return await perf_web_service.update_scorecard_item_response(
        request, auth, db, scorecard_id, item_id
    )


@router.get("/scorecards/{scorecard_id}/finalize", response_class=HTMLResponse)
def scorecard_finalize_form(
    request: Request,
    scorecard_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Finalize scorecard form."""
    return perf_web_service.scorecard_finalize_form_response(
        request, auth, db, scorecard_id
    )


@router.post("/scorecards/{scorecard_id}/finalize", response_class=HTMLResponse)
async def finalize_scorecard(
    request: Request,
    scorecard_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Finalize a scorecard."""
    return await perf_web_service.finalize_scorecard_response(
        request, auth, db, scorecard_id
    )


# ─────────────────────────────────────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/reports/ratings", response_class=HTMLResponse)
def report_ratings(
    request: Request,
    cycle_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Ratings distribution report."""
    return perf_web_service.ratings_report_response(request, auth, db, cycle_id)


@router.get("/reports/by-department", response_class=HTMLResponse)
def report_by_department(
    request: Request,
    cycle_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Performance by department report."""
    return perf_web_service.by_department_report_response(request, auth, db, cycle_id)


@router.get("/reports/kpi-achievement", response_class=HTMLResponse)
def report_kpi_achievement(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    department_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """KPI achievement report."""
    return perf_web_service.kpi_achievement_report_response(
        request, auth, db, start_date, end_date, department_id
    )


@router.get("/reports/trends", response_class=HTMLResponse)
def report_trends(
    request: Request,
    employee_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Performance trends report."""
    return perf_web_service.trends_report_response(request, auth, db, employee_id)
