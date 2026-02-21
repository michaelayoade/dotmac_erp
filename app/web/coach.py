"""
Coach Web Routes.

Dashboard, insights list, reports, and feedback.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.coach.coach_service import CoachService
from app.services.common import coerce_uuid
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_web_permission

router = APIRouter(prefix="/coach", tags=["coach-web"])


def _build_scope(svc: CoachService, auth: WebAuthContext):
    """Build insight visibility scope from the current user."""
    return svc.build_scope_for_user(
        auth.organization_id,
        auth.person_id,
        auth.employee_id,
        set(auth.roles),
    )


# ── Dashboard ─────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("coach:insights:read")),
    db: Session = Depends(get_db),
):
    context = base_context(request, auth, "Coach Dashboard", "coach", db=db)
    svc = CoachService(db)

    if not svc.is_enabled():
        context.update(svc._empty_dashboard())
        return templates.TemplateResponse(request, "coach/dashboard.html", context)

    scope = _build_scope(svc, auth)
    context.update(svc.dashboard_context(auth.organization_id, scope))
    return templates.TemplateResponse(request, "coach/dashboard.html", context)


# ── Insights ──────────────────────────────────────────────────────────


@router.get("/insights", response_class=HTMLResponse)
def insights_list(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("coach:insights:read")),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    include_expired: bool = Query(False),
    category: str = Query(""),
):
    context = base_context(request, auth, "Coach Insights", "coach", db=db)
    svc = CoachService(db)

    if not svc.is_enabled():
        context.update({"items": [], "total": 0, "page": page, "per_page": per_page})
        return templates.TemplateResponse(request, "coach/insights.html", context)

    scope = _build_scope(svc, auth)
    items, total = svc.list_insights(
        auth.organization_id,
        scope,
        page=page,
        per_page=per_page,
        include_expired=include_expired,
        category=category,
    )

    context.update(
        {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "include_expired": include_expired,
            "category": category,
        }
    )
    return templates.TemplateResponse(request, "coach/insights.html", context)


# ── Reports ───────────────────────────────────────────────────────────


@router.get("/reports", response_class=HTMLResponse)
def reports_list(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("coach:reports:read")),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
):
    context = base_context(request, auth, "Coach Reports", "coach", db=db)
    svc = CoachService(db)

    if not svc.is_enabled():
        context.update({"reports": [], "total": 0, "page": page})
        return templates.TemplateResponse(request, "coach/reports.html", context)

    reports, total = svc.list_reports(auth.organization_id, page=page)
    context.update({"reports": reports, "total": total, "page": page})
    return templates.TemplateResponse(request, "coach/reports.html", context)


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report_detail(
    request: Request,
    report_id: str,
    auth: WebAuthContext = Depends(require_web_permission("coach:reports:read")),
    db: Session = Depends(get_db),
):
    context = base_context(request, auth, "Coach Report", "coach", db=db)
    svc = CoachService(db)
    report = svc.get_report(auth.organization_id, coerce_uuid(report_id))
    if not report:
        context.update({"report": None})
    else:
        context.update({"report": report})
    return templates.TemplateResponse(request, "coach/report_detail.html", context)


# ── Feedback ──────────────────────────────────────────────────────────


@router.post(
    "/insights/{insight_id}/feedback",
    response_class=HTMLResponse,
    dependencies=[Depends(require_web_permission("coach:insights:feedback"))],
)
def insight_feedback(
    request: Request,
    insight_id: str,
    feedback: str = Form(...),
    auth: WebAuthContext = Depends(require_web_permission("coach:insights:read")),
    db: Session = Depends(get_db),
):
    svc = CoachService(db)
    if svc.is_enabled():
        svc.update_feedback(
            organization_id=auth.organization_id,
            insight_id=coerce_uuid(insight_id),
            feedback=feedback,
        )
    return RedirectResponse(url="/coach/insights", status_code=303)
