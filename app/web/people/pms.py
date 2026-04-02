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

from app.web.deps import WebAuthContext, get_db, require_hr_access

router = APIRouter(prefix="/pms", tags=["people-pms-web"])


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
def pms_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Employee signs the performance contract."""
    from app.services.people.perf.web.contract_web import ContractWebService

    return ContractWebService().sign_employee_response(auth, db, contract_id)


@router.post("/contracts/{contract_id}/sign-supervisor", response_class=HTMLResponse)
async def sign_contract_supervisor(
    request: Request,
    contract_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Supervisor signs the performance contract."""
    from app.services.people.perf.web.contract_web import ContractWebService

    return ContractWebService().sign_supervisor_response(auth, db, contract_id)


@router.post("/contracts/{contract_id}/amend", response_class=HTMLResponse)
async def amend_contract(
    request: Request,
    contract_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create amendment and start staged signoff workflow."""
    from app.services.people.perf.web.contract_web import ContractWebService

    return await ContractWebService().amend_contract_response(request, auth, db, contract_id)


@router.post("/contracts/{contract_id}/amend/approve/{stage}", response_class=HTMLResponse)
async def approve_contract_amendment_stage(
    request: Request,
    contract_id: str,
    stage: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Approve one stage of amendment signoff chain."""
    from app.services.people.perf.web.contract_web import ContractWebService

    form_data = await request.form()
    note = str(form_data.get("signoff_note", "")).strip() or None
    return ContractWebService().approve_amendment_stage_response(
        auth, db, contract_id, stage=stage.upper(), note=note
    )


@router.post("/contracts/{contract_id}/amend/reject/{stage}", response_class=HTMLResponse)
async def reject_contract_amendment_stage(
    request: Request,
    contract_id: str,
    stage: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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


@router.get("/reviews/new", response_class=HTMLResponse)
def new_review_form(
    request: Request,
    employee_id: str | None = None,
    contract_id: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """New PIP form."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return PIPWebService().pip_new_form_response(request, auth, db)


@router.post("/pips/new", response_class=HTMLResponse)
async def create_pip(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new PIP."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return await PIPWebService().create_pip_response(request, auth, db)


@router.get("/pips/{pip_id}", response_class=HTMLResponse)
def pip_detail(
    request: Request,
    pip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """PIP detail page."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return PIPWebService().pip_detail_response(request, auth, db, pip_id)


@router.post("/pips/{pip_id}/activate", response_class=HTMLResponse)
async def activate_pip(
    request: Request,
    pip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Activate a PIP."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return await PIPWebService().activate_pip_response(request, auth, db, pip_id)


@router.post("/pips/{pip_id}/extend", response_class=HTMLResponse)
async def extend_pip(
    request: Request,
    pip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Extend a PIP end date."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return await PIPWebService().extend_pip_response(request, auth, db, pip_id)


@router.post("/pips/{pip_id}/record-review", response_class=HTMLResponse)
async def pip_record_review(
    request: Request,
    pip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Record a PIP interval review."""
    from app.services.people.perf.web.pip_web import PIPWebService

    return await PIPWebService().record_review_response(request, auth, db, pip_id)


@router.post("/pips/{pip_id}/complete", response_class=HTMLResponse)
async def complete_pip(
    request: Request,
    pip_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """New appeal form."""
    from app.services.people.perf.web.appeal_web import AppealWebService

    return AppealWebService().appeal_new_form_response(request, auth, db)


@router.post("/appeals/new", response_class=HTMLResponse)
async def create_appeal(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new appeal."""
    from app.services.people.perf.web.appeal_web import AppealWebService

    return await AppealWebService().create_appeal_response(request, auth, db)


@router.get("/appeals/{appeal_id}", response_class=HTMLResponse)
def appeal_detail(
    request: Request,
    appeal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appeal detail page."""
    from app.services.people.perf.web.appeal_web import AppealWebService

    return AppealWebService().appeal_detail_response(request, auth, db, appeal_id)


@router.post("/appeals/{appeal_id}/assign-mediator", response_class=HTMLResponse)
async def assign_mediator(
    request: Request,
    appeal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """New institutional performance form."""
    from app.services.people.perf.web.institutional_web import InstitutionalWebService

    return InstitutionalWebService().institutional_new_form_response(request, auth, db)


@router.post("/institutional/new", response_class=HTMLResponse)
async def create_institutional(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Reconcile institutional performance with employee ratings."""
    from app.services.people.perf.web.institutional_web import InstitutionalWebService

    return await InstitutionalWebService().reconcile_institutional_response(
        request, auth, db, inst_perf_id
    )


@router.post("/institutional/{inst_perf_id}/assign-governance", response_class=HTMLResponse)
async def assign_institutional_governance(
    request: Request,
    inst_perf_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Assign governance owner/reviewer/approver for institutional record."""
    from app.services.people.perf.web.institutional_web import InstitutionalWebService

    return await InstitutionalWebService().assign_governance_response(
        request, auth, db, inst_perf_id
    )


@router.post("/institutional/{inst_perf_id}/transition-stage", response_class=HTMLResponse)
async def transition_institutional_stage(
    request: Request,
    inst_perf_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """PMS reports hub."""
    from app.services.people.perf.web.pms_reports_web import PMSReportsWebService

    return PMSReportsWebService().reports_hub_response(request, auth, db)


@router.get("/reports/{report_type}", response_class=HTMLResponse)
def pms_report(
    request: Request,
    report_type: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
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
# Governance Grievances
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/grievances", response_class=HTMLResponse)
def list_grievances(
    request: Request,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """PMS grievance list page."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return governance_web_service.grievances_response(request, auth, db, status, page)


@router.get("/grievances/new", response_class=HTMLResponse)
def new_grievance_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New grievance form page."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return governance_web_service.grievance_new_form_response(request, auth, db)


@router.post("/grievances/new", response_class=HTMLResponse)
async def create_grievance(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create governance grievance."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return await governance_web_service.grievance_create_response(request, auth, db)


@router.post("/grievances/{grievance_id}/assign", response_class=HTMLResponse)
async def assign_grievance(
    request: Request,
    grievance_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Stakeholder feedback list page."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return governance_web_service.feedback_response(request, auth, db, status, page)


@router.get("/stakeholder-feedback/new", response_class=HTMLResponse)
def new_stakeholder_feedback_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New stakeholder feedback form page."""
    from app.services.people.perf.web.governance_web import governance_web_service

    return governance_web_service.feedback_new_form_response(request, auth, db)


@router.post("/stakeholder-feedback/new", response_class=HTMLResponse)
async def create_stakeholder_feedback(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Create reward nomination from completed appraisal."""
    from app.services.people.perf.web.reward_web import reward_web_service

    return await reward_web_service.nominate_reward_response(request, auth, db)


@router.post("/rewards/{action_id}/approve", response_class=HTMLResponse)
async def approve_reward(
    request: Request,
    action_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Cancel pending reward nomination."""
    from app.services.people.perf.web.reward_web import reward_web_service

    return await reward_web_service.cancel_reward_response(
        request,
        auth,
        db,
        action_id,
    )
