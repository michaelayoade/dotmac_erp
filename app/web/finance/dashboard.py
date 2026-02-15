"""
Finance Dashboard Web Routes.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.finance.dashboard_web import dashboard_web_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_finance_access

router = APIRouter()


@router.get("/", include_in_schema=False)
def ifrs_root_redirect():
    """Redirect IFRS root to dashboard."""
    return RedirectResponse(url="/finance/dashboard", status_code=302)


@router.get("/ifrs", include_in_schema=False)
def ifrs_alias_redirect():
    """Redirect legacy /ifrs path to dashboard."""
    return RedirectResponse(url="/finance/dashboard", status_code=302)


@router.get("/ifrs/", include_in_schema=False)
def ifrs_alias_slash_redirect():
    """Redirect legacy /ifrs/ path to dashboard."""
    return RedirectResponse(url="/finance/dashboard", status_code=302)


@router.get("/dashboard", response_class=HTMLResponse)
def ifrs_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
    year: str | None = Query(default=None),
):
    """IFRS Dashboard page."""
    context = base_context(request, auth, "Finance Dashboard", "dashboard", db=db)
    context.update(
        dashboard_web_service.dashboard_context(db, auth.organization_id, year=year)
    )

    # Coach insight cards for finance dashboards
    try:
        from app.services.coach.coach_service import CoachService

        coach_svc = CoachService(db)
        if coach_svc.is_enabled():
            context["coach_insights"] = coach_svc.top_insights_for_module(
                auth.organization_id,
                [
                    "CASH_FLOW",
                    "AR_OVERDUE",
                    "AP_DUE",
                    "COMPLIANCE",
                    "EFFICIENCY",
                    "REVENUE",
                ],
            )
        else:
            context["coach_insights"] = []
    except Exception:
        context["coach_insights"] = []

    return templates.TemplateResponse(request, "finance/dashboard.html", context)
