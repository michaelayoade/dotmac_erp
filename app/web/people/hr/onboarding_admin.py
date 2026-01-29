"""HR Onboarding Admin Web Routes.

Provides HR admin interface for managing:
- Checklist templates (CRUD)
- Active employee onboardings
- Onboarding dashboard and metrics
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.people.hr.web.onboarding_admin_web import onboarding_admin_web_service
from app.web.deps import get_db, require_hr_access, WebAuthContext


router = APIRouter(prefix="/onboarding", tags=["onboarding-admin"])


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
def onboarding_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Onboarding dashboard with metrics and overview."""
    return onboarding_admin_web_service.dashboard_response(
        request=request, auth=auth, db=db
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Template Management
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/templates", response_class=HTMLResponse)
def templates_list(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List all checklist templates."""
    return onboarding_admin_web_service.templates_list_response(
        request=request, auth=auth, db=db
    )


@router.get("/templates/new", response_class=HTMLResponse)
def new_template_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Form to create new checklist template."""
    return onboarding_admin_web_service.template_form_response(
        request=request, auth=auth, db=db
    )


@router.post("/templates/new")
async def create_template(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create new checklist template."""
    return await onboarding_admin_web_service.save_template_response(
        request=request, auth=auth, db=db
    )


@router.get("/templates/{template_id}", response_class=HTMLResponse)
def template_detail(
    request: Request,
    template_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View template with items."""
    return onboarding_admin_web_service.template_detail_response(
        request=request, auth=auth, db=db, template_id=template_id
    )


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def edit_template_form(
    request: Request,
    template_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Form to edit checklist template."""
    return onboarding_admin_web_service.template_form_response(
        request=request, auth=auth, db=db, template_id=template_id
    )


@router.post("/templates/{template_id}/edit")
async def update_template(
    request: Request,
    template_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update checklist template."""
    return await onboarding_admin_web_service.save_template_response(
        request=request, auth=auth, db=db, template_id=template_id
    )


@router.post("/templates/{template_id}/items")
async def add_template_item(
    request: Request,
    template_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Add item to template."""
    return await onboarding_admin_web_service.add_template_item_response(
        request=request, auth=auth, db=db, template_id=template_id
    )


@router.post("/templates/{template_id}/items/{item_id}/delete")
async def delete_template_item(
    request: Request,
    template_id: UUID,
    item_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete item from template."""
    return await onboarding_admin_web_service.delete_template_item_response(
        request=request, auth=auth, db=db, template_id=template_id, item_id=item_id
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Employee Onboardings
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/employees", response_class=HTMLResponse)
def employees_list(
    request: Request,
    status: Optional[str] = Query(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List all employee onboardings."""
    return onboarding_admin_web_service.employees_list_response(
        request=request, auth=auth, db=db, status_filter=status
    )


@router.get("/employees/{onboarding_id}", response_class=HTMLResponse)
def employee_detail(
    request: Request,
    onboarding_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View employee onboarding detail."""
    return onboarding_admin_web_service.employee_detail_response(
        request=request, auth=auth, db=db, onboarding_id=onboarding_id
    )


@router.post("/employees/{onboarding_id}/activities/{activity_id}/toggle")
async def toggle_activity(
    request: Request,
    onboarding_id: UUID,
    activity_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Toggle activity completion status."""
    return await onboarding_admin_web_service.toggle_activity_response(
        request=request,
        auth=auth,
        db=db,
        onboarding_id=onboarding_id,
        activity_id=activity_id,
    )
