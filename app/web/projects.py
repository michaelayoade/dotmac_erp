"""
Project Management Web Routes.

HTML template routes for project management including tasks, milestones,
resources, and time tracking.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.services.common import (
    NotFoundError,
    PaginationParams,
    ValidationError,
    coerce_uuid,
)
from app.services.pm.web.import_web import project_import_web_service
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    require_projects_access,
)

router = APIRouter(prefix="/projects", tags=["projects-web"])
logger = logging.getLogger(__name__)


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

    deps_map = {}
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


@router.get("", response_class=HTMLResponse)
def list_projects(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    search: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Projects list page."""
    from sqlalchemy import func, select

    from app.models.finance.core_org.project import Project, ProjectStatus

    org_id = coerce_uuid(auth.organization_id)

    # Build query
    stmt = select(Project).where(Project.organization_id == org_id)

    if search:
        stmt = stmt.where(
            Project.project_name.ilike(f"%{search}%")
            | Project.project_code.ilike(f"%{search}%")
        )
    if status:
        try:
            status_key = status.strip().upper().replace("-", "_")
            status_enum = ProjectStatus(status_key)
            stmt = stmt.where(Project.status == status_enum)
        except ValueError:
            pass

    stmt = stmt.order_by(Project.project_name)

    # Paginate
    per_page = 20
    offset = (page - 1) * per_page
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    projects = list(db.scalars(stmt.offset(offset).limit(per_page)).all())

    # Stats counts (unfiltered, for the org)
    base_stmt = (
        select(Project.status, func.count())
        .where(Project.organization_id == org_id)
        .group_by(Project.status)
    )
    rows = db.execute(base_stmt).all()
    # Normalize keys: status may come back as enum or string
    status_counts: dict[str, int] = {}
    for row in rows:
        key = row[0].value if hasattr(row[0], "value") else str(row[0])
        status_counts[key] = row[1]
    total_all = sum(status_counts.values())

    context = {
        "request": request,
        **base_context(request, auth, "Projects", "projects", db=db),
        "projects": projects,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
        "search": search,
        "status_filter": status,
        "statuses": [s.value for s in ProjectStatus],
        "total_all": total_all,
        "active_count": status_counts.get("ACTIVE", 0),
        "completed_count": status_counts.get("COMPLETED", 0),
        "on_hold_count": status_counts.get("ON_HOLD", 0),
        "planning_count": status_counts.get("PLANNING", 0),
    }

    return templates.TemplateResponse("projects/list.html", context)


# ============================================================================
# Project Dashboard
# ============================================================================


# ============================================================================
# Project Form (Create/Edit)
# ============================================================================


@router.get("/new", response_class=HTMLResponse)
def new_project_form(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """New project form page."""
    from sqlalchemy import select

    from app.models.finance.ar.customer import Customer
    from app.models.finance.core_org.project import (
        ProjectPriority,
        ProjectStatus,
        ProjectType,
    )

    org_id = coerce_uuid(auth.organization_id)

    # Get customers for dropdown
    customers = db.scalars(
        select(Customer)
        .where(Customer.organization_id == org_id, Customer.is_active == True)
        .order_by(Customer.trading_name)
    ).all()

    allowed_project_types = [
        ProjectType.FIBER_OPTICS_INSTALLATION,
        ProjectType.AIR_FIBER_INSTALLATION,
        ProjectType.CABLE_RERUN,
        ProjectType.FIBER_OPTICS_RELOCATION,
        ProjectType.AIR_FIBER_RELOCATION,
    ]

    context = {
        "request": request,
        **base_context(request, auth, "New Project", "projects", db=db),
        "project": None,
        "statuses": [s.value for s in ProjectStatus],
        "project_types": [t.value for t in allowed_project_types],
        "priorities": [p.value for p in ProjectPriority],
        "customers": customers,
        "project_templates": _get_project_templates(db, org_id),
        "error": request.query_params.get("error"),
    }

    return templates.TemplateResponse("projects/form.html", context)


@router.get("/{project_id}/edit", response_class=HTMLResponse)
def edit_project_form(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Edit project form page."""
    from sqlalchemy import select

    from app.models.finance.ar.customer import Customer
    from app.models.finance.core_org.project import (
        ProjectPriority,
        ProjectStatus,
        ProjectType,
    )

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    # Get customers for dropdown
    customers = db.scalars(
        select(Customer)
        .where(Customer.organization_id == org_id, Customer.is_active == True)
        .order_by(Customer.trading_name)
    ).all()

    allowed_project_types = [
        ProjectType.FIBER_OPTICS_INSTALLATION,
        ProjectType.AIR_FIBER_INSTALLATION,
        ProjectType.CABLE_RERUN,
        ProjectType.FIBER_OPTICS_RELOCATION,
        ProjectType.AIR_FIBER_RELOCATION,
    ]

    context = {
        "request": request,
        **base_context(request, auth, "Edit Project", "projects", db=db),
        "project": project,
        "statuses": [s.value for s in ProjectStatus],
        "project_types": [t.value for t in allowed_project_types],
        "priorities": [p.value for p in ProjectPriority],
        "customers": customers,
        "project_templates": _get_project_templates(db, org_id),
    }

    return templates.TemplateResponse("projects/form.html", context)


# ============================================================================
# Project Templates (Global)
# ============================================================================


@router.get("/templates", response_class=HTMLResponse)
def project_template_list(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Project template list page."""
    templates_list = _get_project_templates(db, auth.organization_id)
    context = {
        "request": request,
        **base_context(request, auth, "Project Templates", "templates", db=db),
        "templates": templates_list,
    }
    return templates.TemplateResponse("projects/templates/list.html", context)


@router.get("/templates/new", response_class=HTMLResponse)
def new_project_template_form(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """New project template form page."""
    from app.models.finance.core_org.project import ProjectType

    context = {
        "request": request,
        **base_context(request, auth, "New Project Template", "templates", db=db),
        "template": None,
        "project_types": [t.value for t in ProjectType],
        "tasks_payload_json": "[]",
    }
    return templates.TemplateResponse("projects/templates/form.html", context)


@router.post("/templates", response_class=RedirectResponse)
async def create_project_template(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Create a new project template with ordered tasks."""
    import json
    import logging

    from app.models.finance.core_org.project import ProjectType
    from app.models.pm.project_template import ProjectTemplate
    from app.models.pm.project_template_task import (
        ProjectTemplateTask,
        ProjectTemplateTaskDependency,
    )
    from app.models.pm.task_dependency import DependencyType

    org_id = coerce_uuid(auth.organization_id)
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    logging.getLogger(__name__).warning(
        "Project template POST form keys: %s",
        list(form.keys()),
    )

    template_name_value = _safe_form_text(
        form.get("template_name") or form.get("name")
    ).strip()
    project_type = _safe_form_text(form.get("project_type") or "INTERNAL").strip()
    tasks_json = _safe_form_text(form.get("tasks_json")) or "[]"
    if not template_name_value:
        context = {
            "request": request,
            **base_context(request, auth, "New Project Template", "templates", db=db),
            "template": None,
            "project_types": [t.value for t in ProjectType],
            "error": "Template name is required.",
            "submitted_name": template_name_value,
        }
        return templates.TemplateResponse(
            "projects/templates/form.html",
            context,
            status_code=400,
        )
    template = ProjectTemplate(
        organization_id=org_id,
        name=template_name_value,
        project_type=ProjectType(project_type)
        if project_type
        else ProjectType.INTERNAL,
    )
    db.add(template)
    db.flush()

    try:
        task_payload = json.loads(tasks_json or "[]")
    except json.JSONDecodeError:
        task_payload = []

    task_map = {}
    order_index = 1
    for task_entry in task_payload:
        task_name = (task_entry.get("task_name") or "").strip()
        if not task_name:
            continue
        task = ProjectTemplateTask(
            template_id=template.template_id,
            task_name=task_name,
            description=(task_entry.get("description") or "").strip() or None,
            order_index=order_index,
        )
        db.add(task)
        db.flush()
        task_map[str(task_entry.get("client_id"))] = task
        order_index += 1

    for task_entry in task_payload:
        client_id = str(task_entry.get("client_id"))
        mapped_task = task_map.get(client_id)
        if not mapped_task:
            continue
        depends_on = task_entry.get("depends_on") or []
        for depends_on_id in depends_on:
            depends_on_task = task_map.get(str(depends_on_id))
            if not depends_on_task:
                continue
            db.add(
                ProjectTemplateTaskDependency(
                    template_task_id=mapped_task.template_task_id,
                    depends_on_template_task_id=depends_on_task.template_task_id,
                    dependency_type=DependencyType.FINISH_TO_START,
                )
            )

    db.commit()
    return RedirectResponse(
        url=f"/projects/templates/{template.template_id}?saved=1", status_code=303
    )


@router.get("/templates/{template_id}", response_class=HTMLResponse)
def project_template_detail(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Project template detail page."""
    from sqlalchemy import select

    from app.models.pm.project_template_task import (
        ProjectTemplateTask,
        ProjectTemplateTaskDependency,
    )

    org_id = coerce_uuid(auth.organization_id)
    template = _resolve_project_template(db, org_id, template_id)
    if not template:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project template not found"},
            status_code=404,
        )

    tasks = list(
        db.scalars(
            select(ProjectTemplateTask)
            .where(ProjectTemplateTask.template_id == template.template_id)
            .order_by(
                ProjectTemplateTask.order_index, ProjectTemplateTask.template_task_id
            )
        ).all()
    )
    dependencies = list(
        db.scalars(
            select(ProjectTemplateTaskDependency).where(
                ProjectTemplateTaskDependency.template_task_id.in_(
                    [t.template_task_id for t in tasks]
                )
            )
        ).all()
    )

    context = {
        "request": request,
        **base_context(request, auth, template.name, "templates", db=db),
        "template": template,
        "tasks": tasks,
        "dependencies": dependencies,
    }
    return templates.TemplateResponse("projects/templates/detail.html", context)


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def edit_project_template_form(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Edit project template form page."""
    import json

    from app.models.finance.core_org.project import ProjectType

    org_id = coerce_uuid(auth.organization_id)
    template = _resolve_project_template(db, org_id, template_id)
    if not template:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project template not found"},
            status_code=404,
        )

    tasks_payload = _template_tasks_payload(db, template.template_id)
    context = {
        "request": request,
        **base_context(request, auth, f"Edit {template.name}", "templates", db=db),
        "template": template,
        "project_types": [t.value for t in ProjectType],
        "tasks_payload_json": json.dumps(tasks_payload),
    }
    return templates.TemplateResponse("projects/templates/form.html", context)


@router.post("/templates/{template_id}", response_class=RedirectResponse)
async def update_project_template(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Update a project template and its tasks."""
    import json

    from sqlalchemy import delete, select

    from app.models.finance.core_org.project import ProjectType
    from app.models.pm.project_template_task import (
        ProjectTemplateTask,
        ProjectTemplateTaskDependency,
    )
    from app.models.pm.task_dependency import DependencyType

    org_id = coerce_uuid(auth.organization_id)
    template = _resolve_project_template(db, org_id, template_id)
    if not template:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project template not found"},
            status_code=404,
        )

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    template_name_value = _safe_form_text(
        form.get("template_name") or form.get("name")
    ).strip()
    project_type = _safe_form_text(form.get("project_type") or "INTERNAL").strip()
    tasks_json = _safe_form_text(form.get("tasks_json")) or "[]"

    if not template_name_value:
        context = {
            "request": request,
            **base_context(request, auth, f"Edit {template.name}", "templates", db=db),
            "template": template,
            "project_types": [t.value for t in ProjectType],
            "error": "Template name is required.",
            "submitted_name": template_name_value,
            "tasks_payload_json": tasks_json,
        }
        return templates.TemplateResponse(
            "projects/templates/form.html",
            context,
            status_code=400,
        )

    template.name = template_name_value
    template.project_type = (
        ProjectType(project_type) if project_type else ProjectType.INTERNAL
    )

    try:
        task_payload = json.loads(tasks_json or "[]")
    except json.JSONDecodeError:
        task_payload = []

    existing_tasks = list(
        db.scalars(
            select(ProjectTemplateTask).where(
                ProjectTemplateTask.template_id == template.template_id
            )
        ).all()
    )
    if existing_tasks:
        task_ids = [t.template_task_id for t in existing_tasks]
        db.execute(
            delete(ProjectTemplateTaskDependency).where(
                ProjectTemplateTaskDependency.template_task_id.in_(task_ids)
            )
        )
        db.execute(
            delete(ProjectTemplateTask).where(
                ProjectTemplateTask.template_id == template.template_id
            )
        )

    task_map = {}
    order_index = 1
    for task_entry in task_payload:
        task_name = (task_entry.get("task_name") or "").strip()
        if not task_name:
            continue
        task = ProjectTemplateTask(
            template_id=template.template_id,
            task_name=task_name,
            description=(task_entry.get("description") or "").strip() or None,
            order_index=order_index,
        )
        db.add(task)
        db.flush()
        task_map[str(task_entry.get("client_id"))] = task
        order_index += 1

    for task_entry in task_payload:
        client_id = str(task_entry.get("client_id"))
        mapped_task = task_map.get(client_id)
        if not mapped_task:
            continue
        depends_on = task_entry.get("depends_on") or []
        for depends_on_id in depends_on:
            depends_on_task = task_map.get(str(depends_on_id))
            if not depends_on_task:
                continue
            db.add(
                ProjectTemplateTaskDependency(
                    template_task_id=mapped_task.template_task_id,
                    depends_on_template_task_id=depends_on_task.template_task_id,
                    dependency_type=DependencyType.FINISH_TO_START,
                )
            )

    db.commit()
    return RedirectResponse(
        url=f"/projects/templates/{template.template_id}?saved=1", status_code=303
    )


# ============================================================================
# Global Task Management
# ============================================================================


@router.get("/tasks", response_class=HTMLResponse)
def global_task_list(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    status: str | None = None,
    priority: str | None = None,
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Global task list page."""
    from app.models.pm.task import TaskPriority, TaskStatus

    org_id = coerce_uuid(auth.organization_id)
    services = _get_services(db, org_id)

    tasks = (
        services["task"]
        .list_tasks(
            project_id=coerce_uuid(project_id) if project_id else None,
            status=TaskStatus(status) if status else None,
            priority=TaskPriority(priority) if priority else None,
            include_subtasks=True,
        )
        .items
    )

    context = {
        "request": request,
        **base_context(request, auth, "Project Tasks", "tasks", db=db),
        "tasks": tasks,
        "projects": _get_projects(db, org_id),
        "statuses": [s.value for s in TaskStatus],
        "priorities": [p.value for p in TaskPriority],
        "status_filter": status,
        "priority_filter": priority,
        "project_filter": project_id,
    }
    return templates.TemplateResponse("projects/tasks/global_list.html", context)


@router.get("/tasks/new", response_class=HTMLResponse)
def global_task_new_form(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Global new task form page."""
    from app.models.pm.task import TaskPriority, TaskStatus

    org_id = coerce_uuid(auth.organization_id)
    context = {
        "request": request,
        **base_context(request, auth, "New Task", "tasks", db=db),
        "task": None,
        "projects": _get_projects(db, org_id),
        "statuses": [s.value for s in TaskStatus],
        "priorities": [p.value for p in TaskPriority],
        "team_members": _get_employees(db, org_id),
        "tickets": _get_tickets(db, org_id),
    }
    return templates.TemplateResponse("projects/tasks/global_form.html", context)


@router.post("/tasks", response_class=RedirectResponse)
def create_global_task(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    project_id: str = Form(...),
    task_name: str = Form(...),
    task_code: str = Form(default=""),
    status: str = Form(default="OPEN"),
    priority: str = Form(default="MEDIUM"),
    description: str = Form(default=""),
    start_date: str = Form(default=""),
    due_date: str = Form(default=""),
    estimated_hours: str = Form(default=""),
    assigned_to_id: str = Form(default=""),
    ticket_id: str = Form(default=""),
    parent_task_id: str = Form(default=""),
    files: list[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """Create a new task from the global task form."""
    from app.models.finance.core_config import SequenceType
    from app.models.pm.task import TaskPriority, TaskStatus
    from app.services.finance.common.numbering import SyncNumberingService

    org_id = coerce_uuid(auth.organization_id)
    services = _get_services(db, org_id)

    task_code_value = task_code.strip() if task_code else ""
    if not task_code_value:
        numbering_service = SyncNumberingService(db)
        task_code_value = numbering_service.generate_next_number(
            organization_id=org_id,
            sequence_type=SequenceType.TASK,
        )

    parent_task_uuid = coerce_uuid(parent_task_id) if parent_task_id else None
    if parent_task_uuid:
        parent_task = services["task"].get_task(parent_task_uuid)
        if not parent_task or str(parent_task.project_id) != str(project_id):
            parent_task_uuid = None

    task = services["task"].create_task(
        {
            "project_id": coerce_uuid(project_id),
            "task_code": task_code_value,
            "task_name": task_name.strip(),
            "description": description.strip() if description else None,
            "parent_task_id": parent_task_uuid,
            "ticket_id": coerce_uuid(ticket_id) if ticket_id else None,
            "priority": TaskPriority(priority) if priority else TaskPriority.MEDIUM,
            "assigned_to_id": coerce_uuid(assigned_to_id) if assigned_to_id else None,
            "start_date": _safe_date(start_date),
            "due_date": _safe_date(due_date),
            "estimated_hours": _safe_decimal(estimated_hours),
            "status": TaskStatus(status) if status else TaskStatus.OPEN,
        }
    )

    upload_files = _normalize_uploads(files)
    if upload_files:
        from app.services.pm.attachment import project_attachment_service

        for file in upload_files:
            project_attachment_service.save_file(
                db,
                organization_id=org_id,
                entity_type="TASK",
                entity_id=task.task_id,
                filename=file.filename or "unnamed",
                file_data=file.file,
                content_type=file.content_type or "application/octet-stream",
                uploaded_by_id=coerce_uuid(auth.user_id),
            )

    db.commit()
    return RedirectResponse(
        url=f"/projects/tasks?project_id={task.project_id}&saved=1",
        status_code=303,
    )


@router.get("/{project_id}", response_class=HTMLResponse)
def project_dashboard(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Project dashboard/detail page."""
    from app.services.pm.attachment import project_attachment_service
    from app.services.pm.comment import comment_service

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)

    try:
        dashboard_data = services["dashboard"].get_project_dashboard(project.project_id)
    except NotFoundError:
        dashboard_data = {}

    customer_info = None
    if project.customer:
        contact = project.customer.primary_contact or {}
        customer_info = {
            "customer_name": project.customer.trading_name
            or project.customer.legal_name,
            "customer_code": project.customer.customer_code,
            "email": contact.get("email"),
            "phone": contact.get("phone"),
            "billing_address": (project.customer.billing_address or {}).get(
                "address", ""
            ),
            "shipping_address": (project.customer.shipping_address or {}).get(
                "address", ""
            ),
        }

    comments = comment_service.list_comments(
        db,
        organization_id=org_id,
        entity_type="PROJECT",
        entity_id=project.project_id,
        include_internal=True,
    )
    comment_links = comment_service.list_comment_attachments(
        db, [c.comment_id for c in comments]
    )
    comment_attachment_map = _build_pm_comment_attachment_map(comment_links)
    comment_attachment_ids = {link.attachment_id for link in comment_links}

    all_attachments = project_attachment_service.list_attachments(
        db, org_id, "PROJECT", project.project_id
    )
    project_attachments = [
        att
        for att in all_attachments
        if att.attachment_id not in comment_attachment_ids
    ]

    context = {
        "request": request,
        **base_context(request, auth, project.project_name, "projects", db=db),
        "project": project,
        "dashboard": dashboard_data,
        "customer_info": customer_info,
        "comments": comments,
        "comment_attachments": comment_attachment_map,
        "attachments": project_attachments,
    }

    return templates.TemplateResponse("projects/detail.html", context)


@router.post("/{project_id}/comments", response_class=RedirectResponse)
async def add_project_comment(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    content: str = Form(...),
    is_internal: bool = Form(default=False),
    files: list[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """Add a comment to a project."""
    from app.models.pm.comment import PMCommentAttachment
    from app.services.pm.attachment import project_attachment_service
    from app.services.pm.comment import comment_service

    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+saved+successfully", status_code=303
        )

    upload_files = _normalize_uploads(files)
    attachment_errors = []

    try:
        comment = comment_service.add_comment(
            db,
            organization_id=org_id,
            entity_type="PROJECT",
            entity_id=project.project_id,
            author_id=user_id,
            content=content,
            is_internal=is_internal,
        )

        for file in upload_files:
            attachment, error = project_attachment_service.save_file(
                db,
                organization_id=org_id,
                entity_type="PROJECT",
                entity_id=project.project_id,
                filename=file.filename or "unnamed",
                file_data=file.file,
                content_type=file.content_type or "application/octet-stream",
                uploaded_by_id=user_id,
            )
            if error or not attachment:
                attachment_errors.append(error or "Upload failed")
                continue
            db.add(
                PMCommentAttachment(
                    comment_id=comment.comment_id,
                    attachment_id=attachment.attachment_id,
                )
            )

        db.commit()
    except Exception:
        db.rollback()
        attachment_errors.append("Comment upload failed")

    base_url = _project_url(project)
    if attachment_errors:
        base_url += "?warning=Some+attachments+failed+to+upload"
    return RedirectResponse(url=base_url + "#comments", status_code=303)


@router.post(
    "/{project_id}/comments/{comment_id}/delete", response_class=RedirectResponse
)
def delete_project_comment(
    request: Request,
    project_id: str,
    comment_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Delete a project comment."""
    from app.services.pm.comment import comment_service

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+deleted+successfully", status_code=303
        )

    try:
        comment_service.delete_comment(db, org_id, coerce_uuid(comment_id))
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(
        url=_project_url(project) + "?saved=1" + "#comments", status_code=303
    )


@router.post("", response_class=RedirectResponse)
@router.post("/", response_class=RedirectResponse)
@router.post("/new", response_class=RedirectResponse)
async def create_project(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    project_code: str = Form(default=""),
    project_name: str = Form(default=""),
    description: str = Form(default=""),
    status: str = Form(default="PLANNING"),
    project_type: str = Form(default=""),
    project_priority: str = Form(default="MEDIUM"),
    project_template_id: str = Form(default=""),
    customer_id: str = Form(default=""),
    start_date: str = Form(default=""),
    end_date: str = Form(default=""),
    budget_amount: str = Form(default=""),
    percent_complete: str = Form(default="0"),
    files: list[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """Create a new project."""
    import logging

    from sqlalchemy import select

    from app.models.finance.ar.customer import Customer
    from app.models.finance.core_config import SequenceType
    from app.models.finance.core_org.project import (
        Project,
        ProjectPriority,
        ProjectStatus,
        ProjectType,
    )
    from app.services.finance.common.numbering import SyncNumberingService

    org_id = coerce_uuid(auth.organization_id)

    # Prefer CSRF-parsed form data when available (middleware may consume body).
    form_data = getattr(request.state, "csrf_form", None)
    if form_data is None:
        form_data = await request.form()
    if form_data:
        project_code = _safe_form_text(form_data.get("project_code") or project_code)
        project_name = _safe_form_text(form_data.get("project_name") or project_name)
        description = _safe_form_text(form_data.get("description") or description)
        status = _safe_form_text(form_data.get("status") or status)
        project_type = _safe_form_text(form_data.get("project_type") or project_type)
        project_priority = _safe_form_text(
            form_data.get("project_priority") or project_priority
        )
        project_template_id = _safe_form_text(
            form_data.get("project_template_id") or project_template_id
        )
        customer_id = _safe_form_text(form_data.get("customer_id") or customer_id)
        start_date = _safe_form_text(form_data.get("start_date") or start_date)
        end_date = _safe_form_text(form_data.get("end_date") or end_date)
        budget_amount = _safe_form_text(form_data.get("budget_amount") or budget_amount)
        percent_complete = _safe_form_text(
            form_data.get("percent_complete") or percent_complete
        )

    if not project_name or not project_name.strip():
        logging.getLogger(__name__).warning(
            "Project create missing name. Content-Type=%s Form keys=%s",
            request.headers.get("content-type"),
            list(form_data.keys()) if form_data else [],
        )
        customers = db.scalars(
            select(Customer)
            .where(Customer.organization_id == org_id, Customer.is_active == True)
            .order_by(Customer.trading_name)
        ).all()
        allowed_project_types = [
            ProjectType.FIBER_OPTICS_INSTALLATION,
            ProjectType.AIR_FIBER_INSTALLATION,
            ProjectType.CABLE_RERUN,
            ProjectType.FIBER_OPTICS_RELOCATION,
            ProjectType.AIR_FIBER_RELOCATION,
        ]
        context = {
            "request": request,
            **base_context(request, auth, "New Project", "projects", db=db),
            "project": None,
            "statuses": [s.value for s in ProjectStatus],
            "project_types": [t.value for t in allowed_project_types],
            "priorities": [p.value for p in ProjectPriority],
            "customers": customers,
            "project_templates": _get_project_templates(db, org_id),
            "error": "Project name is required.",
            "form_data": {
                "project_code": project_code,
                "project_name": project_name,
                "description": description,
                "status": status,
                "project_type": project_type,
                "project_priority": project_priority,
                "project_template_id": project_template_id,
                "customer_id": customer_id,
                "start_date": start_date,
                "end_date": end_date,
                "budget_amount": budget_amount,
                "percent_complete": percent_complete,
            },
        }
        return templates.TemplateResponse(
            "projects/form.html",
            context,
            status_code=400,
        )

    project_code_value = project_code.strip() if project_code else ""
    if not project_code_value:
        numbering_service = SyncNumberingService(db)
        project_code_value = numbering_service.generate_next_number(
            organization_id=org_id,
            sequence_type=SequenceType.PROJECT,
        )

    project_type_value = (project_type or "").strip()
    if not project_type_value:
        customers = db.scalars(
            select(Customer)
            .where(Customer.organization_id == org_id, Customer.is_active == True)
            .order_by(Customer.trading_name)
        ).all()
        allowed_project_types = [
            ProjectType.FIBER_OPTICS_INSTALLATION,
            ProjectType.AIR_FIBER_INSTALLATION,
            ProjectType.CABLE_RERUN,
            ProjectType.FIBER_OPTICS_RELOCATION,
            ProjectType.AIR_FIBER_RELOCATION,
        ]
        context = {
            "request": request,
            **base_context(request, auth, "New Project", "projects", db=db),
            "project": None,
            "statuses": [s.value for s in ProjectStatus],
            "project_types": [t.value for t in allowed_project_types],
            "priorities": [p.value for p in ProjectPriority],
            "customers": customers,
            "project_templates": _get_project_templates(db, org_id),
            "error": "Please select a project type.",
            "form_data": {
                "project_code": project_code,
                "project_name": project_name,
                "description": description,
                "status": status,
                "project_type": project_type_value,
                "project_priority": project_priority,
                "project_template_id": project_template_id,
                "customer_id": customer_id,
                "start_date": start_date,
                "end_date": end_date,
                "budget_amount": budget_amount,
                "percent_complete": percent_complete,
            },
        }
        return templates.TemplateResponse(
            "projects/form.html",
            context,
            status_code=400,
        )

    try:
        project_type_enum = ProjectType(project_type_value)
    except ValueError:
        customers = db.scalars(
            select(Customer)
            .where(Customer.organization_id == org_id, Customer.is_active == True)
            .order_by(Customer.trading_name)
        ).all()
        allowed_project_types = [
            ProjectType.FIBER_OPTICS_INSTALLATION,
            ProjectType.AIR_FIBER_INSTALLATION,
            ProjectType.CABLE_RERUN,
            ProjectType.FIBER_OPTICS_RELOCATION,
            ProjectType.AIR_FIBER_RELOCATION,
        ]
        context = {
            "request": request,
            **base_context(request, auth, "New Project", "projects", db=db),
            "project": None,
            "statuses": [s.value for s in ProjectStatus],
            "project_types": [t.value for t in allowed_project_types],
            "priorities": [p.value for p in ProjectPriority],
            "customers": customers,
            "project_templates": _get_project_templates(db, org_id),
            "error": "Invalid project type selection.",
            "form_data": {
                "project_code": project_code,
                "project_name": project_name,
                "description": description,
                "status": status,
                "project_type": project_type_value,
                "project_priority": project_priority,
                "project_template_id": project_template_id,
                "customer_id": customer_id,
                "start_date": start_date,
                "end_date": end_date,
                "budget_amount": budget_amount,
                "percent_complete": percent_complete,
            },
        }
        return templates.TemplateResponse(
            "projects/form.html",
            context,
            status_code=400,
        )

    start_date_value = date.today()
    duration_days = _project_type_duration_days(project_type_enum)
    end_date_value = (
        start_date_value + timedelta(days=duration_days) if duration_days else None
    )

    try:
        project = Project(
            organization_id=org_id,
            project_code=project_code_value,
            project_name=project_name.strip(),
            description=description.strip() if description else None,
            status=ProjectStatus(status),
            project_type=project_type_enum,
            project_priority=ProjectPriority(project_priority)
            if project_priority
            else ProjectPriority.MEDIUM,
            project_template_id=coerce_uuid(project_template_id)
            if project_template_id
            else None,
            customer_id=coerce_uuid(customer_id) if customer_id else None,
            start_date=start_date_value,
            end_date=end_date_value,
            budget_amount=_safe_decimal(budget_amount),
            percent_complete=_safe_decimal(percent_complete, Decimal("0")),
        )

        db.add(project)
        db.flush()

        if project.project_template_id:
            _apply_project_template(db, org_id, project, project.project_template_id)

        upload_files = _normalize_uploads(files)
        if upload_files:
            from app.services.pm.attachment import project_attachment_service

            for file in upload_files:
                project_attachment_service.save_file(
                    db,
                    organization_id=org_id,
                    entity_type="PROJECT",
                    entity_id=project.project_id,
                    filename=file.filename or "unnamed",
                    file_data=file.file,
                    content_type=file.content_type or "application/octet-stream",
                    uploaded_by_id=coerce_uuid(auth.user_id),
                )

        db.commit()

        return RedirectResponse(url=_project_url(project) + "?saved=1", status_code=303)
    except Exception as exc:
        db.rollback()
        customers = db.scalars(
            select(Customer)
            .where(Customer.organization_id == org_id, Customer.is_active == True)
            .order_by(Customer.trading_name)
        ).all()
        allowed_project_types = [
            ProjectType.FIBER_OPTICS_INSTALLATION,
            ProjectType.AIR_FIBER_INSTALLATION,
            ProjectType.CABLE_RERUN,
            ProjectType.FIBER_OPTICS_RELOCATION,
            ProjectType.AIR_FIBER_RELOCATION,
        ]
        context = {
            "request": request,
            **base_context(request, auth, "New Project", "projects", db=db),
            "project": None,
            "statuses": [s.value for s in ProjectStatus],
            "project_types": [t.value for t in allowed_project_types],
            "priorities": [p.value for p in ProjectPriority],
            "customers": customers,
            "project_templates": _get_project_templates(db, org_id),
            "error": _format_project_error(exc),
            "form_data": {
                "project_code": project_code,
                "project_name": project_name,
                "description": description,
                "status": status,
                "project_type": project_type_value,
                "project_priority": project_priority,
                "project_template_id": project_template_id,
                "customer_id": customer_id,
                "start_date": start_date,
                "end_date": end_date,
                "budget_amount": budget_amount,
                "percent_complete": percent_complete,
            },
        }
        return templates.TemplateResponse(
            "projects/form.html",
            context,
            status_code=400,
        )


@router.post("/{project_id}", response_class=RedirectResponse)
async def update_project(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    project_name: str = Form(...),
    description: str = Form(default=""),
    status: str = Form(...),
    project_type: str = Form(default="INTERNAL"),
    project_priority: str = Form(default="MEDIUM"),
    customer_id: str = Form(default=""),
    start_date: str = Form(default=""),
    end_date: str = Form(default=""),
    budget_amount: str = Form(default=""),
    percent_complete: str = Form(default="0"),
    files: list[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """Update an existing project."""
    from app.models.finance.core_org.project import (
        ProjectPriority,
        ProjectStatus,
        ProjectType,
    )

    org_id = coerce_uuid(auth.organization_id)

    # Prefer CSRF-parsed form data when available (middleware may consume body).
    form_data = getattr(request.state, "csrf_form", None)
    if form_data is None:
        form_data = await request.form()
    if form_data:
        project_name = _safe_form_text(form_data.get("project_name") or project_name)
        description = _safe_form_text(form_data.get("description") or description)
        status = _safe_form_text(form_data.get("status") or status)
        project_type = _safe_form_text(form_data.get("project_type") or project_type)
        project_priority = _safe_form_text(
            form_data.get("project_priority") or project_priority
        )
        customer_id = _safe_form_text(form_data.get("customer_id") or customer_id)
        start_date = _safe_form_text(form_data.get("start_date") or start_date)
        end_date = _safe_form_text(form_data.get("end_date") or end_date)
        budget_amount = _safe_form_text(form_data.get("budget_amount") or budget_amount)
        percent_complete = _safe_form_text(
            form_data.get("percent_complete") or percent_complete
        )
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return RedirectResponse(
            url="/projects?success=Record+updated+successfully", status_code=303
        )

    project.project_name = project_name.strip()
    project.description = description.strip() if description else None
    project.status = ProjectStatus(status)
    project.project_type = (
        ProjectType(project_type) if project_type else ProjectType.INTERNAL
    )
    project.project_priority = (
        ProjectPriority(project_priority)
        if project_priority
        else ProjectPriority.MEDIUM
    )
    project.customer_id = coerce_uuid(customer_id) if customer_id else None
    project.start_date = _safe_date(start_date)
    project.end_date = _safe_date(end_date)
    project.budget_amount = _safe_decimal(budget_amount)
    project.percent_complete = _safe_decimal(percent_complete, Decimal("0"))

    upload_files = _normalize_uploads(files)
    if upload_files:
        from app.services.pm.attachment import project_attachment_service

        for file in upload_files:
            project_attachment_service.save_file(
                db,
                organization_id=org_id,
                entity_type="PROJECT",
                entity_id=project.project_id,
                filename=file.filename or "unnamed",
                file_data=file.file,
                content_type=file.content_type or "application/octet-stream",
                uploaded_by_id=coerce_uuid(auth.user_id),
            )

    db.commit()

    return RedirectResponse(
        url=_project_url(project) + "?saved=1",
        status_code=303,
    )


@router.post("/{project_id}/delete", response_class=RedirectResponse)
def delete_project(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Delete a project (soft delete by setting status to CANCELLED)."""
    from app.models.finance.core_org.project import ProjectStatus

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if project:
        project.status = ProjectStatus.CANCELLED
        db.commit()

    return RedirectResponse(
        url="/projects?success=Record+deleted+successfully", status_code=303
    )


# ============================================================================
# Tasks
# ============================================================================


@router.get("/{project_id}/tasks", response_class=HTMLResponse)
def project_tasks(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    status: str | None = None,
    priority: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Project tasks list page."""
    from app.models.pm import TaskPriority, TaskStatus

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)

    status_enum = None
    if status:
        try:
            status_enum = TaskStatus(status)
        except ValueError:
            pass

    priority_enum = None
    if priority:
        try:
            priority_enum = TaskPriority(priority)
        except ValueError:
            pass

    # Get more tasks for tree view (we need all to show hierarchy properly)
    per_page = 100  # Higher limit for tree view
    result = services["task"].list_tasks(
        project_id=project.project_id,
        status=status_enum,
        priority=priority_enum,
        params=PaginationParams(offset=(page - 1) * per_page, limit=per_page),
    )

    # Compute subtask counts for each parent task
    tasks = result.items
    subtask_counts = {}
    for task in tasks:
        if task.parent_task_id:
            parent_id = str(task.parent_task_id)
            subtask_counts[parent_id] = subtask_counts.get(parent_id, 0) + 1

    # Attach subtask_count to each task
    for task in tasks:
        task.subtask_count = subtask_counts.get(str(task.task_id), 0)

    employees = _get_employees(db, org_id)

    context = {
        "request": request,
        **base_context(request, auth, "Project Tasks", "tasks", db=db),
        "project": project,
        "tasks": tasks,
        "total": result.total,
        "page": page,
        "per_page": per_page,
        "total_pages": (result.total + per_page - 1) // per_page
        if result.total > 0
        else 1,
        "status_filter": status,
        "priority_filter": priority,
        "statuses": [s.value for s in TaskStatus],
        "priorities": [p.value for p in TaskPriority],
        "employees": employees,
        "view_mode": "tree",
    }

    return templates.TemplateResponse("projects/tasks/list.html", context)


@router.get("/{project_id}/tasks/{task_id}", response_class=HTMLResponse)
def task_detail(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Task detail page."""
    from app.services.pm.attachment import project_attachment_service
    from app.services.pm.comment import comment_service

    org_id = coerce_uuid(auth.organization_id)

    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)

    task = _resolve_task_ref(db, org_id, project.project_id, task_id)
    if not task:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Task not found"},
            status_code=404,
        )
    task_uuid = task.task_id
    _ensure_task_code(db, org_id, task)
    if task.task_code and task_id != task.task_code:
        db.commit()
        return RedirectResponse(
            url=_task_url(project, task),
            status_code=302,
        )

    # Get subtasks
    subtasks = services["task"].get_subtasks(task_uuid)

    # Get dependencies (what this task depends on)
    dependencies = services["task"].get_dependencies(task_uuid)

    # Get dependents (what depends on this task)
    dependents = services["task"].get_dependents(task_uuid)

    # Get time entries for this task
    time_entries = services["time"].list_entries(
        task_id=task_uuid,
        params=PaginationParams(offset=0, limit=10),
    )

    comments = comment_service.list_comments(
        db,
        organization_id=org_id,
        entity_type="TASK",
        entity_id=task_uuid,
        include_internal=True,
    )
    comment_links = comment_service.list_comment_attachments(
        db, [c.comment_id for c in comments]
    )
    comment_attachment_map = _build_pm_comment_attachment_map(comment_links)
    comment_attachment_ids = {link.attachment_id for link in comment_links}
    all_attachments = project_attachment_service.list_attachments(
        db, org_id, "TASK", task_uuid
    )
    task_attachments = [
        att
        for att in all_attachments
        if att.attachment_id not in comment_attachment_ids
    ]

    context = {
        "request": request,
        **base_context(request, auth, task.task_name, "tasks", db=db),
        "project": project,
        "task": task,
        "subtasks": subtasks,
        "dependencies": dependencies,
        "dependents": dependents,
        "time_entries": time_entries.items,
        "comments": comments,
        "comment_attachments": comment_attachment_map,
        "attachments": task_attachments,
    }

    return templates.TemplateResponse("projects/tasks/detail.html", context)


@router.post("/{project_id}/tasks/{task_id}/comments", response_class=RedirectResponse)
async def add_task_comment(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    content: str = Form(...),
    is_internal: bool = Form(default=False),
    files: list[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """Add a comment to a task."""
    from app.models.pm.comment import PMCommentAttachment
    from app.services.pm.attachment import project_attachment_service
    from app.services.pm.comment import comment_service

    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+saved+successfully", status_code=303
        )
    task = _resolve_task_ref(db, org_id, project.project_id, task_id)
    if not task:
        return RedirectResponse(
            url=f"/projects/{project.project_code}/tasks?saved=1", status_code=303
        )

    upload_files = _normalize_uploads(files)
    attachment_errors = []

    try:
        comment = comment_service.add_comment(
            db,
            organization_id=org_id,
            entity_type="TASK",
            entity_id=task.task_id,
            author_id=user_id,
            content=content,
            is_internal=is_internal,
        )

        for file in upload_files:
            attachment, error = project_attachment_service.save_file(
                db,
                organization_id=org_id,
                entity_type="TASK",
                entity_id=task.task_id,
                filename=file.filename or "unnamed",
                file_data=file.file,
                content_type=file.content_type or "application/octet-stream",
                uploaded_by_id=user_id,
            )
            if error or not attachment:
                attachment_errors.append(error or "Upload failed")
                continue
            db.add(
                PMCommentAttachment(
                    comment_id=comment.comment_id,
                    attachment_id=attachment.attachment_id,
                )
            )

        db.commit()
    except Exception:
        db.rollback()
        attachment_errors.append("Comment upload failed")

    base_url = (
        f"/projects/{project.project_code}/tasks/{task.task_code or task.task_id}"
    )
    if attachment_errors:
        base_url += "?warning=Some+attachments+failed+to+upload"
    return RedirectResponse(url=base_url + "#comments", status_code=303)


@router.post(
    "/{project_id}/tasks/{task_id}/comments/{comment_id}/delete",
    response_class=RedirectResponse,
)
def delete_task_comment(
    request: Request,
    project_id: str,
    task_id: str,
    comment_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Delete a task comment."""
    from app.services.pm.comment import comment_service

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+deleted+successfully", status_code=303
        )
    task = _resolve_task_ref(db, org_id, project.project_id, task_id)
    if not task:
        return RedirectResponse(
            url=f"/projects/{project.project_code}/tasks?saved=1", status_code=303
        )

    try:
        comment_service.delete_comment(db, org_id, coerce_uuid(comment_id))
        db.commit()
    except Exception:
        db.rollback()

    base_url = (
        f"/projects/{project.project_code}/tasks/{task.task_code or task.task_id}"
    )
    return RedirectResponse(url=base_url + "#comments", status_code=303)


@router.get("/{project_id}/tasks/{task_id}/attachments/{attachment_id}/download")
def download_task_attachment(
    request: Request,
    project_id: str,
    task_id: str,
    attachment_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Download a task attachment."""
    from app.services.pm.attachment import project_attachment_service

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    task = _resolve_task_ref(db, org_id, project.project_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    attachment = project_attachment_service.get_attachment(
        db, org_id, coerce_uuid(attachment_id)
    )
    if (
        not attachment
        or attachment.entity_type != "TASK"
        or attachment.entity_id != task.task_id
    ):
        raise HTTPException(status_code=404, detail="Attachment not found")

    file_path = project_attachment_service.get_file_path(
        db, org_id, coerce_uuid(attachment_id)
    )
    if not file_path:
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=str(file_path),
        filename=attachment.file_name,
        media_type=attachment.content_type,
    )


@router.post(
    "/{project_id}/tasks/{task_id}/attachments", response_class=RedirectResponse
)
async def upload_task_attachment(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    files: list[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """Upload attachments to a task."""
    from app.services.pm.attachment import project_attachment_service

    org_id = coerce_uuid(auth.organization_id)
    user_id = coerce_uuid(auth.user_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+saved+successfully", status_code=303
        )
    task = _resolve_task_ref(db, org_id, project.project_id, task_id)
    if not task:
        return RedirectResponse(
            url=f"/projects/{project.project_code}/tasks?saved=1", status_code=303
        )

    upload_files = _normalize_uploads(files)
    for file in upload_files:
        project_attachment_service.save_file(
            db,
            organization_id=org_id,
            entity_type="TASK",
            entity_id=task.task_id,
            filename=file.filename or "unnamed",
            file_data=file.file,
            content_type=file.content_type or "application/octet-stream",
            uploaded_by_id=user_id,
        )
    db.commit()

    base_url = (
        f"/projects/{project.project_code}/tasks/{task.task_code or task.task_id}"
    )
    return RedirectResponse(url=base_url + "#attachments", status_code=303)


@router.post(
    "/{project_id}/tasks/{task_id}/attachments/{attachment_id}/delete",
    response_class=RedirectResponse,
)
def delete_task_attachment(
    request: Request,
    project_id: str,
    task_id: str,
    attachment_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Delete a task attachment."""
    from app.services.pm.attachment import project_attachment_service

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+deleted+successfully", status_code=303
        )
    task = _resolve_task_ref(db, org_id, project.project_id, task_id)
    if not task:
        return RedirectResponse(
            url=f"/projects/{project.project_code}/tasks?saved=1", status_code=303
        )

    project_attachment_service.delete_attachment(db, org_id, coerce_uuid(attachment_id))
    db.commit()

    base_url = (
        f"/projects/{project.project_code}/tasks/{task.task_code or task.task_id}"
    )
    return RedirectResponse(url=base_url + "#attachments", status_code=303)


@router.get("/{project_id}/tasks/new", response_class=HTMLResponse)
def new_task_form(
    request: Request,
    project_id: str,
    parent_task_id: str | None = None,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """New task form page."""
    from app.models.pm import TaskPriority, TaskStatus

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)
    available_tasks = (
        services["task"]
        .list_tasks(
            project_id=project.project_id,
            params=PaginationParams(offset=0, limit=1000),
        )
        .items
    )

    employees = _get_employees(db, org_id)
    tickets = _get_tickets(db, org_id)

    context = {
        "request": request,
        **base_context(request, auth, "New Task", "tasks", db=db),
        "project": project,
        "task": None,
        "parent_task_id": parent_task_id,
        "available_parent_tasks": available_tasks,
        "team_members": employees,
        "tickets": tickets,
        "statuses": [s.value for s in TaskStatus],
        "priorities": [p.value for p in TaskPriority],
    }

    return templates.TemplateResponse("projects/tasks/form.html", context)


@router.post("/{project_id}/tasks", response_class=RedirectResponse)
def create_task(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    task_name: str = Form(...),
    task_code: str = Form(default=""),
    description: str = Form(default=""),
    status: str = Form(default="OPEN"),
    priority: str = Form(default="MEDIUM"),
    parent_task_id: str = Form(default=""),
    assigned_to_id: str = Form(default=""),
    ticket_id: str = Form(default=""),
    start_date: str = Form(default=""),
    due_date: str = Form(default=""),
    estimated_hours: str = Form(default=""),
    files: list[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """Create a new task."""
    from app.models.finance.core_config import SequenceType
    from app.models.pm import TaskPriority, TaskStatus
    from app.services.finance.common.numbering import SyncNumberingService

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+created+successfully", status_code=303
        )
    services = _get_services(db, org_id)

    # Generate task code if not provided
    task_code_value = task_code.strip() if task_code else ""
    if not task_code_value:
        numbering_service = SyncNumberingService(db)
        task_code_value = numbering_service.generate_next_number(
            organization_id=org_id,
            sequence_type=SequenceType.TASK,
        )

    task = services["task"].create_task(
        {
            "project_id": project.project_id,
            "task_code": task_code_value,
            "task_name": task_name.strip(),
            "description": description.strip() if description else None,
            "status": TaskStatus(status),
            "priority": TaskPriority(priority),
            "parent_task_id": coerce_uuid(parent_task_id) if parent_task_id else None,
            "assigned_to_id": coerce_uuid(assigned_to_id) if assigned_to_id else None,
            "ticket_id": coerce_uuid(ticket_id) if ticket_id else None,
            "start_date": _safe_date(start_date),
            "due_date": _safe_date(due_date),
            "estimated_hours": _safe_decimal(estimated_hours),
        }
    )

    upload_files = _normalize_uploads(files)
    if upload_files:
        from app.services.pm.attachment import project_attachment_service

        for file in upload_files:
            project_attachment_service.save_file(
                db,
                organization_id=org_id,
                entity_type="TASK",
                entity_id=task.task_id,
                filename=file.filename or "unnamed",
                file_data=file.file,
                content_type=file.content_type or "application/octet-stream",
                uploaded_by_id=coerce_uuid(auth.user_id),
            )

    db.commit()

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks/{task.task_code}?saved=1",
        status_code=303,
    )


@router.get("/{project_id}/tasks/{task_id}/edit", response_class=HTMLResponse)
def edit_task_form(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Edit task form page."""
    from app.models.pm import TaskPriority, TaskStatus

    org_id = coerce_uuid(auth.organization_id)

    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)

    task = _resolve_task_ref(db, org_id, project.project_id, task_id)
    if not task:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Task not found"},
            status_code=404,
        )
    task_uuid = task.task_id
    _ensure_task_code(db, org_id, task)
    if task.task_code and task_id != task.task_code:
        db.commit()
        return RedirectResponse(
            url=f"/projects/{project.project_code}/tasks/{task.task_code}/edit",
            status_code=302,
        )

    available_tasks = [
        t
        for t in services["task"]
        .list_tasks(
            project_id=project.project_id,
            params=PaginationParams(offset=0, limit=1000),
        )
        .items
        if t.task_id != task_uuid
    ]

    employees = _get_employees(db, org_id)
    tickets = _get_tickets(db, org_id)

    # Get current dependencies for this task
    dependencies = services["task"].get_dependencies(task_uuid)

    context = {
        "request": request,
        **base_context(request, auth, "Edit Task", "tasks", db=db),
        "project": project,
        "task": task,
        "available_parent_tasks": available_tasks,
        "team_members": employees,
        "tickets": tickets,
        "dependencies": dependencies,
        "statuses": [s.value for s in TaskStatus],
        "priorities": [p.value for p in TaskPriority],
    }

    return templates.TemplateResponse("projects/tasks/form.html", context)


@router.post("/{project_id}/tasks/{task_id}", response_class=RedirectResponse)
def update_task(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    task_name: str | None = Form(default=None),
    description: str = Form(default=""),
    status: str | None = Form(default=None),
    priority: str | None = Form(default=None),
    parent_task_id: str = Form(default=""),
    assigned_to_id: str = Form(default=""),
    ticket_id: str = Form(default=""),
    start_date: str = Form(default=""),
    due_date: str = Form(default=""),
    estimated_hours: str = Form(default=""),
    actual_hours: str = Form(default=""),
    progress_percent: str = Form(default="0"),
    files: list[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """Update an existing task."""
    from app.models.pm import TaskPriority, TaskStatus

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+updated+successfully", status_code=303
        )
    services = _get_services(db, org_id)
    task = _resolve_task_ref(db, org_id, project.project_id, task_id)
    if not task:
        return RedirectResponse(
            url=f"/projects/{project.project_code}/tasks?saved=1", status_code=303
        )
    task_uuid = task.task_id
    _ensure_task_code(db, org_id, task)

    form_data = getattr(request.state, "csrf_form", None)
    if form_data:
        assigned_to_id = (
            form_data.get("assigned_to_id") or assigned_to_id or ""
        ).strip()
        parent_task_id = (
            form_data.get("parent_task_id") or parent_task_id or ""
        ).strip()
        ticket_id = (form_data.get("ticket_id") or ticket_id or "").strip()
        task_name = (form_data.get("task_name") or task_name or "").strip() or None
        status = form_data.get("status") or status or None
        priority = form_data.get("priority") or priority or None

    task_name_value = task_name.strip() if task_name else task.task_name
    status_value = status or task.status.value
    priority_value = priority or task.priority.value

    try:
        services["task"].update_task(
            task_uuid,
            {
                "task_name": task_name_value,
                "description": description.strip() if description else None,
                "status": TaskStatus(status_value),
                "priority": TaskPriority(priority_value),
                "parent_task_id": coerce_uuid(parent_task_id)
                if parent_task_id
                else None,
                "assigned_to_id": coerce_uuid(assigned_to_id)
                if assigned_to_id
                else None,
                "ticket_id": coerce_uuid(ticket_id) if ticket_id else None,
                "start_date": _safe_date(start_date),
                "due_date": _safe_date(due_date),
                "estimated_hours": _safe_decimal(estimated_hours),
                "actual_hours": _safe_decimal(actual_hours, Decimal("0")),
                "progress_percent": int(progress_percent)
                if progress_percent and progress_percent.isdigit()
                else 0,
            },
        )

        upload_files = _normalize_uploads(files)
        if upload_files:
            from app.services.pm.attachment import project_attachment_service

            for file in upload_files:
                project_attachment_service.save_file(
                    db,
                    organization_id=org_id,
                    entity_type="TASK",
                    entity_id=task_uuid,
                    filename=file.filename or "unnamed",
                    file_data=file.file,
                    content_type=file.content_type or "application/octet-stream",
                    uploaded_by_id=coerce_uuid(auth.user_id),
                )
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks/{task.task_code or task.task_id}?saved=1",
        status_code=303,
    )


@router.post("/{project_id}/tasks/{task_id}/delete", response_class=RedirectResponse)
def delete_task(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Delete a task (soft delete)."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+deleted+successfully", status_code=303
        )
    services = _get_services(db, org_id)
    task = _resolve_task_ref(db, org_id, project.project_id, task_id)
    if not task:
        return RedirectResponse(
            url="/projects?success=Record+deleted+successfully", status_code=303
        )
    task_uuid = task.task_id
    _ensure_task_code(db, org_id, task)

    try:
        services["task"].delete_task(task_uuid)
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks?saved=1",
        status_code=303,
    )


@router.post("/{project_id}/tasks/{task_id}/start", response_class=RedirectResponse)
def start_task(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Start a task (transition from OPEN to IN_PROGRESS)."""
    from app.models.pm import TaskStatus

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+saved+successfully", status_code=303
        )
    services = _get_services(db, org_id)
    task = _resolve_task_ref(db, org_id, project.project_id, task_id)
    if not task:
        return RedirectResponse(
            url=f"/projects/{project.project_code}/tasks?saved=1", status_code=303
        )

    try:
        services["task"].update_task(task.task_id, {"status": TaskStatus.IN_PROGRESS})
        db.commit()
    except (NotFoundError, ValidationError):
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks/{task.task_code}?saved=1",
        status_code=303,
    )


@router.post("/{project_id}/tasks/{task_id}/complete", response_class=RedirectResponse)
def complete_task(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Complete a task (transition to COMPLETED)."""
    from app.models.pm import TaskStatus

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+saved+successfully", status_code=303
        )
    services = _get_services(db, org_id)
    task = _resolve_task_ref(db, org_id, project.project_id, task_id)
    if not task:
        return RedirectResponse(
            url=f"/projects/{project.project_code}/tasks?saved=1", status_code=303
        )

    try:
        services["task"].update_task(
            task.task_id,
            {
                "status": TaskStatus.COMPLETED,
                "progress_percent": 100,
            },
        )
        db.commit()
    except (NotFoundError, ValidationError):
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks/{task.task_code}?saved=1",
        status_code=303,
    )


# ============================================================================
# Task Dependencies
# ============================================================================


@router.post(
    "/{project_id}/tasks/{task_id}/dependencies", response_class=RedirectResponse
)
def add_task_dependency(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    depends_on_id: str = Form(...),
    dependency_type: str = Form(default="FINISH_TO_START"),
    lag_days: int = Form(default=0),
    db: Session = Depends(get_db),
):
    """Add a dependency to a task."""
    from app.models.pm import DependencyType

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+saved+successfully", status_code=303
        )

    task = _resolve_task_ref(db, org_id, project.project_id, task_id)
    if not task:
        return RedirectResponse(
            url="/projects?success=Record+saved+successfully", status_code=303
        )
    task_uuid = task.task_id
    depends_on_uuid = coerce_uuid(depends_on_id)
    services = _get_services(db, org_id)

    try:
        dep_type = DependencyType(dependency_type)
        services["task"].add_dependency(
            task_id=task_uuid,
            depends_on_id=depends_on_uuid,
            dependency_type=dep_type,
            lag_days=lag_days,
        )
        db.commit()
    except (NotFoundError, ValidationError):
        # Redirect back with error (could flash message in future)
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks/{task.task_code}/edit?saved=1",
        status_code=303,
    )


@router.post(
    "/{project_id}/tasks/{task_id}/dependencies/{depends_on_id}/remove",
    response_class=RedirectResponse,
)
def remove_task_dependency(
    request: Request,
    project_id: str,
    task_id: str,
    depends_on_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Remove a dependency from a task."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+saved+successfully", status_code=303
        )

    task = _resolve_task_ref(db, org_id, project.project_id, task_id)
    if not task:
        return RedirectResponse(
            url="/projects?success=Record+saved+successfully", status_code=303
        )
    task_uuid = task.task_id
    depends_on_uuid = coerce_uuid(depends_on_id)
    services = _get_services(db, org_id)

    try:
        services["task"].remove_dependency(task_uuid, depends_on_uuid)
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks/{task.task_code}/edit?saved=1",
        status_code=303,
    )


# ============================================================================
# Bulk Task Operations
# ============================================================================


@router.post("/{project_id}/tasks/bulk-status", response_class=RedirectResponse)
def bulk_update_task_status(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    task_ids: str = Form(...),
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    """Update status for multiple tasks."""
    from app.models.pm import TaskStatus

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+updated+successfully", status_code=303
        )

    if not status:
        return RedirectResponse(
            url=f"/projects/{project.project_code}/tasks?saved=1",
            status_code=303,
        )

    services = _get_services(db, org_id)

    try:
        status_enum = TaskStatus(status)
    except ValueError:
        return RedirectResponse(
            url=f"/projects/{project.project_code}/tasks",
            status_code=303,
        )

    # Parse comma-separated task IDs
    for task_id in task_ids.split(","):
        task_id = task_id.strip()
        if task_id:
            try:
                task_uuid = coerce_uuid(task_id)
                services["task"].update_task(task_uuid, {"status": status_enum})
            except Exception:
                logger.exception("bulk_update_tasks: failed for task_id=%s", task_id)
                continue

    db.commit()

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks?saved=1",
        status_code=303,
    )


@router.post("/{project_id}/tasks/bulk-delete", response_class=RedirectResponse)
def bulk_delete_tasks(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    task_ids: str = Form(...),
    db: Session = Depends(get_db),
):
    """Delete multiple tasks (soft delete)."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+deleted+successfully", status_code=303
        )

    services = _get_services(db, org_id)

    # Parse comma-separated task IDs
    for task_id in task_ids.split(","):
        task_id = task_id.strip()
        if task_id:
            try:
                task_uuid = coerce_uuid(task_id)
                services["task"].delete_task(task_uuid)
            except Exception:
                logger.exception("bulk_delete_tasks: failed for task_id=%s", task_id)
                continue

    db.commit()

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks?saved=1",
        status_code=303,
    )


# ============================================================================
# Gantt Chart
# ============================================================================


@router.get("/{project_id}/gantt", response_class=HTMLResponse)
def project_gantt(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Project Gantt chart page."""

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)

    try:
        gantt_data = services["gantt"].get_gantt_data(project.project_id)
    except NotFoundError:
        gantt_data = None

    context = {
        "request": request,
        **base_context(request, auth, "Gantt Chart", "gantt", db=db),
        "project": project,
        "gantt_data": gantt_data,
    }

    return templates.TemplateResponse("projects/gantt.html", context)


# ============================================================================
# Team/Resources
# ============================================================================


@router.get("/{project_id}/team", response_class=HTMLResponse)
def project_team(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Project team management page."""

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)

    allocations = services["resource"].get_project_team(project.project_id)
    employees = _get_employees(db, org_id)

    context = {
        "request": request,
        **base_context(request, auth, "Project Team", "team", db=db),
        "project": project,
        "allocations": allocations,
        "employees": employees,
    }

    return templates.TemplateResponse("projects/team.html", context)


@router.post("/{project_id}/team", response_class=RedirectResponse)
def create_resource_allocation(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    employee_id: str = Form(...),
    role_on_project: str = Form(default=""),
    allocation_percent: str = Form(default="100"),
    start_date: str = Form(...),
    end_date: str = Form(default=""),
    cost_rate_per_hour: str = Form(default=""),
    billing_rate_per_hour: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """Add a team member to the project."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+created+successfully", status_code=303
        )
    services = _get_services(db, org_id)

    parsed_start = _safe_date(start_date)
    if not parsed_start:
        return RedirectResponse(
            url=f"/projects/{project.project_code}/team?saved=1",
            status_code=303,
        )

    services["resource"].allocate_resource(
        {
            "project_id": project.project_id,
            "employee_id": coerce_uuid(employee_id),
            "role_on_project": role_on_project.strip() if role_on_project else None,
            "allocation_percent": _safe_decimal(allocation_percent, Decimal("100")),
            "start_date": parsed_start,
            "end_date": _safe_date(end_date),
            "cost_rate_per_hour": _safe_decimal(cost_rate_per_hour),
            "billing_rate_per_hour": _safe_decimal(billing_rate_per_hour),
        }
    )

    db.commit()

    return RedirectResponse(
        url=f"/projects/{project.project_code}/team?saved=1",
        status_code=303,
    )


@router.post("/{project_id}/team/{allocation_id}", response_class=RedirectResponse)
def update_resource_allocation(
    request: Request,
    project_id: str,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    role_on_project: str = Form(default=""),
    allocation_percent: str = Form(default="100"),
    start_date: str = Form(...),
    end_date: str = Form(default=""),
    cost_rate_per_hour: str = Form(default=""),
    billing_rate_per_hour: str = Form(default=""),
    is_active: str = Form(default="on"),
    db: Session = Depends(get_db),
):
    """Update a resource allocation."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+updated+successfully", status_code=303
        )
    allocation_uuid = coerce_uuid(allocation_id)
    services = _get_services(db, org_id)

    try:
        services["resource"].update_allocation(
            allocation_uuid,
            {
                "role_on_project": role_on_project.strip() if role_on_project else None,
                "allocation_percent": _safe_decimal(allocation_percent, Decimal("100")),
                "end_date": _safe_date(end_date),
                "cost_rate_per_hour": _safe_decimal(cost_rate_per_hour),
                "billing_rate_per_hour": _safe_decimal(billing_rate_per_hour),
                "is_active": is_active == "on",
            },
        )
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/team?saved=1",
        status_code=303,
    )


@router.post("/{project_id}/team/{allocation_id}/end", response_class=RedirectResponse)
def end_resource_allocation(
    request: Request,
    project_id: str,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    end_date: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """End a resource allocation."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+saved+successfully", status_code=303
        )
    allocation_uuid = coerce_uuid(allocation_id)
    services = _get_services(db, org_id)

    try:
        end_dt = date.fromisoformat(end_date) if end_date else date.today()
        services["resource"].end_allocation(allocation_uuid, end_dt)
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/team?saved=1",
        status_code=303,
    )


@router.post(
    "/{project_id}/team/{allocation_id}/delete", response_class=RedirectResponse
)
def delete_resource_allocation(
    request: Request,
    project_id: str,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Delete a resource allocation."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+deleted+successfully", status_code=303
        )
    allocation_uuid = coerce_uuid(allocation_id)
    services = _get_services(db, org_id)

    try:
        services["resource"].delete_allocation(allocation_uuid)
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/team?saved=1",
        status_code=303,
    )


# ============================================================================
# Resource Utilization Report
# ============================================================================


@router.get("/reports/utilization", response_class=HTMLResponse)
def resource_utilization_report(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    start_date: str | None = None,
    end_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Resource utilization report across all projects."""
    from datetime import timedelta

    org_id = coerce_uuid(auth.organization_id)
    services = _get_services(db, org_id)

    # Default to current month
    today = date.today()
    if start_date:
        try:
            period_start = date.fromisoformat(start_date)
        except ValueError:
            period_start = today.replace(day=1)
    else:
        period_start = today.replace(day=1)

    if end_date:
        try:
            period_end = date.fromisoformat(end_date)
        except ValueError:
            # End of month
            next_month = period_start.replace(day=28) + timedelta(days=4)
            period_end = next_month.replace(day=1) - timedelta(days=1)
    else:
        next_month = period_start.replace(day=28) + timedelta(days=4)
        period_end = next_month.replace(day=1) - timedelta(days=1)

    # Get all employees with active allocations
    employees = _get_employees(db, org_id)

    utilization_data = []
    total_utilization = Decimal("0")

    for emp in employees:
        try:
            util = services["resource"].get_utilization(
                emp.employee_id, period_start, period_end
            )
            if util["total_allocation_percent"] > 0 or util["hours_logged"] > 0:
                utilization_data.append(
                    {
                        "employee_id": emp.employee_id,
                        "employee_name": emp.full_name,
                        "hours_logged": util["hours_logged"],
                        "expected_hours": util["expected_hours"],
                        "utilization_percent": util["utilization_percent"],
                        "total_allocation_percent": util["total_allocation_percent"],
                        "allocations": util["project_allocations"],
                    }
                )
                total_utilization += util["utilization_percent"]
        except Exception:
            logger.exception(
                "utilization_by_employee: failed for employee_id=%s",
                emp.employee_id,
            )
            continue

    # Calculate averages and flags
    avg_utilization = (
        total_utilization / len(utilization_data) if utilization_data else Decimal("0")
    )
    over_allocated = [
        d for d in utilization_data if d["total_allocation_percent"] > 100
    ]
    under_utilized = [d for d in utilization_data if d["utilization_percent"] < 50]

    # Sort by utilization descending
    utilization_data.sort(key=lambda x: x["utilization_percent"], reverse=True)

    # Get project-level utilization
    projects = _get_projects(db, org_id)
    project_utilization = []
    for proj in projects:
        if proj.status and proj.status.value in ("ACTIVE", "IN_PROGRESS"):
            try:
                proj_util = services["resource"].get_project_utilization(
                    proj.project_id
                )
                if proj_util["total_team_members"] > 0:
                    project_utilization.append(
                        {
                            "project_id": proj.project_id,
                            "project_code": proj.project_code,
                            "project_name": proj.project_name,
                            "team_size": proj_util["total_team_members"],
                            "avg_allocation": proj_util["average_allocation"],
                            "total_hours": proj_util["total_hours_logged"],
                            "billable_percent": proj_util["billable_percent"],
                        }
                    )
            except Exception:
                logger.exception(
                    "utilization_by_project: failed for project_id=%s",
                    proj.project_id,
                )
                continue

    context = {
        "request": request,
        **base_context(request, auth, "Resource Utilization", "utilization", db=db),
        "period_start": period_start,
        "period_end": period_end,
        "utilization_data": utilization_data,
        "avg_utilization": avg_utilization,
        "over_allocated": over_allocated,
        "under_utilized": under_utilized,
        "team_members": employees,
        "project_utilization": project_utilization,
    }

    return templates.TemplateResponse("projects/utilization.html", context)


# ============================================================================
# Milestones
# ============================================================================


@router.get("/{project_id}/milestones", response_class=HTMLResponse)
def project_milestones(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Project milestones page."""

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)

    milestones = services["milestone"].get_project_milestones(project.project_id)

    context = {
        "request": request,
        **base_context(request, auth, "Milestones", "milestones", db=db),
        "project": project,
        "milestones": milestones,
    }

    return templates.TemplateResponse("projects/milestones.html", context)


@router.post("/{project_id}/milestones", response_class=RedirectResponse)
def create_milestone(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    name: str = Form(...),
    description: str = Form(default=""),
    target_date: str = Form(...),
    linked_task_id: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """Create a new milestone."""
    import uuid as uuid_mod

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+created+successfully", status_code=303
        )
    services = _get_services(db, org_id)

    # Generate milestone code
    milestone_code = f"MS-{str(uuid_mod.uuid4())[:8].upper()}"

    services["milestone"].create_milestone(
        {
            "project_id": project.project_id,
            "milestone_code": milestone_code,
            "milestone_name": name.strip(),
            "description": description.strip() if description else None,
            "target_date": date.fromisoformat(target_date),
            "linked_task_id": coerce_uuid(linked_task_id) if linked_task_id else None,
        }
    )

    db.commit()

    return RedirectResponse(
        url=f"/projects/{project.project_code}/milestones?saved=1",
        status_code=303,
    )


@router.post("/{project_id}/milestones/{milestone_id}", response_class=RedirectResponse)
def update_milestone(
    request: Request,
    project_id: str,
    milestone_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    name: str = Form(...),
    description: str = Form(default=""),
    target_date: str = Form(...),
    status: str = Form(...),
    actual_date: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """Update a milestone."""

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+updated+successfully", status_code=303
        )
    milestone_uuid = coerce_uuid(milestone_id)
    services = _get_services(db, org_id)

    try:
        # Note: update_milestone doesn't accept status change - only the fields defined
        # If status changes needed, use achieve_milestone for ACHIEVED
        services["milestone"].update_milestone(
            milestone_uuid,
            {
                "milestone_name": name.strip(),
                "description": description.strip() if description else None,
                "target_date": date.fromisoformat(target_date),
            },
        )
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/milestones?saved=1",
        status_code=303,
    )


@router.post(
    "/{project_id}/milestones/{milestone_id}/achieve", response_class=RedirectResponse
)
def achieve_milestone(
    request: Request,
    project_id: str,
    milestone_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Mark a milestone as achieved."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+saved+successfully", status_code=303
        )
    milestone_uuid = coerce_uuid(milestone_id)
    services = _get_services(db, org_id)

    try:
        services["milestone"].achieve_milestone(milestone_uuid)
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/milestones?saved=1",
        status_code=303,
    )


@router.post(
    "/{project_id}/milestones/{milestone_id}/delete", response_class=RedirectResponse
)
def delete_milestone(
    request: Request,
    project_id: str,
    milestone_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Delete a milestone."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+deleted+successfully", status_code=303
        )
    milestone_uuid = coerce_uuid(milestone_id)
    services = _get_services(db, org_id)

    try:
        services["milestone"].delete_milestone(milestone_uuid)
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/milestones?saved=1",
        status_code=303,
    )


# ============================================================================
# Time Tracking
# ============================================================================


@router.get("/{project_id}/time", response_class=HTMLResponse)
def project_time_entries(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    page: int = Query(default=1, ge=1),
    start_date: str | None = None,
    end_date: str | None = None,
    billable: str | None = None,
    billing_status: str | None = None,
    db: Session = Depends(get_db),
):
    """Project time entries page."""
    from app.models.pm import BillingStatus

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)

    # Parse filter parameters
    is_billable = None
    if billable == "true":
        is_billable = True
    elif billable == "false":
        is_billable = False

    billing_status_enum = None
    if billing_status:
        try:
            billing_status_enum = BillingStatus(billing_status)
        except ValueError:
            pass

    start_date_parsed = None
    if start_date:
        try:
            start_date_parsed = date.fromisoformat(start_date)
        except ValueError:
            pass

    end_date_parsed = None
    if end_date:
        try:
            end_date_parsed = date.fromisoformat(end_date)
        except ValueError:
            pass

    per_page = 20
    result = services["time"].list_entries(
        project_id=project.project_id,
        start_date=start_date_parsed,
        end_date=end_date_parsed,
        is_billable=is_billable,
        billing_status=billing_status_enum,
        params=PaginationParams(offset=(page - 1) * per_page, limit=per_page),
    )

    time_summary = services["time"].get_project_time_summary(project.project_id)

    # Get tasks for the dropdown in the time entry form
    tasks = (
        services["task"]
        .list_tasks(
            project_id=project.project_id,
            params=PaginationParams(offset=0, limit=1000),
        )
        .items
    )

    employees = _get_employees(db, org_id)

    context = {
        "request": request,
        **base_context(request, auth, "Time Entries", "time", db=db),
        "project": project,
        "entries": result.items,
        "total": result.total,
        "page": page,
        "per_page": per_page,
        "total_pages": (result.total + per_page - 1) // per_page
        if result.total > 0
        else 1,
        "time_summary": time_summary,
        "tasks": tasks,
        "employees": employees,
        "start_date": start_date,
        "end_date": end_date,
        "billable_filter": billable,
        "billing_status_filter": billing_status,
    }

    return templates.TemplateResponse("projects/time/list.html", context)


@router.get("/{project_id}/time/new", response_class=HTMLResponse)
def new_time_entry_form(
    request: Request,
    project_id: str,
    task_id: str | None = None,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """New time entry form page."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)

    # Get tasks for the dropdown
    tasks = (
        services["task"]
        .list_tasks(
            project_id=project.project_id,
            params=PaginationParams(offset=0, limit=1000),
        )
        .items
    )

    employees = _get_employees(db, org_id)

    context = {
        "request": request,
        **base_context(request, auth, "Log Time", "time", db=db),
        "project": project,
        "entry": None,
        "tasks": tasks,
        "employees": employees,
        "preselected_task_id": task_id,
        "today": date.today(),
    }

    return templates.TemplateResponse("projects/time/form.html", context)


@router.post("/{project_id}/time", response_class=RedirectResponse)
def create_time_entry(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    task_id: str = Form(default=""),
    employee_id: str = Form(...),
    entry_date: str = Form(...),
    hours: str = Form(...),
    description: str = Form(default=""),
    is_billable: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """Log a time entry."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+created+successfully", status_code=303
        )
    services = _get_services(db, org_id)

    parsed_date = _safe_date(entry_date)
    parsed_hours = _safe_decimal(hours)
    if not parsed_date or not parsed_hours:
        return RedirectResponse(
            url=f"/projects/{project.project_code}/time?saved=1",
            status_code=303,
        )

    services["time"].log_time(
        {
            "project_id": project.project_id,
            "task_id": coerce_uuid(task_id) if task_id else None,
            "employee_id": coerce_uuid(employee_id),
            "entry_date": parsed_date,
            "hours": parsed_hours,
            "description": description.strip() if description else None,
            "is_billable": is_billable == "on",
        }
    )

    db.commit()

    return RedirectResponse(
        url=f"/projects/{project.project_code}/time?saved=1",
        status_code=303,
    )


@router.get("/{project_id}/time/{entry_id}/edit", response_class=HTMLResponse)
def edit_time_entry_form(
    request: Request,
    project_id: str,
    entry_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Edit time entry form page."""
    from sqlalchemy import select

    from app.models.pm import BillingStatus, TimeEntry

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    entry_uuid = coerce_uuid(entry_id)
    entry = db.scalar(
        select(TimeEntry).where(
            TimeEntry.entry_id == entry_uuid,
            TimeEntry.organization_id == org_id,
        )
    )

    if not entry:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Time entry not found"},
            status_code=404,
        )

    # Don't allow editing billed entries
    if entry.billing_status == BillingStatus.BILLED:
        return RedirectResponse(
            url=f"/projects/{project.project_code}/time?saved=1",
            status_code=303,
        )

    services = _get_services(db, org_id)

    tasks = (
        services["task"]
        .list_tasks(
            project_id=project.project_id,
            params=PaginationParams(offset=0, limit=1000),
        )
        .items
    )

    employees = _get_employees(db, org_id)

    context = {
        "request": request,
        **base_context(request, auth, "Edit Time Entry", "time", db=db),
        "project": project,
        "entry": entry,
        "tasks": tasks,
        "employees": employees,
        "preselected_task_id": None,
    }

    return templates.TemplateResponse("projects/time/form.html", context)


@router.post("/{project_id}/time/{entry_id}", response_class=RedirectResponse)
def update_time_entry(
    request: Request,
    project_id: str,
    entry_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    task_id: str = Form(default=""),
    entry_date: str = Form(...),
    hours: str = Form(...),
    description: str = Form(default=""),
    is_billable: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """Update a time entry."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+updated+successfully", status_code=303
        )
    entry_uuid = coerce_uuid(entry_id)
    services = _get_services(db, org_id)

    parsed_date = _safe_date(entry_date)
    parsed_hours = _safe_decimal(hours)
    if not parsed_date or not parsed_hours:
        return RedirectResponse(
            url=f"/projects/{project.project_code}/time?saved=1",
            status_code=303,
        )

    try:
        services["time"].update_entry(
            entry_uuid,
            {
                "task_id": coerce_uuid(task_id) if task_id else None,
                "entry_date": parsed_date,
                "hours": parsed_hours,
                "description": description.strip() if description else None,
                "is_billable": is_billable == "on",
            },
        )
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/time?saved=1",
        status_code=303,
    )


@router.post("/{project_id}/time/{entry_id}/delete", response_class=RedirectResponse)
def delete_time_entry(
    request: Request,
    project_id: str,
    entry_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Delete a time entry."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+deleted+successfully", status_code=303
        )
    entry_uuid = coerce_uuid(entry_id)
    services = _get_services(db, org_id)

    try:
        services["time"].delete_entry(entry_uuid)
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/time?saved=1",
        status_code=303,
    )


@router.post("/{project_id}/time/{entry_id}/bill", response_class=RedirectResponse)
def bill_time_entry(
    request: Request,
    project_id: str,
    entry_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Mark a single time entry as billed."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+saved+successfully", status_code=303
        )

    entry_uuid = coerce_uuid(entry_id)
    services = _get_services(db, org_id)

    try:
        services["time"].mark_billed([entry_uuid])
        db.commit()
    except (NotFoundError, ValidationError):
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/time?saved=1",
        status_code=303,
    )


@router.post("/{project_id}/time/bulk-bill", response_class=RedirectResponse)
def bulk_bill_time_entries(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    entry_ids: str = Form(...),
    db: Session = Depends(get_db),
):
    """Mark multiple time entries as billed."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(
            url="/projects?success=Record+saved+successfully", status_code=303
        )

    services = _get_services(db, org_id)

    # Parse comma-separated entry IDs
    entry_uuids = []
    for entry_id in entry_ids.split(","):
        entry_id = entry_id.strip()
        if entry_id:
            try:
                entry_uuids.append(coerce_uuid(entry_id))
            except Exception:
                logger.exception(
                    "bulk_bill_time_entries: failed for entry_id=%s", entry_id
                )
                continue

    if entry_uuids:
        services["time"].mark_billed(entry_uuids)
        db.commit()

    return RedirectResponse(
        url=f"/projects/{project.project_code}/time?saved=1",
        status_code=303,
    )


# ============================================================================
# Timesheet
# ============================================================================


@router.get("/timesheet", response_class=HTMLResponse)
def employee_timesheet(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    week_start: str | None = None,
    db: Session = Depends(get_db),
):
    """Employee weekly timesheet page."""
    from datetime import timedelta

    org_id = coerce_uuid(auth.organization_id)
    today = date.today()

    # Determine week start (Monday)
    if week_start:
        try:
            ws = date.fromisoformat(week_start)
        except ValueError:
            ws = today
    else:
        ws = today

    # Adjust to Monday
    ws = ws - timedelta(days=ws.weekday())
    week_end = ws + timedelta(days=6)

    # Get employee for current user and their time entries
    services = _get_services(db, org_id)
    projects = _get_projects(db, org_id)

    # Try to get the current user's employee record
    entries = []
    week_total = Decimal("0")
    billable_total = Decimal("0")

    if auth.employee_id:
        try:
            result = services["time"].list_entries(
                employee_id=coerce_uuid(auth.employee_id),
                start_date=ws,
                end_date=week_end,
                params=PaginationParams(offset=0, limit=100),
            )
            entries = result.items
            for e in entries:
                week_total += e.hours or Decimal("0")
                if e.is_billable:
                    billable_total += e.hours or Decimal("0")
        except Exception:
            logger.exception("Ignored exception")

    context = {
        "request": request,
        **base_context(request, auth, "Timesheet", "time", db=db),
        "week_start": ws,
        "week_end": week_end,
        "today": today,
        "timedelta": timedelta,
        "projects": projects,
        "entries": entries,
        "week_total": week_total,
        "billable_total": billable_total,
    }

    return templates.TemplateResponse("projects/time/timesheet.html", context)


@router.post("/timesheet/log", response_class=RedirectResponse)
def log_timesheet_entry(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    entry_date: str = Form(...),
    project_id: str = Form(...),
    task_id: str = Form(default=""),
    hours: str = Form(...),
    description: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """Log a time entry from the employee timesheet view."""
    org_id = coerce_uuid(auth.organization_id)
    employee_id = coerce_uuid(auth.employee_id) if auth.employee_id else None
    if not org_id or not employee_id:
        return RedirectResponse(
            url="/projects/timesheet?success=Record+saved+successfully", status_code=303
        )

    services = _get_services(db, org_id)
    parsed_date = _safe_date(entry_date)
    parsed_hours = _safe_decimal(hours)
    if not parsed_date or not parsed_hours:
        return RedirectResponse(
            url="/projects/timesheet?success=Record+saved+successfully", status_code=303
        )

    services["time"].log_time(
        {
            "project_id": coerce_uuid(project_id),
            "task_id": coerce_uuid(task_id) if task_id else None,
            "employee_id": employee_id,
            "entry_date": parsed_date,
            "hours": parsed_hours,
            "description": description.strip() if description else None,
            "is_billable": False,
        }
    )
    db.commit()

    return RedirectResponse(
        url="/projects/timesheet?success=Record+saved+successfully", status_code=303
    )


# ============================================================================
# Expenses
# ============================================================================


@router.get("/{project_id}/expenses", response_class=HTMLResponse)
def project_expenses(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Project expenses page (read-only view)."""

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    from app.services.pm import ProjectExpenseService

    expense_svc = ProjectExpenseService(db, org_id)
    expenses = expense_svc.get_project_expenses(project.project_id)
    summary = expense_svc.get_expense_summary(project.project_id)

    context = {
        "request": request,
        **base_context(request, auth, "Project Expenses", "projects", db=db),
        "project": project,
        "expenses": expenses,
        "summary": summary,
    }

    return templates.TemplateResponse("projects/expenses.html", context)


# ============================================================================
# Attachments
# ============================================================================


@router.get("/{project_id}/attachments", response_class=HTMLResponse)
def project_attachments(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """List project attachments."""
    from app.services.pm.attachment import project_attachment_service

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    attachments = project_attachment_service.list_attachments(
        db, org_id, "PROJECT", project.project_id
    )

    context = {
        "request": request,
        **base_context(
            request, auth, f"{project.project_name} - Attachments", "projects", db=db
        ),
        "project": project,
        "attachments": attachments,
    }

    return templates.TemplateResponse("projects/attachments.html", context)


@router.post("/{project_id}/attachments", response_class=RedirectResponse)
async def upload_project_attachment(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Upload attachment to project."""
    from app.services.pm.attachment import project_attachment_service

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return RedirectResponse(
            url="/projects?error=Project+not+found", status_code=303
        )

    form = await request.form()
    file = form.get("file")
    description = form.get("description", "")

    from starlette.datastructures import UploadFile

    if not isinstance(file, UploadFile):
        return RedirectResponse(
            url=f"/projects/{project.project_code}/attachments?error=No+file+provided",
            status_code=303,
        )

    attachment, error = project_attachment_service.save_file(
        db=db,
        organization_id=org_id,
        entity_type="PROJECT",
        entity_id=project.project_id,
        filename=file.filename,
        file_data=file.file,
        content_type=file.content_type or "application/octet-stream",
        uploaded_by_id=auth.person_id,
        description=description if description else None,
    )

    if error:
        db.rollback()
        return RedirectResponse(
            url=(
                f"/projects/{project.project_code}/attachments"
                f"?error={(error or 'Failed to delete attachment').replace(' ', '+')}"
            ),
            status_code=303,
        )

    db.commit()

    return RedirectResponse(
        url=f"/projects/{project.project_code}/attachments?success=File+uploaded",
        status_code=303,
    )


@router.get("/{project_id}/attachments/{attachment_id}/download")
def download_project_attachment(
    request: Request,
    project_id: str,
    attachment_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Download project attachment."""
    from fastapi.responses import FileResponse

    from app.services.pm.attachment import project_attachment_service

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    attachment = project_attachment_service.get_attachment(
        db, org_id, coerce_uuid(attachment_id)
    )

    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    file_path = project_attachment_service.get_file_path(
        db, org_id, coerce_uuid(attachment_id)
    )

    if not file_path:
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=file_path,
        filename=attachment.file_name,
        media_type=attachment.content_type,
    )


@router.post(
    "/{project_id}/attachments/{attachment_id}/delete", response_class=RedirectResponse
)
def delete_project_attachment(
    request: Request,
    project_id: str,
    attachment_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Delete project attachment."""
    from app.services.pm.attachment import project_attachment_service

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return RedirectResponse(
            url="/projects?error=Project+not+found", status_code=303
        )

    success, error = project_attachment_service.delete_attachment(
        db, org_id, coerce_uuid(attachment_id)
    )

    if not success:
        db.rollback()
        return RedirectResponse(
            url=(
                f"/projects/{project.project_code}/attachments"
                f"?error={(error or 'Failed to delete attachment').replace(' ', '+')}"
            ),
            status_code=303,
        )

    db.commit()

    return RedirectResponse(
        url=f"/projects/{project.project_code}/attachments?success=Attachment+deleted",
        status_code=303,
    )


# =============================================================================
# Import/Export
# =============================================================================


@router.get("/import", response_class=HTMLResponse)
def project_import_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Project import dashboard page."""
    context = base_context(request, auth, "Project Import", "projects", db=db)
    context["entity_types"] = project_import_web_service.get_dashboard_entities()
    return templates.TemplateResponse(
        request, "projects/import_export/dashboard.html", context
    )


@router.get("/import/{entity_type}", response_class=HTMLResponse)
def project_import_form(
    request: Request,
    entity_type: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Project import form for a specific entity type."""
    entity_names = project_import_web_service.ENTITY_TYPES
    context = base_context(
        request,
        auth,
        f"Import {entity_names.get(entity_type, entity_type)}",
        "projects",
        db=db,
    )
    context["entity_type"] = entity_type
    context["entity_name"] = entity_names.get(entity_type, entity_type)
    context["columns"] = project_import_web_service.get_entity_columns(entity_type)
    return templates.TemplateResponse(
        request, "projects/import_export/import_form.html", context
    )


@router.post("/import/{entity_type}/preview", response_class=JSONResponse)
async def project_import_preview(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Preview project import with validation and column mapping."""
    try:
        result = await project_import_web_service.preview_import(
            db=db,
            organization_id=auth.organization_id,
            user_id=auth.person_id,
            entity_type=entity_type,
            file=file,
        )
        return JSONResponse(content=result)
    except ValueError as exc:
        return JSONResponse(content={"detail": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse(
            content={"detail": f"Preview failed: {str(exc)}"}, status_code=500
        )


@router.post("/import/{entity_type}", response_class=JSONResponse)
async def project_execute_import(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    skip_duplicates: str | None = Form(default=None),
    dry_run: str | None = Form(default=None),
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Execute project import operation (web route)."""
    try:
        skip_dups = skip_duplicates is not None and skip_duplicates.lower() in (
            "true",
            "1",
            "on",
            "",
        )
        is_dry_run = dry_run is not None and dry_run.lower() in ("true", "1", "on", "")

        result = await project_import_web_service.execute_import(
            db=db,
            organization_id=auth.organization_id,
            user_id=auth.person_id,
            entity_type=entity_type,
            file=file,
            skip_duplicates=skip_dups,
            dry_run=is_dry_run,
        )
        return JSONResponse(content=result)
    except ValueError as exc:
        return JSONResponse(content={"detail": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse(
            content={"detail": f"Import failed: {str(exc)}"}, status_code=500
        )
