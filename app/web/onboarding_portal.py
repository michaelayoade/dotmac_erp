"""
Onboarding Self-Service Portal Web Routes.

Public routes for new employees to complete their onboarding tasks.
Authentication is token-based (no login required).
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.services.people.hr.errors import (
    ActivityNotFoundError,
    InvalidSelfServiceTokenError,
    OnboardingNotFoundError,
    ValidationError,
)
from app.services.people.hr.onboarding import OnboardingService
from app.templates import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding-portal"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_service(db: Session) -> OnboardingService:
    return OnboardingService(db)


def _require_valid_token(token: str, db: Session) -> tuple:
    """Validate token and get onboarding context."""
    service = _get_service(db)
    try:
        onboarding = service.get_onboarding_by_token(token)
        return onboarding, service
    except InvalidSelfServiceTokenError:
        raise HTTPException(
            status_code=404,
            detail="Invalid or expired onboarding link. Please contact HR for assistance.",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Portal Landing & Dashboard
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/start/{token}", response_class=HTMLResponse)
def onboarding_portal_landing(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    """
    Onboarding portal landing page.

    New employees access this URL from their welcome email.
    Shows overview of onboarding tasks and progress.
    """
    onboarding, service = _require_valid_token(token, db)

    # Get employee info
    employee = onboarding.employee if hasattr(onboarding, "employee") else None
    employee_name = "New Team Member"
    if employee:
        employee_name = f"{employee.first_name} {employee.last_name}" if hasattr(employee, "first_name") else str(employee)

    # Get activities grouped by category
    activities = sorted(onboarding.activities, key=lambda a: (a.sequence or 0, a.due_date or "9999-12-31"))
    self_service_activities = [a for a in activities if a.assigned_to_employee]
    other_activities = [a for a in activities if not a.assigned_to_employee]

    # Group self-service activities by category
    categories = {}
    for activity in self_service_activities:
        cat = activity.category or "GENERAL"
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(activity)

    # Progress calculation
    total = len(onboarding.activities) if onboarding.activities else 0
    completed = sum(
        1 for a in onboarding.activities
        if a.activity_status in ("COMPLETED", "SKIPPED")
        or (hasattr(a, "status") and a.status in ("completed", "skipped"))
    ) if onboarding.activities else 0
    percentage = int((completed / total) * 100) if total > 0 else 0
    progress = {
        "percentage": percentage,
        "completed": completed,
        "total": total,
    }

    return templates.TemplateResponse(
        "onboarding/portal/dashboard.html",
        {
            "request": request,
            "token": token,
            "onboarding": onboarding,
            "employee_name": employee_name,
            "progress": progress,
            "self_service_activities": self_service_activities,
            "other_activities": other_activities,
            "categories": categories,
            "category_labels": {
                "PRE_BOARDING": "Before Your First Day",
                "DAY_ONE": "First Day Tasks",
                "FIRST_WEEK": "First Week",
                "FIRST_MONTH": "First Month",
                "ONGOING": "Ongoing",
                "GENERAL": "General Tasks",
            },
            "brand_name": settings.brand_name,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# Activity Detail & Completion
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/start/{token}/task/{activity_id}", response_class=HTMLResponse)
def onboarding_task_detail(
    request: Request,
    token: str,
    activity_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """
    View details of a specific onboarding task.

    Shows task instructions and allows completion/document upload.
    """
    onboarding, service = _require_valid_token(token, db)

    # Find the activity
    activity = next((a for a in onboarding.activities if a.activity_id == activity_id), None)
    if not activity:
        raise HTTPException(status_code=404, detail="Task not found")

    # Verify this is a self-service task
    if not activity.assigned_to_employee:
        raise HTTPException(
            status_code=403,
            detail="This task is not assigned to you. Please contact HR.",
        )

    # Get template item for instructions
    instructions = None
    if activity.template_item:
        instructions = activity.template_item.instructions

    return templates.TemplateResponse(
        "onboarding/portal/task_detail.html",
        {
            "request": request,
            "token": token,
            "onboarding": onboarding,
            "activity": activity,
            "instructions": instructions,
            "brand_name": settings.brand_name,
            "csrf_token": getattr(request.state, "csrf_token", ""),
        },
    )


@router.post("/start/{token}/task/{activity_id}/complete")
def complete_onboarding_task(
    request: Request,
    token: str,
    activity_id: uuid.UUID,
    notes: Optional[str] = Form(None),
    document: Optional[UploadFile] = None,
    db: Session = Depends(get_db),
):
    """
    Complete an onboarding task.

    For tasks requiring documents, a file upload is required.
    """
    onboarding, service = _require_valid_token(token, db)

    # Find and validate the activity
    activity = next((a for a in onboarding.activities if a.activity_id == activity_id), None)
    if not activity:
        raise HTTPException(status_code=404, detail="Task not found")

    if not activity.assigned_to_employee:
        raise HTTPException(status_code=403, detail="This task is not assigned to you")

    if activity.activity_status in ("COMPLETED", "SKIPPED"):
        # Already complete, redirect back
        return RedirectResponse(
            url=f"/onboarding/start/{token}",
            status_code=303,
        )

    # Handle document upload if required
    document_id = None
    if activity.requires_document:
        if not document or not document.filename:
            # Show error on the task page
            return templates.TemplateResponse(
                "onboarding/portal/task_detail.html",
                {
                    "request": request,
                    "token": token,
                    "onboarding": onboarding,
                    "activity": activity,
                    "instructions": activity.template_item.instructions if activity.template_item else None,
                    "error": "Please upload the required document to complete this task.",
                    "brand_name": settings.brand_name,
                    "csrf_token": getattr(request.state, "csrf_token", ""),
                },
                status_code=400,
            )

        # TODO: Save document using attachment service
        # For now, we'll skip the document_id requirement
        # document_id = attachment_service.save(...)
        logger.info("Document uploaded for activity %s: %s", activity_id, document.filename)

    try:
        # Complete the activity
        # Use the employee's person_id as completed_by (get from onboarding.employee)
        completed_by = onboarding.employee.person_id if onboarding.employee else onboarding.employee_id

        service.complete_activity(
            org_id=onboarding.organization_id,
            activity_id=activity_id,
            completed_by=completed_by,
            completion_notes=notes,
            document_id=document_id,
        )
        db.commit()

        logger.info("Employee completed onboarding task %s", activity_id)

        return RedirectResponse(
            url=f"/onboarding/start/{token}?completed={activity_id}",
            status_code=303,
        )

    except ValidationError as e:
        return templates.TemplateResponse(
            "onboarding/portal/task_detail.html",
            {
                "request": request,
                "token": token,
                "onboarding": onboarding,
                "activity": activity,
                "instructions": activity.template_item.instructions if activity.template_item else None,
                "error": str(e),
                "brand_name": settings.brand_name,
                "csrf_token": getattr(request.state, "csrf_token", ""),
            },
            status_code=400,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Company Information Pages
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/start/{token}/info", response_class=HTMLResponse)
def onboarding_company_info(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    """
    Company information page for new employees.

    Shows organizational structure, key contacts, and general info.
    """
    onboarding, service = _require_valid_token(token, db)

    # Get organization info
    org = onboarding.organization if hasattr(onboarding, "organization") else None

    # Get buddy info if assigned
    buddy = None
    if onboarding.buddy_employee_id:
        from app.models.people.hr import Employee
        buddy = db.get(Employee, onboarding.buddy_employee_id)

    # Get manager info if assigned
    manager = None
    if onboarding.manager_id:
        from app.models.people.hr import Employee
        manager = db.get(Employee, onboarding.manager_id)

    return templates.TemplateResponse(
        "onboarding/portal/company_info.html",
        {
            "request": request,
            "token": token,
            "onboarding": onboarding,
            "organization": org,
            "buddy": buddy,
            "manager": manager,
            "brand_name": settings.brand_name,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# Error Pages
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/expired", response_class=HTMLResponse)
def onboarding_link_expired(request: Request):
    """Expired link error page."""
    return templates.TemplateResponse(
        "onboarding/portal/expired.html",
        {
            "request": request,
            "brand_name": settings.brand_name,
        },
    )
