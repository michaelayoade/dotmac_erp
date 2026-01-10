"""
IFRS Dashboard Web Routes.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.services.ifrs.dashboard_web import dashboard_web_service

templates = Jinja2Templates(directory="templates")

router = APIRouter()


@router.get("/", include_in_schema=False)
def ifrs_root_redirect():
    """Redirect IFRS root to dashboard."""
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/dashboard", response_class=HTMLResponse)
def ifrs_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """IFRS Dashboard page."""
    context = base_context(request, auth, "IFRS Dashboard", "dashboard")
    context.update(
        dashboard_web_service.dashboard_context(db, auth.organization_id)
    )
    return templates.TemplateResponse(request, "ifrs/dashboard.html", context)
