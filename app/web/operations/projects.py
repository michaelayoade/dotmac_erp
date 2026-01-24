"""
Project Management Web Routes - Operations Module.

HTML template routes for project management including tasks, milestones,
resources, and time tracking.
"""

from typing import Optional

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.common import coerce_uuid, NotFoundError, ValidationError, PaginationParams
from app.templates import templates
from app.web.deps import (
    base_context,
    get_db,
    require_operations_access,
    WebAuthContext,
)

router = APIRouter(prefix="/projects", tags=["operations-projects-web"])


# ============================================================================
# Helper Functions
# ============================================================================


def _get_services(db: Session, org_id):
    """Get all PM services."""
    from app.services.pm import (
        DashboardService,
        GanttService,
        MilestoneService,
        ResourceService,
        TaskService,
        TimeEntryService,
    )
    from uuid import UUID

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
    from app.models.finance.core_org.project import Project
    from sqlalchemy import select

    stmt = select(Project).where(
        Project.organization_id == coerce_uuid(org_id)
    ).order_by(Project.project_name)
    return list(db.scalars(stmt).all())


def _get_employees(db: Session, org_id):
    """Get all employees for the organization."""
    from app.models.people.hr.employee import Employee
    from sqlalchemy import select

    stmt = select(Employee).where(
        Employee.organization_id == coerce_uuid(org_id)
    ).order_by(Employee.first_name)
    return list(db.scalars(stmt).all())


def _resolve_project_ref(db: Session, org_id, project_ref: str):
    """Resolve project by UUID or project_code."""
    from app.models.finance.core_org.project import Project
    from sqlalchemy import select

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
    return f"/operations/projects/{project.project_code or project.project_id}"


# ============================================================================
# Project List
# ============================================================================


@router.get("", response_class=HTMLResponse)
def list_projects(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Projects list page."""
    from app.models.finance.core_org.project import Project, ProjectStatus
    from sqlalchemy import select, func

    org_id = coerce_uuid(auth.organization_id)

    # Build query
    stmt = select(Project).where(
        Project.organization_id == org_id
    )

    if search:
        stmt = stmt.where(
            Project.project_name.ilike(f"%{search}%") |
            Project.project_code.ilike(f"%{search}%")
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
    }

    return templates.TemplateResponse("operations/projects/list.html", context)


# ============================================================================
# Project Dashboard
# ============================================================================


# ============================================================================
# Project Form (Create/Edit)
# ============================================================================


@router.get("/new", response_class=HTMLResponse)
def new_project_form(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """New project form page."""
    from app.models.finance.core_org.project import ProjectStatus

    context = {
        "request": request,
        **base_context(request, auth, "New Project", "projects", db=db),
        "project": None,
        "statuses": [s.value for s in ProjectStatus],
    }

    return templates.TemplateResponse("operations/projects/form.html", context)


@router.get("/{project_id}/edit", response_class=HTMLResponse)
def edit_project_form(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Edit project form page."""
    from app.models.finance.core_org.project import Project, ProjectStatus
    from sqlalchemy import select

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    context = {
        "request": request,
        **base_context(request, auth, "Edit Project", "projects", db=db),
        "project": project,
        "statuses": [s.value for s in ProjectStatus],
    }

    return templates.TemplateResponse("operations/projects/form.html", context)


@router.get("/{project_id}", response_class=HTMLResponse)
def project_dashboard(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Project dashboard/detail page."""
    from app.models.finance.core_org.project import Project
    from sqlalchemy import select

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

    context = {
        "request": request,
        **base_context(request, auth, project.project_name, "projects", db=db),
        "project": project,
        "dashboard": dashboard_data,
    }

    return templates.TemplateResponse("operations/projects/detail.html", context)


@router.post("", response_class=RedirectResponse)
@router.post("/", response_class=RedirectResponse)
def create_project(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    project_code: str = Form(...),
    project_name: str = Form(...),
    description: str = Form(default=""),
    status: str = Form(default="PLANNING"),
    start_date: str = Form(default=""),
    end_date: str = Form(default=""),
    budget_amount: str = Form(default=""),
    percent_complete: str = Form(default="0"),
    db: Session = Depends(get_db),
):
    """Create a new project."""
    from app.models.finance.core_org.project import Project, ProjectStatus

    org_id = coerce_uuid(auth.organization_id)

    project = Project(
        organization_id=org_id,
        project_code=project_code.strip(),
        project_name=project_name.strip(),
        description=description.strip() if description else None,
        status=ProjectStatus(status),
        start_date=date.fromisoformat(start_date) if start_date else None,
        end_date=date.fromisoformat(end_date) if end_date else None,
        budget_amount=Decimal(budget_amount) if budget_amount else None,
        percent_complete=Decimal(percent_complete) if percent_complete else Decimal("0"),
    )

    db.add(project)
    db.commit()

    return RedirectResponse(
        url=_project_url(project),
        status_code=303,
    )


@router.post("/{project_id}", response_class=RedirectResponse)
def update_project(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    project_name: str = Form(...),
    description: str = Form(default=""),
    status: str = Form(...),
    start_date: str = Form(default=""),
    end_date: str = Form(default=""),
    budget_amount: str = Form(default=""),
    percent_complete: str = Form(default="0"),
    db: Session = Depends(get_db),
):
    """Update an existing project."""
    from app.models.finance.core_org.project import Project, ProjectStatus
    from sqlalchemy import select

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return RedirectResponse(url="/operations/projects", status_code=303)

    project.project_name = project_name.strip()
    project.description = description.strip() if description else None
    project.status = ProjectStatus(status)
    project.start_date = date.fromisoformat(start_date) if start_date else None
    project.end_date = date.fromisoformat(end_date) if end_date else None
    project.budget_amount = Decimal(budget_amount) if budget_amount else None
    project.percent_complete = Decimal(percent_complete) if percent_complete else Decimal("0")

    db.commit()

    return RedirectResponse(
        url=_project_url(project),
        status_code=303,
    )


@router.post("/{project_id}/delete", response_class=RedirectResponse)
def delete_project(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Delete a project (soft delete by setting status to CANCELLED)."""
    from app.models.finance.core_org.project import Project, ProjectStatus
    from sqlalchemy import select

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if project:
        project.status = ProjectStatus.CANCELLED
        db.commit()

    return RedirectResponse(url="/operations/projects", status_code=303)


# ============================================================================
# Tasks
# ============================================================================


@router.get("/{project_id}/tasks", response_class=HTMLResponse)
def project_tasks(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    status: Optional[str] = None,
    priority: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Project tasks list page."""
    from app.models.finance.core_org.project import Project
    from app.models.pm import TaskPriority, TaskStatus
    from sqlalchemy import select

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

    per_page = 20
    result = services["task"].list_tasks(
        project_id=project.project_id,
        status=status_enum,
        priority=priority_enum,
        params=PaginationParams(offset=(page - 1) * per_page, limit=per_page),
    )

    employees = _get_employees(db, org_id)

    context = {
        "request": request,
        **base_context(request, auth, "Project Tasks", "tasks", db=db),
        "project": project,
        "tasks": result.items,
        "total": result.total,
        "page": page,
        "per_page": per_page,
        "total_pages": (result.total + per_page - 1) // per_page if result.total > 0 else 1,
        "status_filter": status,
        "priority_filter": priority,
        "statuses": [s.value for s in TaskStatus],
        "priorities": [p.value for p in TaskPriority],
        "employees": employees,
    }

    return templates.TemplateResponse("operations/projects/tasks/list.html", context)


@router.get("/{project_id}/tasks/{task_id}", response_class=HTMLResponse)
def task_detail(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Task detail page."""
    from app.models.finance.core_org.project import Project
    from sqlalchemy import select

    org_id = coerce_uuid(auth.organization_id)
    task_uuid = coerce_uuid(task_id)

    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)

    try:
        task = services["task"].get_task_or_raise(task_uuid)
    except NotFoundError:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Task not found"},
            status_code=404,
        )

    # Get subtasks
    subtasks = services["task"].get_subtasks(task_uuid)

    # Get dependencies
    dependencies = services["task"].get_dependencies(task_uuid)

    # Get time entries for this task
    time_entries = services["time"].list_entries(
        task_id=task_uuid,
        params=PaginationParams(offset=0, limit=10),
    )

    context = {
        "request": request,
        **base_context(request, auth, task.task_name, "tasks", db=db),
        "project": project,
        "task": task,
        "subtasks": subtasks,
        "dependencies": dependencies,
        "time_entries": time_entries.items,
    }

    return templates.TemplateResponse("operations/projects/tasks/detail.html", context)


@router.get("/{project_id}/tasks/new", response_class=HTMLResponse)
def new_task_form(
    request: Request,
    project_id: str,
    parent_task_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """New task form page."""
    from app.models.finance.core_org.project import Project
    from app.models.pm import TaskPriority, TaskStatus
    from sqlalchemy import select

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)
    available_tasks = services["task"].list_tasks(
        project_id=project.project_id,
        params=PaginationParams(offset=0, limit=1000),
    ).items

    employees = _get_employees(db, org_id)

    context = {
        "request": request,
        **base_context(request, auth, "New Task", "tasks", db=db),
        "project": project,
        "task": None,
        "parent_task_id": parent_task_id,
        "available_parent_tasks": available_tasks,
        "team_members": employees,
        "statuses": [s.value for s in TaskStatus],
        "priorities": [p.value for p in TaskPriority],
    }

    return templates.TemplateResponse("operations/projects/tasks/form.html", context)


@router.post("/{project_id}/tasks", response_class=RedirectResponse)
def create_task(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    task_name: str = Form(...),
    task_code: str = Form(default=""),
    description: str = Form(default=""),
    status: str = Form(default="OPEN"),
    priority: str = Form(default="MEDIUM"),
    parent_task_id: str = Form(default=""),
    assigned_to_id: str = Form(default=""),
    start_date: str = Form(default=""),
    due_date: str = Form(default=""),
    estimated_hours: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """Create a new task."""
    from app.models.pm import TaskPriority, TaskStatus

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(url="/operations/projects", status_code=303)
    services = _get_services(db, org_id)

    # Generate task code if not provided
    if not task_code.strip():
        import uuid as uuid_mod
        task_code = f"TASK-{str(uuid_mod.uuid4())[:8].upper()}"

    task = services["task"].create_task({
        "project_id": project.project_id,
        "task_code": task_code.strip(),
        "task_name": task_name.strip(),
        "description": description.strip() if description else None,
        "status": TaskStatus(status),
        "priority": TaskPriority(priority),
        "parent_task_id": coerce_uuid(parent_task_id) if parent_task_id else None,
        "assigned_to_id": coerce_uuid(assigned_to_id) if assigned_to_id else None,
        "start_date": date.fromisoformat(start_date) if start_date else None,
        "due_date": date.fromisoformat(due_date) if due_date else None,
        "estimated_hours": Decimal(estimated_hours) if estimated_hours else None,
    })

    db.commit()

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/tasks/{task.task_id}",
        status_code=303,
    )


@router.get("/{project_id}/tasks/{task_id}/edit", response_class=HTMLResponse)
def edit_task_form(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Edit task form page."""
    from app.models.finance.core_org.project import Project
    from app.models.pm import TaskPriority, TaskStatus
    from sqlalchemy import select

    org_id = coerce_uuid(auth.organization_id)
    task_uuid = coerce_uuid(task_id)

    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)

    try:
        task = services["task"].get_task_or_raise(task_uuid)
    except NotFoundError:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Task not found"},
            status_code=404,
        )

    available_tasks = [
        t for t in services["task"].list_tasks(
            project_id=project.project_id,
            params=PaginationParams(offset=0, limit=1000),
        ).items
        if t.task_id != task_uuid
    ]

    employees = _get_employees(db, org_id)

    context = {
        "request": request,
        **base_context(request, auth, "Edit Task", "tasks", db=db),
        "project": project,
        "task": task,
        "available_parent_tasks": available_tasks,
        "team_members": employees,
        "statuses": [s.value for s in TaskStatus],
        "priorities": [p.value for p in TaskPriority],
    }

    return templates.TemplateResponse("operations/projects/tasks/form.html", context)


@router.post("/{project_id}/tasks/{task_id}", response_class=RedirectResponse)
def update_task(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    task_name: str = Form(...),
    description: str = Form(default=""),
    status: str = Form(...),
    priority: str = Form(...),
    parent_task_id: str = Form(default=""),
    assigned_to_id: str = Form(default=""),
    start_date: str = Form(default=""),
    due_date: str = Form(default=""),
    estimated_hours: str = Form(default=""),
    actual_hours: str = Form(default=""),
    progress_percent: str = Form(default="0"),
    db: Session = Depends(get_db),
):
    """Update an existing task."""
    from app.models.pm import TaskPriority, TaskStatus

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(url="/operations/projects", status_code=303)
    task_uuid = coerce_uuid(task_id)
    services = _get_services(db, org_id)

    try:
        services["task"].update_task(task_uuid, {
            "task_name": task_name.strip(),
            "description": description.strip() if description else None,
            "status": TaskStatus(status),
            "priority": TaskPriority(priority),
            "parent_task_id": coerce_uuid(parent_task_id) if parent_task_id else None,
            "assigned_to_id": coerce_uuid(assigned_to_id) if assigned_to_id else None,
            "start_date": date.fromisoformat(start_date) if start_date else None,
            "due_date": date.fromisoformat(due_date) if due_date else None,
            "estimated_hours": Decimal(estimated_hours) if estimated_hours else None,
            "actual_hours": Decimal(actual_hours) if actual_hours else Decimal("0"),
            "progress_percent": int(progress_percent) if progress_percent else 0,
        })
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/tasks/{task_id}",
        status_code=303,
    )


@router.post("/{project_id}/tasks/{task_id}/delete", response_class=RedirectResponse)
def delete_task(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Delete a task (soft delete)."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(url="/operations/projects", status_code=303)
    task_uuid = coerce_uuid(task_id)
    services = _get_services(db, org_id)

    try:
        services["task"].delete_task(task_uuid)
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/tasks",
        status_code=303,
    )


# ============================================================================
# Gantt Chart
# ============================================================================


@router.get("/{project_id}/gantt", response_class=HTMLResponse)
def project_gantt(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Project Gantt chart page."""
    from app.models.finance.core_org.project import Project
    from sqlalchemy import select

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

    return templates.TemplateResponse("operations/projects/gantt.html", context)


# ============================================================================
# Team/Resources
# ============================================================================


@router.get("/{project_id}/team", response_class=HTMLResponse)
def project_team(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Project team management page."""
    from app.models.finance.core_org.project import Project
    from sqlalchemy import select

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

    return templates.TemplateResponse("operations/projects/team.html", context)


@router.post("/{project_id}/team", response_class=RedirectResponse)
def create_resource_allocation(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
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
        return RedirectResponse(url="/operations/projects", status_code=303)
    services = _get_services(db, org_id)

    services["resource"].allocate_resource({
        "project_id": project.project_id,
        "employee_id": coerce_uuid(employee_id),
        "role_on_project": role_on_project.strip() if role_on_project else None,
        "allocation_percent": Decimal(allocation_percent) if allocation_percent else Decimal("100"),
        "start_date": date.fromisoformat(start_date),
        "end_date": date.fromisoformat(end_date) if end_date else None,
        "cost_rate_per_hour": Decimal(cost_rate_per_hour) if cost_rate_per_hour else None,
        "billing_rate_per_hour": Decimal(billing_rate_per_hour) if billing_rate_per_hour else None,
    })

    db.commit()

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/team",
        status_code=303,
    )


@router.post("/{project_id}/team/{allocation_id}", response_class=RedirectResponse)
def update_resource_allocation(
    request: Request,
    project_id: str,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
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
        return RedirectResponse(url="/operations/projects", status_code=303)
    allocation_uuid = coerce_uuid(allocation_id)
    services = _get_services(db, org_id)

    try:
        services["resource"].update_allocation(allocation_uuid, {
            "role_on_project": role_on_project.strip() if role_on_project else None,
            "allocation_percent": Decimal(allocation_percent) if allocation_percent else Decimal("100"),
            "end_date": date.fromisoformat(end_date) if end_date else None,
            "cost_rate_per_hour": Decimal(cost_rate_per_hour) if cost_rate_per_hour else None,
            "billing_rate_per_hour": Decimal(billing_rate_per_hour) if billing_rate_per_hour else None,
            "is_active": is_active == "on",
        })
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/team",
        status_code=303,
    )


@router.post("/{project_id}/team/{allocation_id}/end", response_class=RedirectResponse)
def end_resource_allocation(
    request: Request,
    project_id: str,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    end_date: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """End a resource allocation."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(url="/operations/projects", status_code=303)
    allocation_uuid = coerce_uuid(allocation_id)
    services = _get_services(db, org_id)

    try:
        end_dt = date.fromisoformat(end_date) if end_date else date.today()
        services["resource"].end_allocation(allocation_uuid, end_dt)
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/team",
        status_code=303,
    )


@router.post("/{project_id}/team/{allocation_id}/delete", response_class=RedirectResponse)
def delete_resource_allocation(
    request: Request,
    project_id: str,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Delete a resource allocation."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(url="/operations/projects", status_code=303)
    allocation_uuid = coerce_uuid(allocation_id)
    services = _get_services(db, org_id)

    try:
        services["resource"].delete_allocation(allocation_uuid)
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/team",
        status_code=303,
    )


# ============================================================================
# Milestones
# ============================================================================


@router.get("/{project_id}/milestones", response_class=HTMLResponse)
def project_milestones(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Project milestones page."""
    from app.models.finance.core_org.project import Project
    from sqlalchemy import select

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

    return templates.TemplateResponse("operations/projects/milestones.html", context)


@router.post("/{project_id}/milestones", response_class=RedirectResponse)
def create_milestone(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
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
        return RedirectResponse(url="/operations/projects", status_code=303)
    services = _get_services(db, org_id)

    # Generate milestone code
    milestone_code = f"MS-{str(uuid_mod.uuid4())[:8].upper()}"

    services["milestone"].create_milestone({
        "project_id": project.project_id,
        "milestone_code": milestone_code,
        "milestone_name": name.strip(),
        "description": description.strip() if description else None,
        "target_date": date.fromisoformat(target_date),
        "linked_task_id": coerce_uuid(linked_task_id) if linked_task_id else None,
    })

    db.commit()

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/milestones",
        status_code=303,
    )


@router.post("/{project_id}/milestones/{milestone_id}", response_class=RedirectResponse)
def update_milestone(
    request: Request,
    project_id: str,
    milestone_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    name: str = Form(...),
    description: str = Form(default=""),
    target_date: str = Form(...),
    status: str = Form(...),
    actual_date: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """Update a milestone."""
    from app.models.pm import MilestoneStatus

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(url="/operations/projects", status_code=303)
    milestone_uuid = coerce_uuid(milestone_id)
    services = _get_services(db, org_id)

    try:
        # Note: update_milestone doesn't accept status change - only the fields defined
        # If status changes needed, use achieve_milestone for ACHIEVED
        services["milestone"].update_milestone(milestone_uuid, {
            "milestone_name": name.strip(),
            "description": description.strip() if description else None,
            "target_date": date.fromisoformat(target_date),
        })
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/milestones",
        status_code=303,
    )


@router.post("/{project_id}/milestones/{milestone_id}/achieve", response_class=RedirectResponse)
def achieve_milestone(
    request: Request,
    project_id: str,
    milestone_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Mark a milestone as achieved."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(url="/operations/projects", status_code=303)
    milestone_uuid = coerce_uuid(milestone_id)
    services = _get_services(db, org_id)

    try:
        services["milestone"].achieve_milestone(milestone_uuid)
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/milestones",
        status_code=303,
    )


@router.post("/{project_id}/milestones/{milestone_id}/delete", response_class=RedirectResponse)
def delete_milestone(
    request: Request,
    project_id: str,
    milestone_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Delete a milestone."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(url="/operations/projects", status_code=303)
    milestone_uuid = coerce_uuid(milestone_id)
    services = _get_services(db, org_id)

    try:
        services["milestone"].delete_milestone(milestone_uuid)
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/milestones",
        status_code=303,
    )


# ============================================================================
# Time Tracking
# ============================================================================


@router.get("/{project_id}/time", response_class=HTMLResponse)
def project_time_entries(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Project time entries page."""
    from app.models.finance.core_org.project import Project
    from sqlalchemy import select

    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)

    if not project:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "message": "Project not found"},
            status_code=404,
        )

    services = _get_services(db, org_id)

    per_page = 20
    result = services["time"].list_entries(
        project_id=project.project_id,
        params=PaginationParams(offset=(page - 1) * per_page, limit=per_page),
    )

    time_summary = services["time"].get_project_time_summary(project.project_id)

    # Get tasks for the dropdown in the time entry form
    tasks = services["task"].list_tasks(
        project_id=project.project_id,
        params=PaginationParams(offset=0, limit=1000),
    ).items

    employees = _get_employees(db, org_id)

    context = {
        "request": request,
        **base_context(request, auth, "Time Entries", "time", db=db),
        "project": project,
        "entries": result.items,
        "total": result.total,
        "page": page,
        "per_page": per_page,
        "total_pages": (result.total + per_page - 1) // per_page if result.total > 0 else 1,
        "time_summary": time_summary,
        "tasks": tasks,
        "employees": employees,
    }

    return templates.TemplateResponse("operations/projects/time/list.html", context)


@router.post("/{project_id}/time", response_class=RedirectResponse)
def create_time_entry(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
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
        return RedirectResponse(url="/operations/projects", status_code=303)
    services = _get_services(db, org_id)

    services["time"].log_time({
        "project_id": project.project_id,
        "task_id": coerce_uuid(task_id) if task_id else None,
        "employee_id": coerce_uuid(employee_id),
        "entry_date": date.fromisoformat(entry_date),
        "hours": Decimal(hours),
        "description": description.strip() if description else None,
        "is_billable": is_billable == "on",
    })

    db.commit()

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/time",
        status_code=303,
    )


@router.post("/{project_id}/time/{entry_id}", response_class=RedirectResponse)
def update_time_entry(
    request: Request,
    project_id: str,
    entry_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
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
        return RedirectResponse(url="/operations/projects", status_code=303)
    entry_uuid = coerce_uuid(entry_id)
    services = _get_services(db, org_id)

    try:
        services["time"].update_entry(entry_uuid, {
            "task_id": coerce_uuid(task_id) if task_id else None,
            "entry_date": date.fromisoformat(entry_date),
            "hours": Decimal(hours),
            "description": description.strip() if description else None,
            "is_billable": is_billable == "on",
        })
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/time",
        status_code=303,
    )


@router.post("/{project_id}/time/{entry_id}/delete", response_class=RedirectResponse)
def delete_time_entry(
    request: Request,
    project_id: str,
    entry_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Delete a time entry."""
    org_id = coerce_uuid(auth.organization_id)
    project = _resolve_project_ref(db, org_id, project_id)
    if not project:
        return RedirectResponse(url="/operations/projects", status_code=303)
    entry_uuid = coerce_uuid(entry_id)
    services = _get_services(db, org_id)

    try:
        services["time"].delete_entry(entry_uuid)
        db.commit()
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/operations/projects/{project.project_code}/time",
        status_code=303,
    )


# ============================================================================
# Timesheet
# ============================================================================


@router.get("/timesheet", response_class=HTMLResponse)
def employee_timesheet(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    week_start: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Employee weekly timesheet page."""
    from datetime import date, timedelta

    # Determine week start (Monday)
    if week_start:
        try:
            ws = date.fromisoformat(week_start)
        except ValueError:
            ws = date.today()
    else:
        ws = date.today()

    # Adjust to Monday
    ws = ws - timedelta(days=ws.weekday())

    # Get employee for current user
    # For now, just show the timesheet UI
    projects = _get_projects(db, auth.organization_id)

    context = {
        "request": request,
        **base_context(request, auth, "Timesheet", "time", db=db),
        "week_start": ws,
        "week_end": ws + timedelta(days=6),
        "projects": projects,
        "entries": [],  # Would be filled from API
    }

    return templates.TemplateResponse("operations/projects/time/timesheet.html", context)


# ============================================================================
# Expenses
# ============================================================================


@router.get("/{project_id}/expenses", response_class=HTMLResponse)
def project_expenses(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Project expenses page (read-only view)."""
    from app.models.finance.core_org.project import Project
    from sqlalchemy import select

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

    return templates.TemplateResponse("operations/projects/expenses.html", context)
