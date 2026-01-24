"""
Performance web routes.

Lists appraisals and KPIs with full CRUD support.
"""
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.perf import AppraisalStatus, KPIStatus
from app.models.people.perf.appraisal_cycle import AppraisalCycleStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import EmployeeFilters, OrganizationService
from app.services.people.perf import PerformanceService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_hr_access


router = APIRouter(prefix="/perf", tags=["people-perf-web"])


def _parse_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    try:
        return coerce_uuid(value)
    except Exception:
        return None


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.get("/appraisals", response_class=HTMLResponse)
def list_appraisals(
    request: Request,
    status: Optional[str] = None,
    employee_id: Optional[str] = None,
    cycle_id: Optional[str] = None,
    manager_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appraisals list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = PerformanceService(db)

    status_enum = None
    if status:
        try:
            status_enum = AppraisalStatus(status)
        except ValueError:
            status_enum = None

    result = svc.list_appraisals(
        org_id,
        status=status_enum,
        employee_id=_parse_uuid(employee_id),
        cycle_id=_parse_uuid(cycle_id),
        manager_id=_parse_uuid(manager_id),
        pagination=pagination,
    )

    context = base_context(request, auth, "Appraisals", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "appraisals": result.items,
            "status": status,
            "employee_id": employee_id,
            "cycle_id": cycle_id,
            "manager_id": manager_id,
            "statuses": [s.value for s in AppraisalStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/perf/appraisals.html", context)


@router.get("/goals", response_class=HTMLResponse)
def list_kpis(
    request: Request,
    status: Optional[str] = None,
    search: Optional[str] = None,
    employee_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """KPIs list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = PerformanceService(db)

    status_enum = None
    if status:
        try:
            status_enum = KPIStatus(status)
        except ValueError:
            status_enum = None

    result = svc.list_kpis(
        org_id,
        status=status_enum,
        search=search,
        employee_id=_parse_uuid(employee_id),
        from_date=_parse_date(start_date),
        to_date=_parse_date(end_date),
        pagination=pagination,
    )

    context = base_context(request, auth, "Goals & KPIs", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "kpis": result.items,
            "status": status,
            "search": search,
            "employee_id": employee_id,
            "start_date": start_date,
            "end_date": end_date,
            "statuses": [s.value for s in KPIStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/perf/kpis.html", context)


@router.get("/feedback", response_class=HTMLResponse)
def list_feedback_requests(
    request: Request,
    appraisal_id: Optional[str] = None,
    feedback_type: Optional[str] = None,
    submitted: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Feedback requests list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = PerformanceService(db)

    submitted_filter = None
    if submitted == "true":
        submitted_filter = True
    elif submitted == "false":
        submitted_filter = False

    result = svc.list_feedback(
        org_id,
        appraisal_id=_parse_uuid(appraisal_id),
        feedback_type=feedback_type or None,
        submitted=submitted_filter,
        pagination=pagination,
    )

    context = base_context(request, auth, "360° Feedback", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "feedback_list": result.items,
            "appraisal_id": appraisal_id,
            "feedback_type": feedback_type,
            "submitted": submitted,
            "feedback_types": ["PEER", "SUBORDINATE", "EXTERNAL"],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/perf/feedback.html", context)


@router.get("/feedback/request", response_class=HTMLResponse)
def request_feedback_form(
    request: Request,
    appraisal_id: str = Query(...),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Request feedback form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    try:
        appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
    except Exception:
        return RedirectResponse(url="/people/perf/appraisals", status_code=303)

    employees = org_svc.list_employees(
        EmployeeFilters(is_active=True),
        PaginationParams(limit=500),
    ).items

    context = base_context(request, auth, "Request Feedback", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "appraisal": appraisal,
            "employees": employees,
            "feedback_types": ["PEER", "SUBORDINATE", "EXTERNAL"],
            "form_data": {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/feedback_request_form.html", context)


@router.post("/feedback/request", response_class=HTMLResponse)
def create_feedback_request(
    request: Request,
    appraisal_id: str = Form(...),
    feedback_from_id: str = Form(...),
    feedback_type: str = Form(...),
    is_anonymous: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create feedback request."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.request_feedback(
            org_id,
            appraisal_id=coerce_uuid(appraisal_id),
            feedback_from_id=coerce_uuid(feedback_from_id),
            feedback_type=feedback_type,
            is_anonymous=is_anonymous == "true",
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}?success=Feedback+requested",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)
        appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))

        context = base_context(request, auth, "Request Feedback", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "appraisal": appraisal,
                "employees": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "feedback_types": ["PEER", "SUBORDINATE", "EXTERNAL"],
                "form_data": {
                    "feedback_from_id": feedback_from_id,
                    "feedback_type": feedback_type,
                    "is_anonymous": is_anonymous,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/feedback_request_form.html", context)


@router.get("/feedback/{feedback_id}", response_class=HTMLResponse)
def feedback_detail(
    request: Request,
    feedback_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Feedback detail page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        feedback = svc.get_feedback(org_id, coerce_uuid(feedback_id))
    except Exception:
        return RedirectResponse(url="/people/perf/feedback", status_code=303)

    context = base_context(request, auth, "Feedback Details", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "feedback": feedback,
            "success": success,
            "error": error,
        }
    )
    return templates.TemplateResponse(request, "people/perf/feedback_detail.html", context)


@router.get("/feedback/{feedback_id}/submit", response_class=HTMLResponse)
def submit_feedback_form(
    request: Request,
    feedback_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit feedback form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        feedback = svc.get_feedback(org_id, coerce_uuid(feedback_id))
    except Exception:
        return RedirectResponse(url="/people/perf/feedback", status_code=303)

    if feedback.submitted_on:
        return RedirectResponse(
            url=f"/people/perf/feedback/{feedback_id}?error=Feedback+already+submitted",
            status_code=303,
        )

    context = base_context(request, auth, "Submit Feedback", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "feedback": feedback,
            "form_data": {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/feedback_submit_form.html", context)


@router.post("/feedback/{feedback_id}/submit", response_class=HTMLResponse)
def submit_feedback(
    request: Request,
    feedback_id: str,
    overall_rating: Optional[int] = Form(None),
    strengths: Optional[str] = Form(None),
    areas_for_improvement: Optional[str] = Form(None),
    general_comments: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit feedback."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.submit_feedback(
            org_id,
            coerce_uuid(feedback_id),
            overall_rating=overall_rating,
            strengths=strengths or None,
            areas_for_improvement=areas_for_improvement or None,
            general_comments=general_comments or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/feedback/{feedback_id}?success=Feedback+submitted+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        feedback = svc.get_feedback(org_id, coerce_uuid(feedback_id))

        context = base_context(request, auth, "Submit Feedback", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "feedback": feedback,
                "form_data": {
                    "overall_rating": overall_rating,
                    "strengths": strengths,
                    "areas_for_improvement": areas_for_improvement,
                    "general_comments": general_comments,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/feedback_submit_form.html", context)


@router.post("/feedback/{feedback_id}/delete")
def delete_feedback(
    request: Request,
    feedback_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a feedback request."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.delete_feedback(org_id, coerce_uuid(feedback_id))
        db.commit()
        return RedirectResponse(
            url="/people/perf/feedback?success=Feedback+request+deleted",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/perf/feedback/{feedback_id}?error={str(e)}",
            status_code=303,
        )


# ===========================================================================
# Appraisal CRUD Routes
# ===========================================================================


@router.get("/appraisals/new", response_class=HTMLResponse)
def new_appraisal_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New appraisal form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    # Get active cycles
    cycles = svc.list_cycles(org_id, pagination=PaginationParams(limit=100)).items

    # Get active employees
    employees = org_svc.list_employees(
        EmployeeFilters(is_active=True),
        PaginationParams(limit=500),
    ).items

    # Get managers (same as employees for now)
    managers = employees

    # Get templates
    templates_list = svc.list_templates(
        org_id, is_active=True, pagination=PaginationParams(limit=100)
    ).items

    # Get KRAs for manual selection
    kras = svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items

    context = base_context(request, auth, "New Appraisal", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "cycles": cycles,
            "employees": employees,
            "managers": managers,
            "templates": templates_list,
            "kras": kras,
            "form_data": {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/appraisal_form.html", context)


@router.post("/appraisals/new", response_class=HTMLResponse)
def create_appraisal(
    request: Request,
    cycle_id: str = Form(...),
    employee_id: str = Form(...),
    manager_id: str = Form(...),
    template_id: Optional[str] = Form(None),
    kra_ids: Optional[list[str]] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new appraisal."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        # Prepare KRA scores if provided
        kra_scores = None
        if kra_ids:
            kra_scores = [{"kra_id": coerce_uuid(kra_id)} for kra_id in kra_ids]

        appraisal = svc.create_appraisal(
            org_id,
            cycle_id=coerce_uuid(cycle_id),
            employee_id=coerce_uuid(employee_id),
            manager_id=coerce_uuid(manager_id),
            template_id=coerce_uuid(template_id) if template_id else None,
            kra_scores=kra_scores,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal.appraisal_id}",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)

        context = base_context(request, auth, "New Appraisal", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "cycles": svc.list_cycles(org_id, pagination=PaginationParams(limit=100)).items,
                "employees": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "managers": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "templates": svc.list_templates(
                    org_id, is_active=True, pagination=PaginationParams(limit=100)
                ).items,
                "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
                "form_data": {
                    "cycle_id": cycle_id,
                    "employee_id": employee_id,
                    "manager_id": manager_id,
                    "template_id": template_id,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/appraisal_form.html", context)


@router.get("/appraisals/{appraisal_id}", response_class=HTMLResponse)
def appraisal_detail(
    request: Request,
    appraisal_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appraisal detail page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
    except Exception as e:
        return RedirectResponse(url="/people/perf/appraisals", status_code=303)

    context = base_context(request, auth, "Appraisal Details", "perf", db=db)
    context["request"] = request
    context["appraisal"] = appraisal
    context["success"] = success
    context["error"] = error
    return templates.TemplateResponse(request, "people/perf/appraisal_detail.html", context)


@router.get("/appraisals/{appraisal_id}/edit", response_class=HTMLResponse)
def edit_appraisal_form(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit appraisal form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    try:
        appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
    except Exception:
        return RedirectResponse(url="/people/perf/appraisals", status_code=303)

    if appraisal.status != AppraisalStatus.DRAFT:
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}", status_code=303
        )

    context = base_context(request, auth, "Edit Appraisal", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "appraisal": appraisal,
            "cycles": svc.list_cycles(org_id, pagination=PaginationParams(limit=100)).items,
            "employees": org_svc.list_employees(
                EmployeeFilters(is_active=True), PaginationParams(limit=500)
            ).items,
            "managers": org_svc.list_employees(
                EmployeeFilters(is_active=True), PaginationParams(limit=500)
            ).items,
            "templates": svc.list_templates(
                org_id, is_active=True, pagination=PaginationParams(limit=100)
            ).items,
            "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
            "form_data": {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/appraisal_form.html", context)


@router.post("/appraisals/{appraisal_id}/edit", response_class=HTMLResponse)
def update_appraisal(
    request: Request,
    appraisal_id: str,
    cycle_id: str = Form(...),
    employee_id: str = Form(...),
    manager_id: str = Form(...),
    template_id: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update an appraisal."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.update_appraisal(
            org_id,
            coerce_uuid(appraisal_id),
            cycle_id=coerce_uuid(cycle_id),
            manager_id=coerce_uuid(manager_id),
            template_id=coerce_uuid(template_id) if template_id else None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}", status_code=303
        )
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)
        appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))

        context = base_context(request, auth, "Edit Appraisal", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "appraisal": appraisal,
                "cycles": svc.list_cycles(org_id, pagination=PaginationParams(limit=100)).items,
                "employees": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "managers": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "templates": svc.list_templates(
                    org_id, is_active=True, pagination=PaginationParams(limit=100)
                ).items,
                "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
                "form_data": {
                    "cycle_id": cycle_id,
                    "manager_id": manager_id,
                    "template_id": template_id,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/appraisal_form.html", context)


@router.post("/appraisals/{appraisal_id}/cancel")
def cancel_appraisal(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Cancel an appraisal."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.update_appraisal(
            org_id, coerce_uuid(appraisal_id), status=AppraisalStatus.CANCELLED
        )
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/perf/appraisals/{appraisal_id}", status_code=303)


# ===========================================================================
# Appraisal Workflow Routes
# ===========================================================================


@router.post("/appraisals/{appraisal_id}/start-self-assessment")
def start_self_assessment(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Start self-assessment phase (DRAFT -> SELF_ASSESSMENT)."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.update_appraisal(
            org_id, coerce_uuid(appraisal_id), status=AppraisalStatus.SELF_ASSESSMENT
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}?success=Self+assessment+phase+started",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}?error={str(e)}",
            status_code=303,
        )


@router.get("/appraisals/{appraisal_id}/self-assessment", response_class=HTMLResponse)
def self_assessment_form(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Self-assessment form page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
    except Exception:
        return RedirectResponse(url="/people/perf/appraisals", status_code=303)

    if appraisal.status not in {AppraisalStatus.DRAFT, AppraisalStatus.SELF_ASSESSMENT}:
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}?error=Appraisal+is+not+in+self-assessment+phase",
            status_code=303,
        )

    context = base_context(request, auth, "Self Assessment", "perf", db=db)
    context["request"] = request
    context["appraisal"] = appraisal
    context["form_data"] = {
        "self_overall_rating": appraisal.self_overall_rating or "",
        "self_summary": appraisal.self_summary or "",
        "achievements": appraisal.achievements or "",
        "challenges": appraisal.challenges or "",
        "development_needs": appraisal.development_needs or "",
    }
    return templates.TemplateResponse(request, "people/perf/self_assessment_form.html", context)


@router.post("/appraisals/{appraisal_id}/self-assessment", response_class=HTMLResponse)
def submit_self_assessment(
    request: Request,
    appraisal_id: str,
    self_overall_rating: int = Form(...),
    self_summary: Optional[str] = Form(None),
    achievements: Optional[str] = Form(None),
    challenges: Optional[str] = Form(None),
    development_needs: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit self-assessment."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.submit_self_assessment(
            org_id,
            coerce_uuid(appraisal_id),
            self_overall_rating=self_overall_rating,
            self_summary=self_summary or None,
            achievements=achievements or None,
            challenges=challenges or None,
            development_needs=development_needs or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}?success=Self+assessment+submitted+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))

        context = base_context(request, auth, "Self Assessment", "perf", db=db)
        context["request"] = request
        context["appraisal"] = appraisal
        context["error"] = str(e)
        context["form_data"] = {
            "self_overall_rating": self_overall_rating,
            "self_summary": self_summary or "",
            "achievements": achievements or "",
            "challenges": challenges or "",
            "development_needs": development_needs or "",
        }
        return templates.TemplateResponse(request, "people/perf/self_assessment_form.html", context)


@router.get("/appraisals/{appraisal_id}/manager-review", response_class=HTMLResponse)
def manager_review_form(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Manager review form page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
    except Exception:
        return RedirectResponse(url="/people/perf/appraisals", status_code=303)

    if appraisal.status != AppraisalStatus.UNDER_REVIEW:
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}?error=Appraisal+is+not+ready+for+manager+review",
            status_code=303,
        )

    context = base_context(request, auth, "Manager Review", "perf", db=db)
    context["request"] = request
    context["appraisal"] = appraisal
    context["form_data"] = {
        "manager_overall_rating": appraisal.manager_overall_rating or "",
        "manager_summary": appraisal.manager_summary or "",
        "manager_recommendations": appraisal.manager_recommendations or "",
    }
    return templates.TemplateResponse(request, "people/perf/manager_review_form.html", context)


@router.post("/appraisals/{appraisal_id}/manager-review", response_class=HTMLResponse)
def submit_manager_review(
    request: Request,
    appraisal_id: str,
    manager_overall_rating: int = Form(...),
    manager_summary: Optional[str] = Form(None),
    manager_recommendations: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit manager review."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.submit_manager_review(
            org_id,
            coerce_uuid(appraisal_id),
            manager_overall_rating=manager_overall_rating,
            manager_summary=manager_summary or None,
            manager_recommendations=manager_recommendations or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}?success=Manager+review+submitted+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))

        context = base_context(request, auth, "Manager Review", "perf", db=db)
        context["request"] = request
        context["appraisal"] = appraisal
        context["error"] = str(e)
        context["form_data"] = {
            "manager_overall_rating": manager_overall_rating,
            "manager_summary": manager_summary or "",
            "manager_recommendations": manager_recommendations or "",
        }
        return templates.TemplateResponse(request, "people/perf/manager_review_form.html", context)


@router.get("/appraisals/{appraisal_id}/calibration", response_class=HTMLResponse)
def calibration_form(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Calibration form page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
    except Exception:
        return RedirectResponse(url="/people/perf/appraisals", status_code=303)

    if appraisal.status != AppraisalStatus.CALIBRATION:
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}?error=Appraisal+is+not+ready+for+calibration",
            status_code=303,
        )

    # Rating labels for selection
    rating_labels = [
        {"value": 5, "label": "Exceptional"},
        {"value": 4, "label": "Exceeds Expectations"},
        {"value": 3, "label": "Meets Expectations"},
        {"value": 2, "label": "Needs Improvement"},
        {"value": 1, "label": "Unsatisfactory"},
    ]

    context = base_context(request, auth, "Calibration", "perf", db=db)
    context["request"] = request
    context["appraisal"] = appraisal
    context["rating_labels"] = rating_labels
    context["form_data"] = {
        "calibrated_rating": appraisal.calibrated_rating or appraisal.manager_overall_rating or "",
        "calibration_notes": appraisal.calibration_notes or "",
        "rating_label": appraisal.rating_label or "",
    }
    return templates.TemplateResponse(request, "people/perf/calibration_form.html", context)


@router.post("/appraisals/{appraisal_id}/calibration", response_class=HTMLResponse)
def submit_calibration(
    request: Request,
    appraisal_id: str,
    calibrated_rating: int = Form(...),
    calibration_notes: Optional[str] = Form(None),
    rating_label: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Submit calibration and complete appraisal."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.submit_calibration(
            org_id,
            coerce_uuid(appraisal_id),
            calibrated_rating=calibrated_rating,
            calibration_notes=calibration_notes or None,
            rating_label=rating_label or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}?success=Appraisal+completed+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))

        rating_labels = [
            {"value": 5, "label": "Exceptional"},
            {"value": 4, "label": "Exceeds Expectations"},
            {"value": 3, "label": "Meets Expectations"},
            {"value": 2, "label": "Needs Improvement"},
            {"value": 1, "label": "Unsatisfactory"},
        ]

        context = base_context(request, auth, "Calibration", "perf", db=db)
        context["request"] = request
        context["appraisal"] = appraisal
        context["error"] = str(e)
        context["rating_labels"] = rating_labels
        context["form_data"] = {
            "calibrated_rating": calibrated_rating,
            "calibration_notes": calibration_notes or "",
            "rating_label": rating_label or "",
        }
        return templates.TemplateResponse(request, "people/perf/calibration_form.html", context)


# ===========================================================================
# KPI/Goal CRUD Routes
# ===========================================================================


@router.get("/goals/new", response_class=HTMLResponse)
def new_kpi_form(
    request: Request,
    employee_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New KPI form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    employees = org_svc.list_employees(
        EmployeeFilters(is_active=True),
        PaginationParams(limit=500),
    ).items

    kras = svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items

    context = base_context(request, auth, "New KPI", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "employees": employees,
            "kras": kras,
            "form_data": {"employee_id": employee_id} if employee_id else {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/kpi_form.html", context)


@router.post("/goals/new", response_class=HTMLResponse)
def create_kpi(
    request: Request,
    kpi_name: str = Form(...),
    employee_id: str = Form(...),
    period_start: str = Form(...),
    period_end: str = Form(...),
    target_value: str = Form(...),
    kra_id: Optional[str] = Form(None),
    unit_of_measure: Optional[str] = Form(None),
    threshold_value: Optional[str] = Form(None),
    stretch_value: Optional[str] = Form(None),
    weightage: Optional[str] = Form("0"),
    description: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new KPI."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        kpi = svc.create_kpi(
            org_id,
            employee_id=coerce_uuid(employee_id),
            kpi_name=kpi_name,
            period_start=date.fromisoformat(period_start),
            period_end=date.fromisoformat(period_end),
            target_value=Decimal(target_value),
            kra_id=coerce_uuid(kra_id) if kra_id else None,
            unit_of_measure=unit_of_measure or None,
            threshold_value=Decimal(threshold_value) if threshold_value else None,
            stretch_value=Decimal(stretch_value) if stretch_value else None,
            weightage=Decimal(weightage) if weightage else Decimal("0"),
            description=description or None,
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(url=f"/people/perf/goals/{kpi.kpi_id}", status_code=303)
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)

        context = base_context(request, auth, "New KPI", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "employees": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
                "form_data": {
                    "kpi_name": kpi_name,
                    "employee_id": employee_id,
                    "period_start": period_start,
                    "period_end": period_end,
                    "target_value": target_value,
                    "kra_id": kra_id,
                    "unit_of_measure": unit_of_measure,
                    "threshold_value": threshold_value,
                    "stretch_value": stretch_value,
                    "weightage": weightage,
                    "description": description,
                    "notes": notes,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/kpi_form.html", context)


@router.get("/goals/{kpi_id}", response_class=HTMLResponse)
def kpi_detail(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """KPI detail page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        kpi = svc.get_kpi(org_id, coerce_uuid(kpi_id))
    except Exception:
        return RedirectResponse(url="/people/perf/goals", status_code=303)

    context = base_context(request, auth, "KPI Details", "perf", db=db)
    context["request"] = request
    context["kpi"] = kpi
    return templates.TemplateResponse(request, "people/perf/kpi_detail.html", context)


@router.get("/goals/{kpi_id}/edit", response_class=HTMLResponse)
def edit_kpi_form(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit KPI form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    try:
        kpi = svc.get_kpi(org_id, coerce_uuid(kpi_id))
    except Exception:
        return RedirectResponse(url="/people/perf/goals", status_code=303)

    if kpi.status in [KPIStatus.ACHIEVED, KPIStatus.CANCELLED]:
        return RedirectResponse(url=f"/people/perf/goals/{kpi_id}", status_code=303)

    context = base_context(request, auth, "Edit KPI", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "kpi": kpi,
            "employees": org_svc.list_employees(
                EmployeeFilters(is_active=True), PaginationParams(limit=500)
            ).items,
            "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
            "form_data": {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/kpi_form.html", context)


@router.post("/goals/{kpi_id}/edit", response_class=HTMLResponse)
def update_kpi(
    request: Request,
    kpi_id: str,
    kpi_name: str = Form(...),
    employee_id: str = Form(...),
    period_start: str = Form(...),
    period_end: str = Form(...),
    target_value: str = Form(...),
    kra_id: Optional[str] = Form(None),
    unit_of_measure: Optional[str] = Form(None),
    threshold_value: Optional[str] = Form(None),
    stretch_value: Optional[str] = Form(None),
    weightage: Optional[str] = Form("0"),
    description: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a KPI."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.update_kpi(
            org_id,
            coerce_uuid(kpi_id),
            kpi_name=kpi_name,
            period_start=date.fromisoformat(period_start),
            period_end=date.fromisoformat(period_end),
            target_value=Decimal(target_value),
            kra_id=coerce_uuid(kra_id) if kra_id else None,
            unit_of_measure=unit_of_measure or None,
            threshold_value=Decimal(threshold_value) if threshold_value else None,
            stretch_value=Decimal(stretch_value) if stretch_value else None,
            weightage=Decimal(weightage) if weightage else Decimal("0"),
            description=description or None,
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(url=f"/people/perf/goals/{kpi_id}", status_code=303)
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)
        kpi = svc.get_kpi(org_id, coerce_uuid(kpi_id))

        context = base_context(request, auth, "Edit KPI", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "kpi": kpi,
                "employees": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
                "form_data": {
                    "kpi_name": kpi_name,
                    "period_start": period_start,
                    "period_end": period_end,
                    "target_value": target_value,
                    "kra_id": kra_id,
                    "unit_of_measure": unit_of_measure,
                    "threshold_value": threshold_value,
                    "stretch_value": stretch_value,
                    "weightage": weightage,
                    "description": description,
                    "notes": notes,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/kpi_form.html", context)


@router.post("/goals/{kpi_id}/update-progress")
def update_kpi_progress(
    request: Request,
    kpi_id: str,
    actual_value: str = Form(...),
    evidence: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update KPI progress."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.update_kpi_progress(
            org_id,
            coerce_uuid(kpi_id),
            actual_value=Decimal(actual_value),
            evidence=evidence or None,
            notes=notes or None,
        )
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/perf/goals/{kpi_id}", status_code=303)


@router.post("/goals/{kpi_id}/delete")
def delete_kpi(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a KPI."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.delete_kpi(org_id, coerce_uuid(kpi_id))
        db.commit()
        return RedirectResponse(url="/people/perf/goals", status_code=303)
    except Exception:
        db.rollback()
        return RedirectResponse(url=f"/people/perf/goals/{kpi_id}", status_code=303)


# ===========================================================================
# Appraisal Cycle Management Routes
# ===========================================================================


@router.get("/cycles", response_class=HTMLResponse)
def list_cycles(
    request: Request,
    status: Optional[str] = None,
    year: Optional[int] = None,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appraisal cycles list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = PerformanceService(db)

    status_enum = None
    if status:
        try:
            status_enum = AppraisalCycleStatus(status)
        except ValueError:
            status_enum = None

    result = svc.list_cycles(
        org_id,
        status=status_enum,
        year=year,
        search=search,
        pagination=pagination,
    )

    context = base_context(request, auth, "Appraisal Cycles", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "cycles": result.items,
            "status": status,
            "year": year,
            "search": search,
            "statuses": [s.value for s in AppraisalCycleStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/perf/appraisal_cycles.html", context)


@router.get("/cycles/new", response_class=HTMLResponse)
def new_cycle_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New appraisal cycle form."""
    context = base_context(request, auth, "New Appraisal Cycle", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "form_data": {},
            "errors": {},
            "statuses": [s.value for s in AppraisalCycleStatus],
        }
    )
    return templates.TemplateResponse(request, "people/perf/appraisal_cycle_form.html", context)


@router.post("/cycles/new", response_class=HTMLResponse)
def create_cycle(
    request: Request,
    cycle_code: str = Form(...),
    cycle_name: str = Form(...),
    review_period_start: str = Form(...),
    review_period_end: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    self_assessment_deadline: Optional[str] = Form(None),
    manager_review_deadline: Optional[str] = Form(None),
    calibration_deadline: Optional[str] = Form(None),
    include_probation_employees: Optional[str] = Form(None),
    min_tenure_months: int = Form(3),
    description: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new appraisal cycle."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        cycle = svc.create_cycle(
            org_id,
            cycle_code=cycle_code,
            cycle_name=cycle_name,
            review_period_start=date.fromisoformat(review_period_start),
            review_period_end=date.fromisoformat(review_period_end),
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            self_assessment_deadline=_parse_date(self_assessment_deadline),
            manager_review_deadline=_parse_date(manager_review_deadline),
            calibration_deadline=_parse_date(calibration_deadline),
            include_probation_employees=include_probation_employees == "true",
            min_tenure_months=min_tenure_months,
            description=description or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/cycles/{cycle.cycle_id}?success=Cycle+created+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "New Appraisal Cycle", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "form_data": {
                    "cycle_code": cycle_code,
                    "cycle_name": cycle_name,
                    "review_period_start": review_period_start,
                    "review_period_end": review_period_end,
                    "start_date": start_date,
                    "end_date": end_date,
                    "self_assessment_deadline": self_assessment_deadline,
                    "manager_review_deadline": manager_review_deadline,
                    "calibration_deadline": calibration_deadline,
                    "include_probation_employees": include_probation_employees,
                    "min_tenure_months": min_tenure_months,
                    "description": description,
                },
                "error": str(e),
                "errors": {},
                "statuses": [s.value for s in AppraisalCycleStatus],
            }
        )
        return templates.TemplateResponse(request, "people/perf/appraisal_cycle_form.html", context)


@router.get("/cycles/{cycle_id}", response_class=HTMLResponse)
def cycle_detail(
    request: Request,
    cycle_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appraisal cycle detail page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        cycle = svc.get_cycle(org_id, coerce_uuid(cycle_id))
    except Exception:
        return RedirectResponse(url="/people/perf/cycles", status_code=303)

    # Get appraisals for this cycle
    appraisals = svc.list_appraisals(
        org_id,
        cycle_id=coerce_uuid(cycle_id),
        pagination=PaginationParams(limit=100),
    )

    # Get statistics for cycle
    stats = {
        "total_appraisals": appraisals.total,
        "draft": sum(1 for a in appraisals.items if a.status == AppraisalStatus.DRAFT),
        "self_assessment": sum(1 for a in appraisals.items if a.status == AppraisalStatus.SELF_ASSESSMENT),
        "under_review": sum(1 for a in appraisals.items if a.status == AppraisalStatus.UNDER_REVIEW),
        "calibration": sum(1 for a in appraisals.items if a.status == AppraisalStatus.CALIBRATION),
        "completed": sum(1 for a in appraisals.items if a.status == AppraisalStatus.COMPLETED),
        "cancelled": sum(1 for a in appraisals.items if a.status == AppraisalStatus.CANCELLED),
    }

    context = base_context(request, auth, cycle.cycle_name, "perf", db=db)
    context["request"] = request
    context.update(
        {
            "cycle": cycle,
            "appraisals": appraisals.items[:10],
            "stats": stats,
            "success": success,
            "error": error,
        }
    )
    return templates.TemplateResponse(request, "people/perf/appraisal_cycle_detail.html", context)


@router.get("/cycles/{cycle_id}/edit", response_class=HTMLResponse)
def edit_cycle_form(
    request: Request,
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit appraisal cycle form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        cycle = svc.get_cycle(org_id, coerce_uuid(cycle_id))
    except Exception:
        return RedirectResponse(url="/people/perf/cycles", status_code=303)

    context = base_context(request, auth, f"Edit {cycle.cycle_name}", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "cycle": cycle,
            "form_data": {},
            "errors": {},
            "statuses": [s.value for s in AppraisalCycleStatus],
        }
    )
    return templates.TemplateResponse(request, "people/perf/appraisal_cycle_form.html", context)


@router.post("/cycles/{cycle_id}/edit", response_class=HTMLResponse)
def update_cycle(
    request: Request,
    cycle_id: str,
    cycle_code: str = Form(...),
    cycle_name: str = Form(...),
    review_period_start: str = Form(...),
    review_period_end: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    self_assessment_deadline: Optional[str] = Form(None),
    manager_review_deadline: Optional[str] = Form(None),
    calibration_deadline: Optional[str] = Form(None),
    include_probation_employees: Optional[str] = Form(None),
    min_tenure_months: int = Form(3),
    description: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update an appraisal cycle."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.update_cycle(
            org_id,
            coerce_uuid(cycle_id),
            cycle_code=cycle_code,
            cycle_name=cycle_name,
            review_period_start=date.fromisoformat(review_period_start),
            review_period_end=date.fromisoformat(review_period_end),
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            self_assessment_deadline=_parse_date(self_assessment_deadline),
            manager_review_deadline=_parse_date(manager_review_deadline),
            calibration_deadline=_parse_date(calibration_deadline),
            include_probation_employees=include_probation_employees == "true",
            min_tenure_months=min_tenure_months,
            description=description or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/cycles/{cycle_id}?success=Cycle+updated+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        cycle = svc.get_cycle(org_id, coerce_uuid(cycle_id))

        context = base_context(request, auth, f"Edit {cycle.cycle_name}", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "cycle": cycle,
                "form_data": {
                    "cycle_code": cycle_code,
                    "cycle_name": cycle_name,
                    "review_period_start": review_period_start,
                    "review_period_end": review_period_end,
                    "start_date": start_date,
                    "end_date": end_date,
                    "self_assessment_deadline": self_assessment_deadline,
                    "manager_review_deadline": manager_review_deadline,
                    "calibration_deadline": calibration_deadline,
                    "include_probation_employees": include_probation_employees,
                    "min_tenure_months": min_tenure_months,
                    "description": description,
                },
                "error": str(e),
                "errors": {},
                "statuses": [s.value for s in AppraisalCycleStatus],
            }
        )
        return templates.TemplateResponse(request, "people/perf/appraisal_cycle_form.html", context)


@router.post("/cycles/{cycle_id}/activate")
def activate_cycle(
    request: Request,
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Activate an appraisal cycle (DRAFT -> ACTIVE)."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.start_cycle(org_id, coerce_uuid(cycle_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/cycles/{cycle_id}?success=Cycle+activated+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/perf/cycles/{cycle_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/cycles/{cycle_id}/advance")
def advance_cycle(
    request: Request,
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Advance cycle to next phase."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        cycle = svc.get_cycle(org_id, coerce_uuid(cycle_id))

        # Determine next status based on current
        next_status = None
        if cycle.status == AppraisalCycleStatus.ACTIVE:
            next_status = AppraisalCycleStatus.REVIEW
        elif cycle.status == AppraisalCycleStatus.REVIEW:
            next_status = AppraisalCycleStatus.CALIBRATION
        elif cycle.status == AppraisalCycleStatus.CALIBRATION:
            next_status = AppraisalCycleStatus.COMPLETED

        if next_status:
            svc.update_cycle(org_id, coerce_uuid(cycle_id), status=next_status)
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/cycles/{cycle_id}?success=Cycle+advanced+to+{next_status.value}",
                status_code=303,
            )
        else:
            return RedirectResponse(
                url=f"/people/perf/cycles/{cycle_id}?error=Cannot+advance+cycle+from+current+status",
                status_code=303,
            )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/perf/cycles/{cycle_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/cycles/{cycle_id}/cancel")
def cancel_cycle(
    request: Request,
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Cancel an appraisal cycle."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.update_cycle(org_id, coerce_uuid(cycle_id), status=AppraisalCycleStatus.CANCELLED)
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/cycles/{cycle_id}?success=Cycle+cancelled",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/perf/cycles/{cycle_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/cycles/{cycle_id}/delete")
def delete_cycle(
    request: Request,
    cycle_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete an appraisal cycle."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.delete_cycle(org_id, coerce_uuid(cycle_id))
        db.commit()
        return RedirectResponse(
            url="/people/perf/cycles?success=Cycle+deleted",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/perf/cycles/{cycle_id}?error={str(e)}",
            status_code=303,
        )


# ===========================================================================
# KRA (Key Result Areas) Management Routes
# ===========================================================================


@router.get("/kras", response_class=HTMLResponse)
def list_kras(
    request: Request,
    is_active: Optional[str] = None,
    category: Optional[str] = None,
    department_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """KRAs list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    active_filter = None
    if is_active == "true":
        active_filter = True
    elif is_active == "false":
        active_filter = False

    result = svc.list_kras(
        org_id,
        is_active=active_filter,
        category=category or None,
        department_id=_parse_uuid(department_id),
        search=search,
        pagination=pagination,
    )

    # Get departments for filter
    departments = org_svc.list_departments(org_id).items

    # Get unique categories from existing KRAs
    categories = ["PERFORMANCE", "BEHAVIOR", "SKILL", "LEARNING", "OTHER"]

    context = base_context(request, auth, "Key Result Areas", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "kras": result.items,
            "is_active": is_active,
            "category": category,
            "department_id": department_id,
            "search": search,
            "departments": departments,
            "categories": categories,
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/perf/kras.html", context)


@router.get("/kras/new", response_class=HTMLResponse)
def new_kra_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New KRA form."""
    org_id = coerce_uuid(auth.organization_id)
    org_svc = OrganizationService(db, org_id)

    context = base_context(request, auth, "New KRA", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "departments": org_svc.list_departments(org_id).items,
            "designations": org_svc.list_designations().items,
            "categories": ["PERFORMANCE", "BEHAVIOR", "SKILL", "LEARNING", "OTHER"],
            "form_data": {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/kra_form.html", context)


@router.post("/kras/new", response_class=HTMLResponse)
def create_kra(
    request: Request,
    kra_code: str = Form(...),
    kra_name: str = Form(...),
    department_id: Optional[str] = Form(None),
    designation_id: Optional[str] = Form(None),
    default_weightage: str = Form("0"),
    category: Optional[str] = Form(None),
    measurement_criteria: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new KRA."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        kra = svc.create_kra(
            org_id,
            kra_code=kra_code,
            kra_name=kra_name,
            department_id=_parse_uuid(department_id),
            designation_id=_parse_uuid(designation_id),
            default_weightage=Decimal(default_weightage) if default_weightage else Decimal("0"),
            category=category or None,
            measurement_criteria=measurement_criteria or None,
            description=description or None,
            is_active=is_active == "true",
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/kras/{kra.kra_id}?success=KRA+created+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)

        context = base_context(request, auth, "New KRA", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "departments": org_svc.list_departments(org_id).items,
                "designations": org_svc.list_designations().items,
                "categories": ["PERFORMANCE", "BEHAVIOR", "SKILL", "LEARNING", "OTHER"],
                "form_data": {
                    "kra_code": kra_code,
                    "kra_name": kra_name,
                    "department_id": department_id,
                    "designation_id": designation_id,
                    "default_weightage": default_weightage,
                    "category": category,
                    "measurement_criteria": measurement_criteria,
                    "description": description,
                    "is_active": is_active,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/kra_form.html", context)


@router.get("/kras/{kra_id}", response_class=HTMLResponse)
def kra_detail(
    request: Request,
    kra_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """KRA detail page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        kra = svc.get_kra(org_id, coerce_uuid(kra_id))
    except Exception:
        return RedirectResponse(url="/people/perf/kras", status_code=303)

    context = base_context(request, auth, kra.kra_name, "perf", db=db)
    context["request"] = request
    context.update(
        {
            "kra": kra,
            "success": success,
            "error": error,
        }
    )
    return templates.TemplateResponse(request, "people/perf/kra_detail.html", context)


@router.get("/kras/{kra_id}/edit", response_class=HTMLResponse)
def edit_kra_form(
    request: Request,
    kra_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit KRA form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    try:
        kra = svc.get_kra(org_id, coerce_uuid(kra_id))
    except Exception:
        return RedirectResponse(url="/people/perf/kras", status_code=303)

    context = base_context(request, auth, f"Edit {kra.kra_name}", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "kra": kra,
            "departments": org_svc.list_departments(org_id).items,
            "designations": org_svc.list_designations().items,
            "categories": ["PERFORMANCE", "BEHAVIOR", "SKILL", "LEARNING", "OTHER"],
            "form_data": {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/kra_form.html", context)


@router.post("/kras/{kra_id}/edit", response_class=HTMLResponse)
def update_kra(
    request: Request,
    kra_id: str,
    kra_code: str = Form(...),
    kra_name: str = Form(...),
    department_id: Optional[str] = Form(None),
    designation_id: Optional[str] = Form(None),
    default_weightage: str = Form("0"),
    category: Optional[str] = Form(None),
    measurement_criteria: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a KRA."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.update_kra(
            org_id,
            coerce_uuid(kra_id),
            kra_code=kra_code,
            kra_name=kra_name,
            department_id=_parse_uuid(department_id),
            designation_id=_parse_uuid(designation_id),
            default_weightage=Decimal(default_weightage) if default_weightage else Decimal("0"),
            category=category or None,
            measurement_criteria=measurement_criteria or None,
            description=description or None,
            is_active=is_active == "true",
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/kras/{kra_id}?success=KRA+updated+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)
        kra = svc.get_kra(org_id, coerce_uuid(kra_id))

        context = base_context(request, auth, f"Edit {kra.kra_name}", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "kra": kra,
                "departments": org_svc.list_departments(org_id).items,
                "designations": org_svc.list_designations().items,
                "categories": ["PERFORMANCE", "BEHAVIOR", "SKILL", "LEARNING", "OTHER"],
                "form_data": {
                    "kra_code": kra_code,
                    "kra_name": kra_name,
                    "department_id": department_id,
                    "designation_id": designation_id,
                    "default_weightage": default_weightage,
                    "category": category,
                    "measurement_criteria": measurement_criteria,
                    "description": description,
                    "is_active": is_active,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/kra_form.html", context)


@router.post("/kras/{kra_id}/toggle-active")
def toggle_kra_active(
    request: Request,
    kra_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Toggle KRA active status."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        kra = svc.get_kra(org_id, coerce_uuid(kra_id))
        svc.update_kra(org_id, coerce_uuid(kra_id), is_active=not kra.is_active)
        db.commit()
        status = "activated" if not kra.is_active else "deactivated"
        return RedirectResponse(
            url=f"/people/perf/kras/{kra_id}?success=KRA+{status}",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/perf/kras/{kra_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/kras/{kra_id}/delete")
def delete_kra(
    request: Request,
    kra_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a KRA."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.delete_kra(org_id, coerce_uuid(kra_id))
        db.commit()
        return RedirectResponse(
            url="/people/perf/kras?success=KRA+deleted",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/perf/kras/{kra_id}?error={str(e)}",
            status_code=303,
        )


# ===========================================================================
# Appraisal Template Management Routes
# ===========================================================================


@router.get("/templates", response_class=HTMLResponse)
def list_templates(
    request: Request,
    is_active: Optional[str] = None,
    department_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appraisal templates list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    active_filter = None
    if is_active == "true":
        active_filter = True
    elif is_active == "false":
        active_filter = False

    result = svc.list_templates(
        org_id,
        is_active=active_filter,
        department_id=_parse_uuid(department_id),
        search=search,
        pagination=pagination,
    )

    departments = org_svc.list_departments(org_id).items

    context = base_context(request, auth, "Appraisal Templates", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "templates": result.items,
            "is_active": is_active,
            "department_id": department_id,
            "search": search,
            "departments": departments,
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/perf/appraisal_templates.html", context)


@router.get("/templates/new", response_class=HTMLResponse)
def new_template_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New appraisal template form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    context = base_context(request, auth, "New Appraisal Template", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "departments": org_svc.list_departments(org_id).items,
            "designations": org_svc.list_designations().items,
            "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
            "form_data": {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/appraisal_template_form.html", context)


@router.post("/templates/new", response_class=HTMLResponse)
def create_template(
    request: Request,
    template_code: str = Form(...),
    template_name: str = Form(...),
    department_id: Optional[str] = Form(None),
    designation_id: Optional[str] = Form(None),
    rating_scale_max: int = Form(5),
    description: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    kra_ids: Optional[list[str]] = Form(None),
    kra_weightages: Optional[list[str]] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new appraisal template."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        # Build KRA list
        kras_list = None
        if kra_ids and kra_weightages:
            kras_list = []
            for i, kra_id in enumerate(kra_ids):
                if kra_id:
                    weightage = kra_weightages[i] if i < len(kra_weightages) else "0"
                    kras_list.append({
                        "kra_id": coerce_uuid(kra_id),
                        "weightage": Decimal(weightage) if weightage else Decimal("0"),
                        "sequence": i,
                    })

        template = svc.create_template(
            org_id,
            template_code=template_code,
            template_name=template_name,
            department_id=_parse_uuid(department_id),
            designation_id=_parse_uuid(designation_id),
            rating_scale_max=rating_scale_max,
            description=description or None,
            is_active=is_active == "true",
            kras=kras_list,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/templates/{template.template_id}?success=Template+created+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)

        context = base_context(request, auth, "New Appraisal Template", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "departments": org_svc.list_departments(org_id).items,
                "designations": org_svc.list_designations().items,
                "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
                "form_data": {
                    "template_code": template_code,
                    "template_name": template_name,
                    "department_id": department_id,
                    "designation_id": designation_id,
                    "rating_scale_max": rating_scale_max,
                    "description": description,
                    "is_active": is_active,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/appraisal_template_form.html", context)


@router.get("/templates/{template_id}", response_class=HTMLResponse)
def template_detail(
    request: Request,
    template_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appraisal template detail page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        template = svc.get_template(org_id, coerce_uuid(template_id))
    except Exception:
        return RedirectResponse(url="/people/perf/templates", status_code=303)

    context = base_context(request, auth, template.template_name, "perf", db=db)
    context["request"] = request
    context.update(
        {
            "template": template,
            "success": success,
            "error": error,
        }
    )
    return templates.TemplateResponse(request, "people/perf/appraisal_template_detail.html", context)


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def edit_template_form(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit appraisal template form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    try:
        template = svc.get_template(org_id, coerce_uuid(template_id))
    except Exception:
        return RedirectResponse(url="/people/perf/templates", status_code=303)

    context = base_context(request, auth, f"Edit {template.template_name}", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "template": template,
            "departments": org_svc.list_departments(org_id).items,
            "designations": org_svc.list_designations().items,
            "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
            "form_data": {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/appraisal_template_form.html", context)


@router.post("/templates/{template_id}/edit", response_class=HTMLResponse)
def update_template(
    request: Request,
    template_id: str,
    template_code: str = Form(...),
    template_name: str = Form(...),
    department_id: Optional[str] = Form(None),
    designation_id: Optional[str] = Form(None),
    rating_scale_max: int = Form(5),
    description: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    kra_ids: Optional[list[str]] = Form(None),
    kra_weightages: Optional[list[str]] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update an appraisal template."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        # Build KRA list
        kras_list = []
        if kra_ids and kra_weightages:
            for i, kra_id in enumerate(kra_ids):
                if kra_id:
                    weightage = kra_weightages[i] if i < len(kra_weightages) else "0"
                    kras_list.append({
                        "kra_id": coerce_uuid(kra_id),
                        "weightage": Decimal(weightage) if weightage else Decimal("0"),
                        "sequence": i,
                    })

        svc.update_template(
            org_id,
            coerce_uuid(template_id),
            template_code=template_code,
            template_name=template_name,
            department_id=_parse_uuid(department_id),
            designation_id=_parse_uuid(designation_id),
            rating_scale_max=rating_scale_max,
            description=description or None,
            is_active=is_active == "true",
            kras=kras_list,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/templates/{template_id}?success=Template+updated+successfully",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)
        template = svc.get_template(org_id, coerce_uuid(template_id))

        context = base_context(request, auth, f"Edit {template.template_name}", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "template": template,
                "departments": org_svc.list_departments(org_id).items,
                "designations": org_svc.list_designations().items,
                "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
                "form_data": {
                    "template_code": template_code,
                    "template_name": template_name,
                    "department_id": department_id,
                    "designation_id": designation_id,
                    "rating_scale_max": rating_scale_max,
                    "description": description,
                    "is_active": is_active,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/appraisal_template_form.html", context)


@router.post("/templates/{template_id}/toggle-active")
def toggle_template_active(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Toggle template active status."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        template = svc.get_template(org_id, coerce_uuid(template_id))
        svc.update_template(org_id, coerce_uuid(template_id), is_active=not template.is_active)
        db.commit()
        status = "activated" if not template.is_active else "deactivated"
        return RedirectResponse(
            url=f"/people/perf/templates/{template_id}?success=Template+{status}",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/perf/templates/{template_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/templates/{template_id}/delete")
def delete_template(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete an appraisal template."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.delete_template(org_id, coerce_uuid(template_id))
        db.commit()
        return RedirectResponse(
            url="/people/perf/templates?success=Template+deleted",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/perf/templates/{template_id}?error={str(e)}",
            status_code=303,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scorecard Management
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/scorecards", response_class=HTMLResponse)
def list_scorecards(
    request: Request,
    employee_id: Optional[str] = None,
    department_id: Optional[str] = None,
    is_finalized: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List scorecards."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    finalized_filter = None
    if is_finalized == "true":
        finalized_filter = True
    elif is_finalized == "false":
        finalized_filter = False

    result = svc.list_scorecards(
        org_id,
        employee_id=_parse_uuid(employee_id),
        department_id=_parse_uuid(department_id),
        is_finalized=finalized_filter,
        pagination=pagination,
    )

    employees = org_svc.list_employees(
        EmployeeFilters(is_active=True),
        PaginationParams(limit=500),
    ).items
    departments = org_svc.list_departments(org_id).items

    context = base_context(request, auth, "Scorecards", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "scorecards": result.items,
            "employees": employees,
            "departments": departments,
            "employee_id": employee_id,
            "department_id": department_id,
            "is_finalized": is_finalized,
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/perf/scorecards.html", context)


@router.get("/scorecards/new", response_class=HTMLResponse)
def new_scorecard_form(
    request: Request,
    employee_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New scorecard form."""
    org_id = coerce_uuid(auth.organization_id)
    org_svc = OrganizationService(db, org_id)

    employees = org_svc.list_employees(
        EmployeeFilters(is_active=True),
        PaginationParams(limit=500),
    ).items

    perspectives = ["FINANCIAL", "CUSTOMER", "PROCESS", "LEARNING"]

    context = base_context(request, auth, "New Scorecard", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "employees": employees,
            "perspectives": perspectives,
            "form_data": {"employee_id": employee_id} if employee_id else {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/scorecard_form.html", context)


@router.post("/scorecards/new", response_class=HTMLResponse)
def create_scorecard(
    request: Request,
    employee_id: str = Form(...),
    period_start: str = Form(...),
    period_end: str = Form(...),
    period_label: Optional[str] = Form(None),
    items_json: str = Form("[]"),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new scorecard."""
    import json

    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    try:
        items = json.loads(items_json) if items_json else []

        scorecard = svc.create_scorecard(
            org_id,
            employee_id=coerce_uuid(employee_id),
            period_start=date.fromisoformat(period_start),
            period_end=date.fromisoformat(period_end),
            period_label=period_label or None,
            items=items,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/scorecards/{scorecard.scorecard_id}?success=Scorecard+created",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        employees = org_svc.list_employees(
            EmployeeFilters(is_active=True),
            PaginationParams(limit=500),
        ).items
        perspectives = ["FINANCIAL", "CUSTOMER", "PROCESS", "LEARNING"]

        context = base_context(request, auth, "New Scorecard", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "employees": employees,
                "perspectives": perspectives,
                "form_data": {
                    "employee_id": employee_id,
                    "period_start": period_start,
                    "period_end": period_end,
                    "period_label": period_label,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/scorecard_form.html", context)


@router.get("/scorecards/{scorecard_id}", response_class=HTMLResponse)
def scorecard_detail(
    request: Request,
    scorecard_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Scorecard detail page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        scorecard = svc.get_scorecard(org_id, coerce_uuid(scorecard_id))
    except Exception:
        return RedirectResponse(url="/people/perf/scorecards", status_code=303)

    # Group items by perspective
    perspectives = {
        "FINANCIAL": [],
        "CUSTOMER": [],
        "PROCESS": [],
        "LEARNING": [],
    }
    for item in scorecard.items:
        if item.perspective in perspectives:
            perspectives[item.perspective].append(item)

    # Sort each perspective by sequence
    for key in perspectives:
        perspectives[key].sort(key=lambda x: x.sequence)

    context = base_context(request, auth, "Scorecard Details", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "scorecard": scorecard,
            "perspectives": perspectives,
            "success": success,
            "error": error,
        }
    )
    return templates.TemplateResponse(request, "people/perf/scorecard_detail.html", context)


@router.get("/scorecards/{scorecard_id}/update-item/{item_id}", response_class=HTMLResponse)
def update_item_form(
    request: Request,
    scorecard_id: str,
    item_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update scorecard item form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        scorecard = svc.get_scorecard(org_id, coerce_uuid(scorecard_id))
    except Exception:
        return RedirectResponse(url="/people/perf/scorecards", status_code=303)

    if scorecard.is_finalized:
        return RedirectResponse(
            url=f"/people/perf/scorecards/{scorecard_id}?error=Cannot+update+finalized+scorecard",
            status_code=303,
        )

    # Find the item
    item = None
    for sc_item in scorecard.items:
        if str(sc_item.item_id) == item_id:
            item = sc_item
            break

    if not item:
        return RedirectResponse(
            url=f"/people/perf/scorecards/{scorecard_id}?error=Item+not+found",
            status_code=303,
        )

    context = base_context(request, auth, "Update Metric", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "scorecard": scorecard,
            "item": item,
            "form_data": {"actual_value": str(item.actual_value) if item.actual_value else ""},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/scorecard_item_form.html", context)


@router.post("/scorecards/{scorecard_id}/update-item/{item_id}", response_class=HTMLResponse)
def update_item(
    request: Request,
    scorecard_id: str,
    item_id: str,
    actual_value: str = Form(...),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a scorecard item's actual value."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.update_scorecard_item(
            org_id,
            coerce_uuid(scorecard_id),
            coerce_uuid(item_id),
            actual_value=Decimal(actual_value),
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/scorecards/{scorecard_id}?success=Metric+updated",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/perf/scorecards/{scorecard_id}?error={str(e)}",
            status_code=303,
        )


@router.get("/scorecards/{scorecard_id}/finalize", response_class=HTMLResponse)
def finalize_scorecard_form(
    request: Request,
    scorecard_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Finalize scorecard form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        scorecard = svc.get_scorecard(org_id, coerce_uuid(scorecard_id))
    except Exception:
        return RedirectResponse(url="/people/perf/scorecards", status_code=303)

    if scorecard.is_finalized:
        return RedirectResponse(
            url=f"/people/perf/scorecards/{scorecard_id}?error=Scorecard+is+already+finalized",
            status_code=303,
        )

    # Calculate preliminary scores for preview
    perspectives = {"FINANCIAL": [], "CUSTOMER": [], "PROCESS": [], "LEARNING": []}
    total_weighted = Decimal("0")
    for item in scorecard.items:
        if item.perspective in perspectives and item.weighted_score:
            perspectives[item.perspective].append(item.weighted_score)
            total_weighted += item.weighted_score

    preview = {
        "financial_score": sum(perspectives["FINANCIAL"]) if perspectives["FINANCIAL"] else None,
        "customer_score": sum(perspectives["CUSTOMER"]) if perspectives["CUSTOMER"] else None,
        "process_score": sum(perspectives["PROCESS"]) if perspectives["PROCESS"] else None,
        "learning_score": sum(perspectives["LEARNING"]) if perspectives["LEARNING"] else None,
        "overall_score": total_weighted,
    }

    context = base_context(request, auth, "Finalize Scorecard", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "scorecard": scorecard,
            "preview": preview,
            "form_data": {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/scorecard_finalize.html", context)


@router.post("/scorecards/{scorecard_id}/finalize", response_class=HTMLResponse)
def finalize_scorecard(
    request: Request,
    scorecard_id: str,
    summary: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Finalize a scorecard."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.finalize_scorecard(
            org_id,
            coerce_uuid(scorecard_id),
            summary=summary or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/scorecards/{scorecard_id}?success=Scorecard+finalized",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/perf/scorecards/{scorecard_id}?error={str(e)}",
            status_code=303,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Performance Reports
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/reports/ratings", response_class=HTMLResponse)
def report_ratings(
    request: Request,
    cycle_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Performance ratings distribution report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    report = svc.get_ratings_distribution_report(
        org_id,
        cycle_id=_parse_uuid(cycle_id),
    )

    context = base_context(request, auth, "Ratings Report", "perf", db=db)
    context.update({
        "report": report,
        "cycle_id": cycle_id or "",
    })

    return templates.TemplateResponse(
        request,
        "people/perf/reports/ratings.html",
        context,
    )


@router.get("/reports/by-department", response_class=HTMLResponse)
def report_by_department(
    request: Request,
    cycle_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Performance by department report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    report = svc.get_performance_by_department_report(
        org_id,
        cycle_id=_parse_uuid(cycle_id),
    )

    # Get cycles for filter
    cycles = svc.list_cycles(org_id).items

    context = base_context(request, auth, "Performance by Department", "perf", db=db)
    context.update({
        "report": report,
        "cycles": cycles,
        "cycle_id": cycle_id or "",
    })

    return templates.TemplateResponse(
        request,
        "people/perf/reports/by_department.html",
        context,
    )


@router.get("/reports/kpi-achievement", response_class=HTMLResponse)
def report_kpi_achievement(
    request: Request,
    department_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """KPI achievement rates report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db)

    report = svc.get_kpi_achievement_report(
        org_id,
        department_id=_parse_uuid(department_id),
    )

    # Get departments for filter
    departments = org_svc.list_departments(org_id).items

    context = base_context(request, auth, "KPI Achievement", "perf", db=db)
    context.update({
        "report": report,
        "departments": departments,
        "department_id": department_id or "",
    })

    return templates.TemplateResponse(
        request,
        "people/perf/reports/kpi_achievement.html",
        context,
    )


@router.get("/reports/trends", response_class=HTMLResponse)
def report_trends(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Performance trends across cycles report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    report = svc.get_performance_trends_report(org_id)

    context = base_context(request, auth, "Performance Trends", "perf", db=db)
    context.update({
        "report": report,
    })

    return templates.TemplateResponse(
        request,
        "people/perf/reports/trends.html",
        context,
    )
