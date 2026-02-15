"""
Coach Web Routes.

Phase 1 UI: insights list page + feedback submission.
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


@router.get("/insights", response_class=HTMLResponse)
def insights_list(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("coach:insights:read")),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    include_expired: bool = Query(False),
):
    context = base_context(request, auth, "Coach Insights", "coach", db=db)
    svc = CoachService(db)

    if not svc.is_enabled():
        context.update({"items": [], "total": 0, "page": page, "per_page": per_page})
        return templates.TemplateResponse(request, "coach/insights.html", context)

    scope = svc.build_scope_for_user(
        auth.organization_id,
        auth.person_id,
        auth.employee_id,
        set(auth.roles),
    )
    items, total = svc.list_insights(
        auth.organization_id,
        scope,
        page=page,
        per_page=per_page,
        include_expired=include_expired,
    )
    context.update(
        {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "include_expired": include_expired,
        }
    )
    return templates.TemplateResponse(request, "coach/insights.html", context)


@router.get(
    "/reports",
    response_class=HTMLResponse,
    dependencies=[Depends(require_web_permission("coach:reports:read"))],
)
def reports_placeholder(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("coach:insights:read")),
    db: Session = Depends(get_db),
):
    # Phase 1: UI placeholder. We store CoachReport in DB, but don't expose it yet.
    context = base_context(request, auth, "Coach Reports", "coach", db=db)
    return templates.TemplateResponse(request, "coach/report.html", context)


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
        db.commit()
    return RedirectResponse(url="/coach/insights", status_code=303)
