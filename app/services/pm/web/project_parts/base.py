"""Shared helpers for project web services."""

import logging
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import (
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.services.common import (
    NotFoundError,
    PaginationParams,
    ValidationError,
    coerce_uuid,
)
from app.services.common_filters import build_active_filters
from app.services.pm.web.import_web import project_import_web_service
from app.services.recent_activity import get_recent_activity_for_record
from app.services.storage import get_storage
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    require_projects_access,
)

logger = logging.getLogger(__name__)
MANUAL_PROJECT_CREATION_ENABLED = True


def _manual_project_creation_disabled_response(request: Request):
    """Manual project creation is disabled; projects are CRM-synced."""
    return templates.TemplateResponse(
        request,
        "errors/404.html",
        {
            "message": "Manual project creation is disabled. Projects are synced from CRM.",
        },
        status_code=404,
    )


# ============================================================================
# Safe Parsing Helpers
# ============================================================================


def _safe_date(value: str) -> date | None:
    """Safely parse a date string, returning None if invalid."""
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _format_project_error(exc: Exception) -> str:
    """Return a user-friendly error message for project actions."""
    if isinstance(exc, HTTPException):
        detail = getattr(exc, "detail", None)
        return detail or "Unable to save project. Please check your input."
    if isinstance(exc, IntegrityError):
        message = str(getattr(exc, "orig", exc))
        if "uq_project_code" in message:
            return "Project code already exists. Please choose a different code."
        if "foreign key" in message.lower():
            return (
                "Some selected references are invalid. Please reselect and try again."
            )
        return "Project could not be saved due to a data conflict. Please try again."
    if isinstance(exc, DataError):
        return "Some fields have invalid values or are too long. Please review and try again."
    return "Project could not be saved. Please check your input and try again."


def _safe_form_text(value: object) -> str:
    """Normalize form values to text for safe parsing."""
    if value is None:
        return ""
    if isinstance(value, UploadFile):
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _normalize_uploads(files: list[UploadFile] | None) -> list[UploadFile]:
    """Return only real uploaded files with filenames."""
    if not files:
        return []
    return [f for f in files if getattr(f, "filename", None)]


def _safe_decimal(value: str, default: Decimal | None = None) -> Decimal | None:
    """Safely parse a decimal string, returning default if invalid."""
    if not value or not value.strip():
        return default
    try:
        return Decimal(value.strip())
    except Exception:
        return default


def _build_pm_comment_attachment_map(links) -> dict[str, list]:
    """Group PM comment attachment links by comment_id."""
    mapping: dict[str, list] = {}
    for link in links or []:
        key = str(link.comment_id)
        mapping.setdefault(key, []).append(link.attachment)
    return mapping


def _project_type_duration_days(project_type):
    """Duration in days for project types that drive auto scheduling."""
    from app.models.finance.core_org.project import ProjectType

    durations = {
        ProjectType.FIBER_OPTICS_INSTALLATION: 14,
        ProjectType.FIBER_OPTICS_RELOCATION: 14,
        ProjectType.AIR_FIBER_INSTALLATION: 3,
        ProjectType.AIR_FIBER_RELOCATION: 3,
        ProjectType.CABLE_RERUN: 5,
    }
    return durations.get(project_type)


# ============================================================================
# Helper Functions
# ============================================================================


def _get_services(db: Session, org_id):
    """Get all PM services."""
    from uuid import UUID

    from app.services.pm import (
        DashboardService,
        GanttService,
        MilestoneService,
        ResourceService,
        TaskService,
        TimeEntryService,
    )

    org_uuid = UUID(str(org_id)) if not isinstance(org_id, UUID) else org_id
    return {
        "task": TaskService(db, org_uuid),
        "milestone": MilestoneService(db, org_uuid),
        "resource": ResourceService(db, org_uuid),
        "time": TimeEntryService(db, org_uuid),
        "dashboard": DashboardService(db, org_uuid),
        "gantt": GanttService(db, org_uuid),
    }


def _get_projects(db: Session, org_id):
    """Get all projects for the organization."""
    from sqlalchemy import select

    from app.models.finance.core_org.project import Project

    stmt = (
        select(Project)
        .where(Project.organization_id == coerce_uuid(org_id))
        .order_by(Project.project_name)
    )
    return list(db.scalars(stmt).all())


def _get_project_templates(db: Session, org_id):
    """Get all project templates for the organization."""
    from sqlalchemy import select

    from app.models.pm.project_template import ProjectTemplate

    stmt = (
        select(ProjectTemplate)
        .where(ProjectTemplate.organization_id == coerce_uuid(org_id))
        .order_by(ProjectTemplate.name)
    )
    return list(db.scalars(stmt).all())


def _resolve_project_template(db: Session, org_id, template_ref: str):
    """Resolve project template by UUID."""
    from sqlalchemy import select

    from app.models.pm.project_template import ProjectTemplate

    try:
        template_uuid = coerce_uuid(template_ref)
    except HTTPException:
        return None

    return db.scalars(
        select(ProjectTemplate).where(
            ProjectTemplate.template_id == template_uuid,
            ProjectTemplate.organization_id == coerce_uuid(org_id),
        )
    ).first()


def _template_tasks_payload(db: Session, template_id):
    """Build client-side payload for template task editor."""
    from sqlalchemy import select

    from app.models.pm.project_template_task import (
        ProjectTemplateTask,
        ProjectTemplateTaskDependency,
    )

    tasks = list(
        db.scalars(
            select(ProjectTemplateTask)
            .where(ProjectTemplateTask.template_id == template_id)
            .order_by(
                ProjectTemplateTask.order_index, ProjectTemplateTask.template_task_id
            )
        ).all()
    )
    if not tasks:
        return []

    deps = list(
        db.scalars(
            select(ProjectTemplateTaskDependency).where(
                ProjectTemplateTaskDependency.template_task_id.in_(
                    [t.template_task_id for t in tasks]
                )
            )
        ).all()
    )

    deps_map: dict[object, list[object]] = {}
    for dep in deps:
        deps_map.setdefault(dep.template_task_id, []).append(
            dep.depends_on_template_task_id
        )

    payload = []
    for task in tasks:
        payload.append(
            {
                "client_id": str(task.template_task_id),
                "task_name": task.task_name,
                "description": task.description or "",
                "depends_on": [
                    str(tid) for tid in deps_map.get(task.template_task_id, [])
                ],
            }
        )
    return payload


def _apply_project_template(db: Session, org_id, project, template_id):
    """Create project tasks from a template (one-time on project creation)."""
    from sqlalchemy import select

    from app.models.finance.core_config import SequenceType
    from app.models.pm.project_template_task import (
        ProjectTemplateTask,
        ProjectTemplateTaskDependency,
    )
    from app.models.pm.task_dependency import TaskDependency
    from app.services.finance.common.numbering import SyncNumberingService

    services = _get_services(db, org_id)
    numbering_service = SyncNumberingService(db)

    template_tasks = list(
        db.scalars(
            select(ProjectTemplateTask)
            .where(ProjectTemplateTask.template_id == template_id)
            .order_by(
                ProjectTemplateTask.order_index, ProjectTemplateTask.template_task_id
            )
        ).all()
    )
    if not template_tasks:
        return

    task_map = {}
    for template_task in template_tasks:
        task_code = numbering_service.generate_next_number(
            organization_id=coerce_uuid(org_id),
            sequence_type=SequenceType.TASK,
        )
        task = services["task"].create_task(
            {
                "project_id": project.project_id,
                "task_code": task_code,
                "task_name": template_task.task_name,
                "description": template_task.description,
            }
        )
        task_map[template_task.template_task_id] = task

    deps = list(
        db.scalars(
            select(ProjectTemplateTaskDependency).where(
                ProjectTemplateTaskDependency.template_task_id.in_(
                    [t.template_task_id for t in template_tasks]
                )
            )
        ).all()
    )
    for dep in deps:
        task = task_map.get(dep.template_task_id)
        depends_on = task_map.get(dep.depends_on_template_task_id)
        if not task or not depends_on:
            continue
        db.add(
            TaskDependency(
                task_id=task.task_id,
                depends_on_task_id=depends_on.task_id,
                dependency_type=dep.dependency_type,
                lag_days=dep.lag_days,
            )
        )


def _get_tickets(db: Session, org_id):
    """Get open/active tickets for task linking."""
    from sqlalchemy import select

    from app.models.support.ticket import Ticket, TicketStatus

    stmt = (
        select(Ticket)
        .where(
            Ticket.organization_id == coerce_uuid(org_id),
            Ticket.status.in_(
                [TicketStatus.OPEN, TicketStatus.REPLIED, TicketStatus.ON_HOLD]
            ),
        )
        .order_by(Ticket.created_at.desc())
        .limit(100)
    )
    return list(db.scalars(stmt).all())


def _get_employees(db: Session, org_id):
    """Get all employees for the organization."""
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    from app.models.people.hr.employee import Employee

    stmt = (
        select(Employee)
        .where(Employee.organization_id == coerce_uuid(org_id))
        .options(
            joinedload(Employee.person),
            joinedload(Employee.manager).joinedload(Employee.person),
        )
        .order_by(Employee.employee_code)
    )
    return list(db.scalars(stmt).all())


def _resolve_project_ref(db: Session, org_id, project_ref: str):
    """Resolve project by UUID or project_code."""
    from sqlalchemy import select

    from app.models.finance.core_org.project import Project

    org_uuid = coerce_uuid(org_id)
    try:
        project_uuid = coerce_uuid(project_ref)
        project = db.scalars(
            select(Project).where(
                Project.project_id == project_uuid,
                Project.organization_id == org_uuid,
            )
        ).first()
        if project:
            return project
    except HTTPException:
        pass

    return db.scalars(
        select(Project).where(
            Project.project_code == project_ref,
            Project.organization_id == org_uuid,
        )
    ).first()


def _project_url(project) -> str:
    return f"/projects/{project.project_code or project.project_id}"


def _resolve_task_ref(db: Session, org_id, project_id, task_ref: str):
    """Resolve task by UUID or task_code."""
    from sqlalchemy import select

    from app.models.pm import Task

    org_uuid = coerce_uuid(org_id)
    project_uuid = coerce_uuid(project_id)
    try:
        task_uuid = coerce_uuid(task_ref)
        task = db.scalars(
            select(Task).where(
                Task.task_id == task_uuid,
                Task.organization_id == org_uuid,
                Task.project_id == project_uuid,
            )
        ).first()
        if task:
            return task
    except HTTPException:
        pass

    return db.scalars(
        select(Task).where(
            Task.task_code == task_ref,
            Task.organization_id == org_uuid,
            Task.project_id == project_uuid,
        )
    ).first()


def _task_url(project, task) -> str:
    return f"/projects/{project.project_code}/tasks/{task.task_code or task.task_id}"


def _ensure_task_code(db: Session, org_id, task):
    """Ensure task_code exists for legacy tasks."""
    if task.task_code:
        return
    from app.models.finance.core_config import SequenceType
    from app.services.finance.common.numbering import SyncNumberingService

    numbering_service = SyncNumberingService(db)
    task.task_code = numbering_service.generate_next_number(
        organization_id=coerce_uuid(org_id),
        sequence_type=SequenceType.TASK,
    )
    db.flush()


# ============================================================================
# Project List
# ============================================================================

__all__ = [
    "DataError",
    "Decimal",
    "Depends",
    "File",
    "FileResponse",
    "Form",
    "HTTPException",
    "IntegrityError",
    "JSONResponse",
    "MANUAL_PROJECT_CREATION_ENABLED",
    "NotFoundError",
    "PaginationParams",
    "Path",
    "Query",
    "RedirectResponse",
    "Request",
    "Session",
    "StreamingResponse",
    "UploadFile",
    "ValidationError",
    "WebAuthContext",
    "_apply_project_template",
    "_build_pm_comment_attachment_map",
    "_ensure_task_code",
    "_format_project_error",
    "_get_employees",
    "_get_project_templates",
    "_get_projects",
    "_get_services",
    "_get_tickets",
    "_manual_project_creation_disabled_response",
    "_normalize_uploads",
    "_project_type_duration_days",
    "_project_url",
    "_resolve_project_ref",
    "_resolve_project_template",
    "_resolve_task_ref",
    "_safe_date",
    "_safe_decimal",
    "_safe_form_text",
    "_task_url",
    "_template_tasks_payload",
    "base_context",
    "build_active_filters",
    "coerce_uuid",
    "date",
    "get_db",
    "get_recent_activity_for_record",
    "get_storage",
    "logger",
    "logging",
    "project_import_web_service",
    "require_projects_access",
    "templates",
    "timedelta",
]
