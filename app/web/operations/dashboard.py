"""
Operations Dashboard Web Routes.

Dashboard page for the Operations module.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.operations.dashboard_web import operations_dashboard_web_service
from app.templates import templates
from app.web.deps import base_context, get_db, require_operations_access, WebAuthContext

router = APIRouter(tags=["operations-dashboard-web"])


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
def operations_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Operations module dashboard page."""
    context = base_context(request, auth, "Operations Dashboard", "dashboard", db=db)

    context.update(
        operations_dashboard_web_service.dashboard_context(db, auth.organization_id)
    )

    return templates.TemplateResponse(request, "operations/dashboard.html", context)
