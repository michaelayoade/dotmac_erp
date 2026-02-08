"""
People Dashboard Web Routes.

Dashboard page for the People/HR module.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.people.hr.web import people_dashboard_service
from app.web.deps import WebAuthContext, get_db, require_hr_access

router = APIRouter(tags=["people-dashboard-web"])


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
def people_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """People module dashboard page."""
    return people_dashboard_service.dashboard_response(request, auth, db)
