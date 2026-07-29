"""
PMS (Performance Management System) Web Routes.

OHCSF-compliant PMS routes: dashboard, contracts, monthly reviews,
PIPs, appeals, institutional performance, strategic objectives,
and reports.

All routes are accessible at /people/perf/pms/*.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.web.deps import (
    get_db_for_org,
    WebAuthContext,
    require_government_pms_mode,
    require_hr_access,
)

router = APIRouter(
    prefix="/pms",
    tags=["people-pms-web"],
    dependencies=[Depends(require_government_pms_mode)],
)


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
def pms_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """OHCSF PMS compliance dashboard."""
    from app.services.people.perf.web.ohcsf_dashboard_web import (
        OHCSFDashboardWebService,
    )

    return OHCSFDashboardWebService().dashboard_response(request, auth, db)


@router.get("/governance", response_class=HTMLResponse)
def governance_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """PMS governance dashboard."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return governance_web_service.governance_dashboard_response(request, auth, db)


# ─────────────────────────────────────────────────────────────────────────────
# Performance Contracts
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/contracts", response_class=HTMLResponse)
def list_contracts(
    request: Request,
    status: str | None = None,
    cycle_id: str | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Performance contracts list page."""
    from app.services.people.perf.web.contract_web import ContractWebService

    return ContractWebService().list_contracts_response(
        request, auth, db, status=status, cycle_id=cycle_id, search=search, page=page
    )


@router.get("/contracts/new", response_class=HTMLResponse)
def new_contract_form(
    request: Request,
    cycle_id: str | None = None,
    employee_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New performance contract form."""
    from app.services.people.perf.web.contract_web import ContractWebService

    return ContractWebService().contract_form_response(
        request, auth, db, cycle_id=cycle_id, employee_id=employee_id
    )


@router.post("/contracts/new", response_class=HTMLResponse)
async def create_contract(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new performance contract."""
    from app.services.people.perf.web.contract_web import ContractWebService

    return await ContractWebService().create_contract_response(request, auth, db)


@router.get("/contracts/{contract_id}", response_class=HTMLResponse)
def contract_detail(
    request: Request,
    contract_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Performance contract detail page."""
    from app.services.people.perf.web.contract_web import ContractWebService

    return ContractWebService().contract_detail_response(
        request, auth, db, contract_id, success=success, error=error
    )


@router.post("/contracts/{contract_id}/sign-employee", response_class=HTMLResponse)
async def sign_contract_employee(
    request: Request,
    contract_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Employee signs the performance contract."""
    from app.services.people.perf.web.contract_web import ContractWebService

    return ContractWebService().sign_employee_response(auth, db, contract_id)


@router.post("/contracts/{contract_id}/sign-supervisor", response_class=HTMLResponse)
async def sign_contract_supervisor(
    request: Request,
    contract_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Supervisor signs the performance contract."""
    from app.services.people.perf.web.contract_web import ContractWebService

    return ContractWebService().sign_supervisor_response(auth, db, contract_id)


@router.post("/contracts/{contract_id}/amend", response_class=HTMLResponse)
async def amend_contract(
    request: Request,
    contract_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create amendment and start staged signoff workflow."""
    from app.services.people.perf.web.contract_web import ContractWebService

    return await ContractWebService().amend_contract_response(
        request, auth, db, contract_id
    )


@router.post(
    "/contracts/{contract_id}/amend/approve/{stage}", response_class=HTMLResponse
)
async def approve_contract_amendment_stage(
    request: Request,
    contract_id: str,
    stage: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Approve one stage of amendment signoff chain."""
    from app.services.people.perf.web.contract_web import ContractWebService

    form_data = await request.form()
    note = str(form_data.get("signoff_note", "")).strip() or None
    return ContractWebService().approve_amendment_stage_response(
        auth, db, contract_id, stage=stage.upper(), note=note
    )


@router.post(
    "/contracts/{contract_id}/amend/reject/{stage}", response_class=HTMLResponse
)
async def reject_contract_amendment_stage(
    request: Request,
    contract_id: str,
    stage: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Reject pending amendment at a specific stage."""
    from app.services.people.perf.web.contract_web import ContractWebService

    return await ContractWebService().reject_amendment_response(
        request, auth, db, contract_id, stage=stage.upper()
    )


# ─────────────────────────────────────────────────────────────────────────────
# Monthly Reviews
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/reviews", response_class=HTMLResponse)
def list_reviews(
    request: Request,
    status: str | None = None,
    employee_id: str | None = None,
    contract_id: str | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Monthly reviews list page."""
    from app.services.people.perf.web.monthly_review_web import MonthlyReviewWebService

    return MonthlyReviewWebService().list_reviews_response(
        request,
        auth,
        db,
        status=status,
        employee_id=employee_id,
        contract_id=contract_id,
        search=search,
        page=page,
    )


@router.get("/appraisals/countersign", response_class=HTMLResponse)
def pms_countersign_queue(
    request: Request,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Government PMS countersign queue."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.pms_appraisal_queue_response(
        request, auth, db, queue="countersign", page=page
    )


@router.get("/appraisals/self-assessment", response_class=HTMLResponse)
def pms_self_assessment_queue(
    request: Request,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Government PMS self-assessment queue."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.pms_self_assessment_queue_response(
        request, auth, db, page=page
    )


@router.get("/appraisals/quarterly-reviews", response_class=HTMLResponse)
def pms_quarterly_reviews(
    request: Request,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Government PMS quarterly reviews overview."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.pms_quarterly_reviews_response(request, auth, db, page=page)


@router.get("/appraisals/manager-review", response_class=HTMLResponse)
def pms_manager_review_queue(
    request: Request,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Government PMS manager review queue."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.pms_manager_review_queue_response(
        request, auth, db, page=page
    )


@router.get("/appraisals/committee", response_class=HTMLResponse)
def pms_committee_queue(
    request: Request,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Government PMS committee queue."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.pms_appraisal_queue_response(
        request, auth, db, queue="committee", page=page
    )


@router.post("/appraisals/{appraisal_id}/start-self-assessment")
def pms_start_self_assessment(
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Start PMS self-assessment."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.pms_start_self_assessment_response(auth, db, appraisal_id)


@router.post("/appraisals/{appraisal_id}/start-manager-review")
def pms_start_manager_review(
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Start PMS manager review."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.pms_start_manager_review_response(auth, db, appraisal_id)


@router.get("/appraisals/{appraisal_id}/self-assessment", response_class=HTMLResponse)
def pms_self_assessment_form(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """PMS self-assessment form page."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.pms_self_assessment_form_response(
        request, auth, db, appraisal_id
    )


@router.post("/appraisals/{appraisal_id}/self-assessment", response_class=HTMLResponse)
async def pms_submit_self_assessment(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Submit PMS self-assessment."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.pms_submit_self_assessment_response(
        request, auth, db, appraisal_id
    )


@router.get("/appraisals/{appraisal_id}/manager-review", response_class=HTMLResponse)
def pms_manager_review_form(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """PMS manager review form page."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.pms_manager_review_form_response(
        request, auth, db, appraisal_id
    )


@router.post("/appraisals/{appraisal_id}/manager-review", response_class=HTMLResponse)
async def pms_submit_manager_review(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Submit PMS manager review."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.pms_submit_manager_review_response(
        request, auth, db, appraisal_id
    )


@router.get("/appraisals/{appraisal_id}", response_class=HTMLResponse)
def pms_appraisal_detail(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Government PMS appraisal queue detail."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.pms_appraisal_detail_response(
        request, auth, db, appraisal_id
    )


@router.post("/appraisals/{appraisal_id}/countersign", response_class=HTMLResponse)
async def pms_submit_countersign(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Government PMS countersign action."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.pms_countersign_response(
        request, auth, db, appraisal_id
    )


@router.post("/appraisals/{appraisal_id}/committee-review", response_class=HTMLResponse)
async def pms_submit_committee_review(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Government PMS committee review action."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.pms_committee_review_response(
        request, auth, db, appraisal_id
    )


@router.get("/reviews/new", response_class=HTMLResponse)
def new_review_form(
    request: Request,
    employee_id: str | None = None,
    contract_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New monthly review form."""
    from app.services.people.perf.web.monthly_review_web import MonthlyReviewWebService

    return MonthlyReviewWebService().review_form_response(
        request, auth, db, employee_id=employee_id, contract_id=contract_id
    )


@router.post("/reviews/new", response_class=HTMLResponse)
async def create_review(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new monthly review."""
    from app.services.people.perf.web.monthly_review_web import MonthlyReviewWebService

    return await MonthlyReviewWebService().create_review_response(request, auth, db)


@router.get("/reviews/{review_id}", response_class=HTMLResponse)
def review_detail(
    request: Request,
    review_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Monthly review detail page."""
    from app.services.people.perf.web.monthly_review_web import MonthlyReviewWebService

    return MonthlyReviewWebService().review_detail_response(
        request, auth, db, review_id, success=success, error=error
    )


@router.post("/reviews/{review_id}/submit", response_class=HTMLResponse)
async def submit_review(
    request: Request,
    review_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Submit a monthly review."""
    from app.services.people.perf.web.monthly_review_web import MonthlyReviewWebService

    return await MonthlyReviewWebService().submit_review_response(
        request, auth, db, review_id
    )


@router.post("/reviews/{review_id}/acknowledge", response_class=HTMLResponse)
async def acknowledge_review(
    request: Request,
    review_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Acknowledge a monthly review."""
    from app.services.people.perf.web.monthly_review_web import MonthlyReviewWebService

    return MonthlyReviewWebService().acknowledge_review_response(auth, db, review_id)


# ─────────────────────────────────────────────────────────────────────────────
# Performance Improvement Plans (PIPs)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/pips", response_class=HTMLResponse)
def list_pips(
    request: Request,
    status: str | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """PIPs list page."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return PIPWebService().list_pips_response(
        request,
        auth,
        db,
        status=status,
        search=search,
        page=page,
    )


@router.get("/pips/new", response_class=HTMLResponse)
def new_pip_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New PIP form."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return PIPWebService().pip_new_form_response(request, auth, db)


@router.post("/pips/new", response_class=HTMLResponse)
async def create_pip(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new PIP."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return await PIPWebService().create_pip_response(request, auth, db)


@router.get("/pips/{pip_id}", response_class=HTMLResponse)
def pip_detail(
    request: Request,
    pip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """PIP detail page."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return PIPWebService().pip_detail_response(request, auth, db, pip_id)


@router.post("/pips/{pip_id}/activate", response_class=HTMLResponse)
async def activate_pip(
    request: Request,
    pip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Activate a PIP."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return await PIPWebService().activate_pip_response(request, auth, db, pip_id)


@router.post("/pips/{pip_id}/extend", response_class=HTMLResponse)
async def extend_pip(
    request: Request,
    pip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Extend a PIP end date."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return await PIPWebService().extend_pip_response(request, auth, db, pip_id)


@router.post("/pips/{pip_id}/record-review", response_class=HTMLResponse)
async def pip_record_review(
    request: Request,
    pip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Record a PIP interval review."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return await PIPWebService().record_review_response(request, auth, db, pip_id)


@router.post("/pips/{pip_id}/complete", response_class=HTMLResponse)
async def complete_pip(
    request: Request,
    pip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Complete a PIP with an outcome."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return await PIPWebService().complete_pip_response(request, auth, db, pip_id)


# ─────────────────────────────────────────────────────────────────────────────
# Appeals
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/appeals", response_class=HTMLResponse)
def list_appeals(
    request: Request,
    status: str | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Appeals list page."""
    from app.services.people.perf.web.appeal_web import AppealWebService

    return AppealWebService().list_appeals_response(
        request,
        auth,
        db,
        status=status,
        search=search,
        page=page,
    )


@router.get("/appeals/new", response_class=HTMLResponse)
def new_appeal_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New appeal form."""
    from app.services.people.perf.web.appeal_web import AppealWebService

    return AppealWebService().appeal_new_form_response(request, auth, db)


@router.post("/appeals/new", response_class=HTMLResponse)
async def create_appeal(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new appeal."""
    from app.services.people.perf.web.appeal_web import AppealWebService

    return await AppealWebService().create_appeal_response(request, auth, db)


@router.get("/appeals/{appeal_id}", response_class=HTMLResponse)
def appeal_detail(
    request: Request,
    appeal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Appeal detail page."""
    from app.services.people.perf.web.appeal_web import AppealWebService

    return AppealWebService().appeal_detail_response(request, auth, db, appeal_id)


@router.post("/appeals/{appeal_id}/assign-mediator", response_class=HTMLResponse)
async def assign_mediator(
    request: Request,
    appeal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Assign a mediator to an appeal."""
    from app.services.people.perf.web.appeal_web import AppealWebService

    return await AppealWebService().assign_mediator_response(
        request, auth, db, appeal_id
    )


@router.post("/appeals/{appeal_id}/mediation-outcome", response_class=HTMLResponse)
async def record_mediation_outcome(
    request: Request,
    appeal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Record the outcome of mediation for an appeal."""
    from app.services.people.perf.web.appeal_web import AppealWebService

    return await AppealWebService().record_mediation_outcome_response(
        request, auth, db, appeal_id
    )


@router.post("/appeals/{appeal_id}/committee-decision", response_class=HTMLResponse)
async def record_committee_decision(
    request: Request,
    appeal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Record an appeal committee decision."""
    from app.services.people.perf.web.appeal_web import AppealWebService

    return await AppealWebService().record_committee_decision_response(
        request, auth, db, appeal_id
    )


@router.post("/appeals/{appeal_id}/communicate", response_class=HTMLResponse)
async def communicate_appeal_decision(
    request: Request,
    appeal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Communicate the final decision on an appeal to the appellant."""
    from app.services.people.perf.web.appeal_web import AppealWebService

    return await AppealWebService().communicate_decision_response(
        request, auth, db, appeal_id
    )


# ─────────────────────────────────────────────────────────────────────────────
# Institutional Performance
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/institutional", response_class=HTMLResponse)
def list_institutional(
    request: Request,
    status: str | None = None,
    cycle_id: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Institutional performance list page."""
    from app.services.people.perf.web.institutional_web import InstitutionalWebService

    return InstitutionalWebService().list_institutional_response(
        request,
        auth,
        db,
        status=status,
        cycle_id=cycle_id,
        page=page,
    )


@router.get("/institutional/new", response_class=HTMLResponse)
def new_institutional_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New institutional performance form."""
    from app.services.people.perf.web.institutional_web import InstitutionalWebService

    return InstitutionalWebService().institutional_new_form_response(request, auth, db)


@router.post("/institutional/new", response_class=HTMLResponse)
async def create_institutional(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new institutional performance record."""
    from app.services.people.perf.web.institutional_web import InstitutionalWebService

    return await InstitutionalWebService().create_institutional_response(
        request, auth, db
    )


@router.get("/institutional/{inst_perf_id}", response_class=HTMLResponse)
def institutional_detail(
    request: Request,
    inst_perf_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Institutional performance detail page."""
    from app.services.people.perf.web.institutional_web import InstitutionalWebService

    return InstitutionalWebService().institutional_detail_response(
        request, auth, db, inst_perf_id
    )


@router.post("/institutional/{inst_perf_id}/score", response_class=HTMLResponse)
async def score_institutional(
    request: Request,
    inst_perf_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Score criteria for an institutional performance record."""
    from app.services.people.perf.web.institutional_web import InstitutionalWebService

    return await InstitutionalWebService().score_institutional_response(
        request, auth, db, inst_perf_id
    )


@router.post("/institutional/{inst_perf_id}/reconcile", response_class=HTMLResponse)
async def reconcile_institutional(
    request: Request,
    inst_perf_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Reconcile institutional performance with employee ratings."""
    from app.services.people.perf.web.institutional_web import InstitutionalWebService

    return await InstitutionalWebService().reconcile_institutional_response(
        request, auth, db, inst_perf_id
    )


@router.post(
    "/institutional/{inst_perf_id}/assign-governance", response_class=HTMLResponse
)
async def assign_institutional_governance(
    request: Request,
    inst_perf_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Assign governance owner/reviewer/approver for institutional record."""
    from app.services.people.perf.web.institutional_web import InstitutionalWebService

    return await InstitutionalWebService().assign_governance_response(
        request, auth, db, inst_perf_id
    )


@router.post(
    "/institutional/{inst_perf_id}/transition-stage", response_class=HTMLResponse
)
async def transition_institutional_stage(
    request: Request,
    inst_perf_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Transition institutional governance workflow stage."""
    from app.services.people.perf.web.institutional_web import InstitutionalWebService

    return await InstitutionalWebService().transition_governance_response(
        request, auth, db, inst_perf_id
    )


# ─────────────────────────────────────────────────────────────────────────────
# Strategic Objectives
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/objectives", response_class=HTMLResponse)
def list_objectives(
    request: Request,
    cycle_id: str | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Strategic objectives list page."""
    from app.services.people.perf.web.strategic_objective_web import (
        StrategicObjectiveWebService,
    )

    return StrategicObjectiveWebService().list_objectives_response(
        request,
        auth,
        db,
        cycle_id=cycle_id,
        search=search,
        page=page,
    )


@router.get("/objectives/new", response_class=HTMLResponse)
def new_objective_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New strategic objective form."""
    from app.services.people.perf.web.strategic_objective_web import (
        StrategicObjectiveWebService,
    )

    return StrategicObjectiveWebService().objective_new_form_response(request, auth, db)


@router.post("/objectives/new", response_class=HTMLResponse)
async def create_objective(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new strategic objective."""
    from app.services.people.perf.web.strategic_objective_web import (
        StrategicObjectiveWebService,
    )

    return await StrategicObjectiveWebService().create_objective_response(
        request, auth, db
    )


# ─────────────────────────────────────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/reports", response_class=HTMLResponse)
def pms_reports_hub(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """PMS reports hub."""
    from app.services.people.perf.web.pms_reports_web import PMSReportsWebService

    return PMSReportsWebService().reports_hub_response(request, auth, db)


@router.get("/reports/{report_type}", response_class=HTMLResponse)
def pms_report(
    request: Request,
    report_type: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Individual PMS report page."""
    from app.services.people.perf.web.pms_reports_web import PMSReportsWebService

    return PMSReportsWebService().report_response(
        request,
        auth,
        db,
        report_type=report_type,
    )


# ─────────────────────────────────────────────────────────────────────────────
# KRAs (Shared Building Block)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/kras", response_class=HTMLResponse)
def pms_list_kras(
    request: Request,
    search: str | None = None,
    is_active: str | None = None,
    department_id: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """KRAs list page (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.list_kras_response(
        request,
        auth,
        db,
        search=search,
        is_active=is_active,
        department_id=department_id,
        page=page,
    )


@router.get("/kras/new", response_class=HTMLResponse)
def pms_new_kra_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New KRA form (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.kra_new_form_response(request, auth, db)


@router.post("/kras/new", response_class=HTMLResponse)
async def pms_create_kra(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new KRA (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.create_kra_response(request, auth, db)


@router.get("/kras/{kra_id}", response_class=HTMLResponse)
def pms_kra_detail(
    request: Request,
    kra_id: str,
    success: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """KRA detail page (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.kra_detail_response(
        request, auth, db, kra_id, success=success
    )


@router.get("/kras/{kra_id}/edit", response_class=HTMLResponse)
def pms_edit_kra_form(
    request: Request,
    kra_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit KRA form (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.kra_edit_form_response(request, auth, db, kra_id)


@router.post("/kras/{kra_id}/edit", response_class=HTMLResponse)
async def pms_update_kra(
    request: Request,
    kra_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Update a KRA (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.update_kra_response(request, auth, db, kra_id)


@router.post("/kras/{kra_id}/toggle-active")
def pms_toggle_kra_active(
    request: Request,
    kra_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Toggle KRA active status (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.toggle_kra_active_response(request, auth, db, kra_id)


@router.post("/kras/{kra_id}/delete")
def pms_delete_kra(
    request: Request,
    kra_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a KRA (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.delete_kra_response(request, auth, db, kra_id)


# ─────────────────────────────────────────────────────────────────────────────
# Cycles (Shared Building Block)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/cycles", response_class=HTMLResponse)
def pms_list_cycles(
    request: Request,
    status: str | None = None,
    year: int | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Appraisal cycles list page (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.list_cycles_response(
        request,
        auth,
        db,
        status=status,
        year=str(year) if year is not None else None,
        search=search,
        page=page,
    )


@router.get("/cycles/new", response_class=HTMLResponse)
def pms_new_cycle_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New appraisal cycle form (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.cycle_new_form_response(request, auth, db)


@router.post("/cycles/new", response_class=HTMLResponse)
async def pms_create_cycle(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new appraisal cycle (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.create_cycle_response(request, auth, db)


@router.get("/cycles/{cycle_id}", response_class=HTMLResponse)
def pms_cycle_detail(
    request: Request,
    cycle_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Appraisal cycle detail page (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.cycle_detail_response(
        request, auth, db, cycle_id, success=success, error=error
    )


@router.get("/cycles/{cycle_id}/edit", response_class=HTMLResponse)
def pms_edit_cycle_form(
    request: Request,
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit appraisal cycle form (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.cycle_edit_form_response(request, auth, db, cycle_id)


@router.post("/cycles/{cycle_id}/edit", response_class=HTMLResponse)
async def pms_update_cycle(
    request: Request,
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Update an appraisal cycle (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.update_cycle_response(request, auth, db, cycle_id)


@router.post("/cycles/{cycle_id}/activate")
def pms_activate_cycle(
    request: Request,
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Activate an appraisal cycle (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.activate_cycle_response(request, auth, db, cycle_id)


@router.post("/cycles/{cycle_id}/advance")
def pms_advance_cycle(
    request: Request,
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Advance cycle to next phase (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.advance_cycle_response(request, auth, db, cycle_id)


@router.post("/cycles/{cycle_id}/cancel")
def pms_cancel_cycle(
    request: Request,
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Cancel an appraisal cycle (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.cancel_cycle_response(request, auth, db, cycle_id)


@router.post("/cycles/{cycle_id}/delete")
def pms_delete_cycle(
    request: Request,
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete an appraisal cycle (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.delete_cycle_response(request, auth, db, cycle_id)


# ─────────────────────────────────────────────────────────────────────────────
# Goals & KPIs (Shared Building Block)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/goals", response_class=HTMLResponse)
def pms_list_goals(
    request: Request,
    status: str | None = None,
    search: str | None = None,
    employee_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """KPIs list page (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.list_goals_response(
        request, auth, db, status, search, employee_id, start_date, end_date, page
    )


@router.get("/goals/new", response_class=HTMLResponse)
def pms_new_goal_form(
    request: Request,
    employee_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New KPI form (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.goal_new_form_response(request, auth, db, employee_id)


@router.post("/goals/new", response_class=HTMLResponse)
async def pms_create_goal(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new KPI (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.create_goal_response(request, auth, db)


@router.get("/goals/{kpi_id}", response_class=HTMLResponse)
def pms_goal_detail(
    request: Request,
    kpi_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """KPI detail page (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.goal_detail_response(
        request, auth, db, kpi_id, success, error
    )


@router.get("/goals/{kpi_id}/edit", response_class=HTMLResponse)
def pms_edit_goal_form(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit KPI form (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.goal_edit_form_response(request, auth, db, kpi_id)


@router.post("/goals/{kpi_id}/edit", response_class=HTMLResponse)
async def pms_update_goal(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Update a KPI (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.update_goal_response(request, auth, db, kpi_id)


@router.post("/goals/{kpi_id}/update-progress")
async def pms_update_goal_progress(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Update KPI progress (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.update_goal_progress_response(
        request, auth, db, kpi_id
    )


@router.post("/goals/{kpi_id}/delete")
def pms_delete_goal(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a KPI (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.delete_goal_response(request, auth, db, kpi_id)


# ─────────────────────────────────────────────────────────────────────────────
# Feedback (Shared Building Block)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/feedback", response_class=HTMLResponse)
def pms_list_feedback_requests(
    request: Request,
    appraisal_id: str | None = None,
    feedback_type: str | None = None,
    submitted: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Feedback requests list page (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.list_feedback_response(
        request, auth, db, appraisal_id, feedback_type, submitted, page
    )


@router.get("/feedback/request", response_class=HTMLResponse)
def pms_request_feedback_form(
    request: Request,
    appraisal_id: str = Query(...),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Request feedback form (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.request_feedback_form_response(
        request, auth, db, appraisal_id
    )


@router.post("/feedback/request", response_class=HTMLResponse)
async def pms_create_feedback_request(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create feedback request (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.create_feedback_request_response(request, auth, db)


@router.get("/feedback/{feedback_id}", response_class=HTMLResponse)
def pms_feedback_detail(
    request: Request,
    feedback_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Feedback detail page (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.feedback_detail_response(
        request, auth, db, feedback_id, success, error
    )


@router.get("/feedback/{feedback_id}/submit", response_class=HTMLResponse)
def pms_submit_feedback_form(
    request: Request,
    feedback_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Submit feedback form (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.submit_feedback_form_response(
        request, auth, db, feedback_id
    )


@router.post("/feedback/{feedback_id}/submit", response_class=HTMLResponse)
async def pms_submit_feedback(
    request: Request,
    feedback_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Submit feedback (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.submit_feedback_response(
        request, auth, db, feedback_id
    )


@router.post("/feedback/{feedback_id}/delete")
def pms_delete_feedback(
    request: Request,
    feedback_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a feedback request (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.delete_feedback_response(request, auth, db, feedback_id)


# ─────────────────────────────────────────────────────────────────────────────
# Templates (Government Mode Access)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/templates", response_class=HTMLResponse)
def pms_list_templates(
    request: Request,
    search: str | None = None,
    is_active: str | None = None,
    department_id: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Appraisal templates list page (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.list_templates_response(
        request,
        auth,
        db,
        search=search,
        is_active=is_active,
        department_id=department_id,
        page=page,
    )


@router.get("/templates/new", response_class=HTMLResponse)
def pms_new_template_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New appraisal template form (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.template_new_form_response(request, auth, db)


@router.post("/templates/new", response_class=HTMLResponse)
async def pms_create_template(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new appraisal template (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.create_template_response(request, auth, db)


@router.get("/templates/{template_id}", response_class=HTMLResponse)
def pms_template_detail(
    request: Request,
    template_id: str,
    success: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Appraisal template detail page (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.template_detail_response(
        request,
        auth,
        db,
        template_id,
        success=success,
    )


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def pms_edit_template_form(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit appraisal template form (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.template_edit_form_response(request, auth, db, template_id)


@router.post("/templates/{template_id}/edit", response_class=HTMLResponse)
async def pms_update_template(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Update an appraisal template (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return await perf_web_service.update_template_response(
        request, auth, db, template_id
    )


@router.post("/templates/{template_id}/toggle-active")
def pms_toggle_template_active(
    template_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Toggle template active status (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.toggle_template_active_response(auth, db, template_id)


@router.post("/templates/{template_id}/delete")
def pms_delete_template(
    template_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a template (PMS mode)."""
    from app.services.people.perf.web import perf_web_service

    return perf_web_service.delete_template_response(auth, db, template_id)


# ─────────────────────────────────────────────────────────────────────────────
# Governance Grievances
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/grievances", response_class=HTMLResponse)
def list_grievances(
    request: Request,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """PMS grievance list page."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return governance_web_service.grievances_response(request, auth, db, status, page)


@router.get("/grievances/new", response_class=HTMLResponse)
def new_grievance_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New grievance form page."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return governance_web_service.grievance_new_form_response(request, auth, db)


@router.post("/grievances/new", response_class=HTMLResponse)
async def create_grievance(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create governance grievance."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return await governance_web_service.grievance_create_response(request, auth, db)


@router.post("/grievances/{grievance_id}/assign", response_class=HTMLResponse)
async def assign_grievance(
    request: Request,
    grievance_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Assign grievance to HR/committee officer."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return await governance_web_service.assign_grievance_response(
        request,
        auth,
        db,
        grievance_id,
    )


@router.post("/grievances/{grievance_id}/resolve", response_class=HTMLResponse)
async def resolve_grievance(
    request: Request,
    grievance_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Resolve grievance with final notes."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return await governance_web_service.resolve_grievance_response(
        request,
        auth,
        db,
        grievance_id,
    )


@router.post("/grievances/{grievance_id}/escalate", response_class=HTMLResponse)
async def escalate_grievance(
    request: Request,
    grievance_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Escalate unresolved grievance to FCSC."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return await governance_web_service.escalate_grievance_response(
        request,
        auth,
        db,
        grievance_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stakeholder Feedback
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/stakeholder-feedback", response_class=HTMLResponse)
def list_stakeholder_feedback(
    request: Request,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Stakeholder feedback list page."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return governance_web_service.feedback_response(request, auth, db, status, page)


@router.get("/stakeholder-feedback/new", response_class=HTMLResponse)
def new_stakeholder_feedback_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """New stakeholder feedback form page."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return governance_web_service.feedback_new_form_response(request, auth, db)


@router.post("/stakeholder-feedback/new", response_class=HTMLResponse)
async def create_stakeholder_feedback(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create stakeholder feedback."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return await governance_web_service.feedback_create_response(request, auth, db)


# ─────────────────────────────────────────────────────────────────────────────
# Rewards and Recognition
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/rewards", response_class=HTMLResponse)
def rewards_hub(
    request: Request,
    status: str | None = None,
    cycle_id: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Rewards and recognition workflow page."""
    from app.services.people.perf.web.reward_web import reward_web_service

    return reward_web_service.rewards_hub_response(
        request,
        auth,
        db,
        status=status,
        cycle_id=cycle_id,
        page=page,
    )


@router.post("/rewards/nominate", response_class=HTMLResponse)
async def nominate_reward(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Create reward nomination from completed appraisal."""
    from app.services.people.perf.web.reward_web import reward_web_service

    return await reward_web_service.nominate_reward_response(request, auth, db)


@router.post("/rewards/{action_id}/approve", response_class=HTMLResponse)
async def approve_reward(
    request: Request,
    action_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Approve pending reward nomination."""
    from app.services.people.perf.web.reward_web import reward_web_service

    return await reward_web_service.approve_reward_response(
        request,
        auth,
        db,
        action_id,
    )


@router.post("/rewards/{action_id}/cancel", response_class=HTMLResponse)
async def cancel_reward(
    request: Request,
    action_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Cancel pending reward nomination."""
    from app.services.people.perf.web.reward_web import reward_web_service

    return await reward_web_service.cancel_reward_response(
        request,
        auth,
        db,
        action_id,
    )
