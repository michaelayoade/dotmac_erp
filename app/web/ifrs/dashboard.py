"""
IFRS Dashboard Web Routes.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.ifrs.dashboard_web import dashboard_web_service
from app.templates import templates
from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context


router = APIRouter()


@router.get("/", include_in_schema=False)
def ifrs_root_redirect():
    """Redirect IFRS root to dashboard."""
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/ifrs", include_in_schema=False)
def ifrs_alias_redirect():
    """Redirect legacy /ifrs path to dashboard."""
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/ifrs/", include_in_schema=False)
def ifrs_alias_slash_redirect():
    """Redirect legacy /ifrs/ path to dashboard."""
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/dashboard", response_class=HTMLResponse)
def ifrs_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
    year: str | None = Query(default=None),
):
    """IFRS Dashboard page."""
    context = base_context(request, auth, "IFRS Dashboard", "dashboard")
    context.update(
        dashboard_web_service.dashboard_context(db, auth.organization_id, year=year)
    )
    return templates.TemplateResponse(request, "ifrs/dashboard.html", context)
