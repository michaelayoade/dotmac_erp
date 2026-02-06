"""
Workflow Tasks Web Routes.

HTML template routes for the workflow tasks dashboard.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.workflow_task import WorkflowTaskStatus, WorkflowTaskPriority
from app.services.common import PaginationParams
from app.services.people.hr.employees import EmployeeService
from app.services.workflow_task_service import WorkflowTaskService
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    require_web_auth,
)
from app.templates import templates


router = APIRouter(prefix="/tasks", tags=["workflow-tasks-web"])


def _get_employee_id(
    db: Session, organization_id: UUID, person_id: UUID
) -> Optional[UUID]:
    """Get employee ID from person ID, returns None if not found."""
    svc = EmployeeService(db, organization_id)
    employee = svc.get_employee_by_person(person_id)
    return employee.employee_id if employee else None


def format_relative_time(dt: Optional[datetime]) -> str:
    """Format datetime as relative time string."""
    if not dt:
        return "No due date"

    now = datetime.utcnow()
    # Handle timezone-aware datetimes
    if dt.tzinfo:
        dt = dt.replace(tzinfo=None)

    diff = dt - now

    if diff.total_seconds() < 0:
        # Past due
        past = abs(diff)
        if past.days > 30:
            return f"Overdue by {past.days // 30} months"
        elif past.days > 0:
            return f"Overdue by {past.days} days"
        elif past.seconds > 3600:
            return f"Overdue by {past.seconds // 3600}h"
        else:
            return "Overdue"
    else:
        # Future
        if diff.days > 30:
            return f"Due in {diff.days // 30} months"
        elif diff.days > 0:
            return f"Due in {diff.days} days"
        elif diff.seconds > 3600:
            return f"Due in {diff.seconds // 3600}h"
        else:
            return "Due soon"


def format_task_for_template(task) -> dict:
    """Convert WorkflowTask model to template-friendly dict."""
    return {
        "id": str(task.task_id),
        "title": task.title,
        "description": task.description or "",
        "module": task.module,
        "source_type": task.source_type,
        "action_url": task.action_url or "#",
        "status": task.status.value,
        "priority": task.priority.value,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "due_display": format_relative_time(task.due_at),
        "is_overdue": task.due_at
        and task.due_at.replace(tzinfo=None) < datetime.utcnow()
        if task.due_at
        else False,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


@router.get("", response_class=HTMLResponse)
async def workflow_tasks_list(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    page: int = Query(1, ge=1),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Display workflow tasks dashboard."""
    if not auth.is_authenticated:
        return RedirectResponse(url="/login", status_code=302)

    per_page = 20
    offset = (page - 1) * per_page

    employee_id = _get_employee_id(db, auth.organization_id, auth.person_id)
    if not employee_id:
        # No employee record, show empty state
        context = base_context(
            request=request,
            auth=auth,
            page_title="My Tasks",
            active_module="tasks",
            db=db,
        )
        context.update(
            {
                "tasks": [],
                "summary": {},
                "current_status": status,
                "current_priority": priority,
                "statuses": [s.value for s in WorkflowTaskStatus],
                "priorities": [p.value for p in WorkflowTaskPriority],
                "page": page,
                "has_more": False,
                "has_previous": False,
                "no_employee": True,
            }
        )
        return templates.TemplateResponse(request, "workflow_tasks/list.html", context)

    svc = WorkflowTaskService(db)

    # Parse filters
    status_enum = WorkflowTaskStatus(status) if status else None
    priority_enum = WorkflowTaskPriority(priority) if priority else None

    # Get tasks
    result = svc.list_tasks(
        org_id=auth.organization_id,
        assignee_employee_id=employee_id,
        status=status_enum,
        priority=priority_enum,
        pagination=PaginationParams(offset=offset, limit=per_page + 1),
    )

    has_more = len(result.items) > per_page
    tasks = result.items[:per_page]

    # Get summary counts
    summary = svc.summary(auth.organization_id, assignee_employee_id=employee_id)

    # Format for template
    formatted_tasks = [format_task_for_template(t) for t in tasks]

    context = base_context(
        request=request,
        auth=auth,
        page_title="My Tasks",
        active_module="tasks",
        db=db,
    )
    context.update(
        {
            "tasks": formatted_tasks,
            "summary": summary,
            "total_tasks": result.total,
            "current_status": status,
            "current_priority": priority,
            "statuses": [s.value for s in WorkflowTaskStatus],
            "priorities": [p.value for p in WorkflowTaskPriority],
            "page": page,
            "has_more": has_more,
            "has_previous": page > 1,
            "no_employee": False,
        }
    )

    return templates.TemplateResponse(request, "workflow_tasks/list.html", context)


@router.post("/{task_id}/complete", response_class=HTMLResponse)
async def complete_task(
    request: Request,
    task_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Mark a task as completed."""
    if not auth.is_authenticated:
        return RedirectResponse(url="/login", status_code=302)

    svc = WorkflowTaskService(db)
    try:
        svc.complete_task(auth.organization_id, UUID(task_id))
        db.commit()
    except Exception:
        db.rollback()

    # Return to list or HTMX response
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            content='<span class="text-emerald-600">Completed</span>',
            headers={"HX-Trigger": "taskCompleted"},
        )
    return RedirectResponse(url="/tasks", status_code=302)


@router.post("/{task_id}/snooze", response_class=HTMLResponse)
async def snooze_task(
    request: Request,
    task_id: str,
    days: int = Form(1),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Snooze a task."""
    if not auth.is_authenticated:
        return RedirectResponse(url="/login", status_code=302)

    svc = WorkflowTaskService(db)
    try:
        svc.snooze_task(auth.organization_id, UUID(task_id), days=days)
        db.commit()
    except Exception:
        db.rollback()

    # Return to list or HTMX response
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            content=f'<span class="text-amber-600">Snoozed {days} days</span>',
            headers={"HX-Trigger": "taskSnoozed"},
        )
    return RedirectResponse(url="/tasks", status_code=302)


@router.post("/{task_id}/status", response_class=HTMLResponse)
async def update_task_status(
    request: Request,
    task_id: str,
    status: str = Form(...),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Update task status."""
    if not auth.is_authenticated:
        return RedirectResponse(url="/login", status_code=302)

    svc = WorkflowTaskService(db)
    try:
        status_enum = WorkflowTaskStatus(status)
        svc.update_status(auth.organization_id, UUID(task_id), status_enum)
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url="/tasks", status_code=302)
