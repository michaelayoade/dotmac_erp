"""Project Management Web Routes.

Thin HTTP wrappers for project management. Route handlers parse FastAPI inputs
and delegate behavior to the PM web service layer.
"""

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.pm.web.project_web import project_web_service
from app.web.deps import WebAuthContext, get_db_for_org, require_projects_access

router = APIRouter(prefix="/projects", tags=["projects-web"])


@router.get("", response_class=HTMLResponse)
def projects_index(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Projects landing page."""
    return project_web_service.projects_index(request, auth, db)


@router.get("/all", response_class=HTMLResponse)
def list_projects(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    search: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db_for_org),
):
    """Projects list page."""
    return project_web_service.list_projects(request, auth, search, status, page, db)


@router.get("/new", response_class=HTMLResponse)
def new_project_form(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """New project form page."""
    return project_web_service.new_project_form(request, auth, db)


@router.get("/{project_id}/edit", response_class=HTMLResponse)
def edit_project_form(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit project form page."""
    return project_web_service.edit_project_form(request, project_id, auth, db)


@router.get("/templates", response_class=HTMLResponse)
def project_template_list(
    request: Request,
    page: int = Query(1, ge=1),
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Project template list page."""
    return project_web_service.project_template_list(request, page, auth, db)


@router.get("/templates/new", response_class=HTMLResponse)
def new_project_template_form(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """New project template form page."""
    return project_web_service.new_project_template_form(request, auth, db)


@router.post("/templates", response_class=RedirectResponse)
async def create_project_template(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new project template with ordered tasks."""
    return await project_web_service.create_project_template(request, auth, db)


@router.get("/templates/{template_id}", response_class=HTMLResponse)
def project_template_detail(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Project template detail page."""
    return project_web_service.project_template_detail(request, template_id, auth, db)


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def edit_project_template_form(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit project template form page."""
    return project_web_service.edit_project_template_form(
        request, template_id, auth, db
    )


@router.post("/templates/{template_id}", response_class=RedirectResponse)
async def update_project_template(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Update a project template and its tasks."""
    return await project_web_service.update_project_template(
        request, template_id, auth, db
    )


@router.get("/tasks", response_class=HTMLResponse)
def global_task_list(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    status: str | None = None,
    priority: str | None = None,
    project_id: str | None = None,
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db_for_org),
):
    """Global task list page."""
    return project_web_service.global_task_list(
        request, auth, status, priority, project_id, page, db
    )


@router.get("/tasks/new", response_class=HTMLResponse)
def global_task_new_form(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Global new task form page."""
    return project_web_service.global_task_new_form(request, auth, db)


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
    db: Session = Depends(get_db_for_org),
):
    """Create a new task from the global task form."""
    return project_web_service.create_global_task(
        request,
        auth,
        project_id,
        task_name,
        task_code,
        status,
        priority,
        description,
        start_date,
        due_date,
        estimated_hours,
        assigned_to_id,
        ticket_id,
        parent_task_id,
        files,
        db,
    )


@router.get("/{project_id}", response_class=HTMLResponse)
def project_dashboard(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Project dashboard/detail page."""
    return project_web_service.project_dashboard(request, project_id, auth, db)


@router.post("/{project_id}/comments", response_class=RedirectResponse)
async def add_project_comment(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    content: str = Form(...),
    is_internal: bool = Form(default=False),
    files: list[UploadFile] = File(default=None),
    db: Session = Depends(get_db_for_org),
):
    """Add a comment to a project."""
    return await project_web_service.add_project_comment(
        request, project_id, auth, content, is_internal, files, db
    )


@router.post(
    "/{project_id}/comments/{comment_id}/delete", response_class=RedirectResponse
)
def delete_project_comment(
    request: Request,
    project_id: str,
    comment_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a project comment."""
    return project_web_service.delete_project_comment(
        request, project_id, comment_id, auth, db
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
    db: Session = Depends(get_db_for_org),
):
    """Create a new project."""
    return await project_web_service.create_project(
        request,
        auth,
        project_code,
        project_name,
        description,
        status,
        project_type,
        project_priority,
        project_template_id,
        customer_id,
        start_date,
        end_date,
        budget_amount,
        percent_complete,
        files,
        db,
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
    db: Session = Depends(get_db_for_org),
):
    """Update an existing project."""
    return await project_web_service.update_project(
        request,
        project_id,
        auth,
        project_name,
        description,
        status,
        project_type,
        project_priority,
        customer_id,
        start_date,
        end_date,
        budget_amount,
        percent_complete,
        files,
        db,
    )


@router.post("/{project_id}/delete", response_class=RedirectResponse)
def delete_project(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a project (soft delete by setting status to CANCELLED)."""
    return project_web_service.delete_project(request, project_id, auth, db)


@router.get("/{project_id}/tasks", response_class=HTMLResponse)
def project_tasks(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    status: str | None = None,
    priority: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db_for_org),
):
    """Project tasks list page."""
    return project_web_service.project_tasks(
        request, project_id, auth, status, priority, page, db
    )


@router.get("/{project_id}/tasks/{task_id}", response_class=HTMLResponse)
def task_detail(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Task detail page."""
    return project_web_service.task_detail(request, project_id, task_id, auth, db)


@router.post("/{project_id}/tasks/{task_id}/comments", response_class=RedirectResponse)
async def add_task_comment(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    content: str = Form(...),
    is_internal: bool = Form(default=False),
    files: list[UploadFile] = File(default=None),
    db: Session = Depends(get_db_for_org),
):
    """Add a comment to a task."""
    return await project_web_service.add_task_comment(
        request, project_id, task_id, auth, content, is_internal, files, db
    )


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
    db: Session = Depends(get_db_for_org),
):
    """Delete a task comment."""
    return project_web_service.delete_task_comment(
        request, project_id, task_id, comment_id, auth, db
    )


@router.get("/{project_id}/tasks/{task_id}/attachments/{attachment_id}/download")
def download_task_attachment(
    request: Request,
    project_id: str,
    task_id: str,
    attachment_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Download a task attachment."""
    return project_web_service.download_task_attachment(
        request, project_id, task_id, attachment_id, auth, db
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
    db: Session = Depends(get_db_for_org),
):
    """Upload attachments to a task."""
    return await project_web_service.upload_task_attachment(
        request, project_id, task_id, auth, files, db
    )


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
    db: Session = Depends(get_db_for_org),
):
    """Delete a task attachment."""
    return project_web_service.delete_task_attachment(
        request, project_id, task_id, attachment_id, auth, db
    )


@router.get("/{project_id}/tasks/new", response_class=HTMLResponse)
def new_task_form(
    request: Request,
    project_id: str,
    parent_task_id: str | None = None,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """New task form page."""
    return project_web_service.new_task_form(
        request, project_id, parent_task_id, auth, db
    )


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
    db: Session = Depends(get_db_for_org),
):
    """Create a new task."""
    return project_web_service.create_task(
        request,
        project_id,
        auth,
        task_name,
        task_code,
        description,
        status,
        priority,
        parent_task_id,
        assigned_to_id,
        ticket_id,
        start_date,
        due_date,
        estimated_hours,
        files,
        db,
    )


@router.get("/{project_id}/tasks/{task_id}/edit", response_class=HTMLResponse)
def edit_task_form(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit task form page."""
    return project_web_service.edit_task_form(request, project_id, task_id, auth, db)


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
    db: Session = Depends(get_db_for_org),
):
    """Update an existing task."""
    return project_web_service.update_task(
        request,
        project_id,
        task_id,
        auth,
        task_name,
        description,
        status,
        priority,
        parent_task_id,
        assigned_to_id,
        ticket_id,
        start_date,
        due_date,
        estimated_hours,
        actual_hours,
        progress_percent,
        files,
        db,
    )


@router.post("/{project_id}/tasks/{task_id}/delete", response_class=RedirectResponse)
def delete_task(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a task (soft delete)."""
    return project_web_service.delete_task(request, project_id, task_id, auth, db)


@router.post("/{project_id}/tasks/{task_id}/start", response_class=RedirectResponse)
def start_task(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Start a task (transition from OPEN to IN_PROGRESS)."""
    return project_web_service.start_task(request, project_id, task_id, auth, db)


@router.post("/{project_id}/tasks/{task_id}/complete", response_class=RedirectResponse)
def complete_task(
    request: Request,
    project_id: str,
    task_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Complete a task (transition to COMPLETED)."""
    return project_web_service.complete_task(request, project_id, task_id, auth, db)


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
    db: Session = Depends(get_db_for_org),
):
    """Add a dependency to a task."""
    return project_web_service.add_task_dependency(
        request, project_id, task_id, auth, depends_on_id, dependency_type, lag_days, db
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
    db: Session = Depends(get_db_for_org),
):
    """Remove a dependency from a task."""
    return project_web_service.remove_task_dependency(
        request, project_id, task_id, depends_on_id, auth, db
    )


@router.post("/{project_id}/tasks/bulk-status", response_class=RedirectResponse)
def bulk_update_task_status(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    task_ids: str = Form(...),
    status: str = Form(...),
    db: Session = Depends(get_db_for_org),
):
    """Update status for multiple tasks."""
    return project_web_service.bulk_update_task_status(
        request, project_id, auth, task_ids, status, db
    )


@router.post("/{project_id}/tasks/bulk-delete", response_class=RedirectResponse)
def bulk_delete_tasks(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    task_ids: str = Form(...),
    db: Session = Depends(get_db_for_org),
):
    """Delete multiple tasks (soft delete)."""
    return project_web_service.bulk_delete_tasks(
        request, project_id, auth, task_ids, db
    )


@router.get("/{project_id}/gantt", response_class=HTMLResponse)
def project_gantt(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Project Gantt chart page."""
    return project_web_service.project_gantt(request, project_id, auth, db)


@router.get("/{project_id}/team", response_class=HTMLResponse)
def project_team(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Project team management page."""
    return project_web_service.project_team(request, project_id, auth, db)


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
    db: Session = Depends(get_db_for_org),
):
    """Add a team member to the project."""
    return project_web_service.create_resource_allocation(
        request,
        project_id,
        auth,
        employee_id,
        role_on_project,
        allocation_percent,
        start_date,
        end_date,
        cost_rate_per_hour,
        billing_rate_per_hour,
        db,
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
    db: Session = Depends(get_db_for_org),
):
    """Update a resource allocation."""
    return project_web_service.update_resource_allocation(
        request,
        project_id,
        allocation_id,
        auth,
        role_on_project,
        allocation_percent,
        start_date,
        end_date,
        cost_rate_per_hour,
        billing_rate_per_hour,
        is_active,
        db,
    )


@router.post("/{project_id}/team/{allocation_id}/end", response_class=RedirectResponse)
def end_resource_allocation(
    request: Request,
    project_id: str,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    end_date: str = Form(default=""),
    db: Session = Depends(get_db_for_org),
):
    """End a resource allocation."""
    return project_web_service.end_resource_allocation(
        request, project_id, allocation_id, auth, end_date, db
    )


@router.post(
    "/{project_id}/team/{allocation_id}/delete", response_class=RedirectResponse
)
def delete_resource_allocation(
    request: Request,
    project_id: str,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a resource allocation."""
    return project_web_service.delete_resource_allocation(
        request, project_id, allocation_id, auth, db
    )


@router.get("/reports/utilization", response_class=HTMLResponse)
def resource_utilization_report(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    start_date: str | None = None,
    end_date: str | None = None,
    db: Session = Depends(get_db_for_org),
):
    """Resource utilization report across all projects."""
    return project_web_service.resource_utilization_report(
        request, auth, start_date, end_date, db
    )


@router.get("/{project_id}/milestones", response_class=HTMLResponse)
def project_milestones(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Project milestones page."""
    return project_web_service.project_milestones(request, project_id, auth, db)


@router.post("/{project_id}/milestones", response_class=RedirectResponse)
def create_milestone(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    name: str = Form(...),
    description: str = Form(default=""),
    target_date: str = Form(...),
    linked_task_id: str = Form(default=""),
    db: Session = Depends(get_db_for_org),
):
    """Create a new milestone."""
    return project_web_service.create_milestone(
        request, project_id, auth, name, description, target_date, linked_task_id, db
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
    db: Session = Depends(get_db_for_org),
):
    """Update a milestone."""
    return project_web_service.update_milestone(
        request,
        project_id,
        milestone_id,
        auth,
        name,
        description,
        target_date,
        status,
        actual_date,
        db,
    )


@router.post(
    "/{project_id}/milestones/{milestone_id}/achieve", response_class=RedirectResponse
)
def achieve_milestone(
    request: Request,
    project_id: str,
    milestone_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Mark a milestone as achieved."""
    return project_web_service.achieve_milestone(
        request, project_id, milestone_id, auth, db
    )


@router.post(
    "/{project_id}/milestones/{milestone_id}/delete", response_class=RedirectResponse
)
def delete_milestone(
    request: Request,
    project_id: str,
    milestone_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a milestone."""
    return project_web_service.delete_milestone(
        request, project_id, milestone_id, auth, db
    )


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
    db: Session = Depends(get_db_for_org),
):
    """Project time entries page."""
    return project_web_service.project_time_entries(
        request,
        project_id,
        auth,
        page,
        start_date,
        end_date,
        billable,
        billing_status,
        db,
    )


@router.get("/{project_id}/time/new", response_class=HTMLResponse)
def new_time_entry_form(
    request: Request,
    project_id: str,
    task_id: str | None = None,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """New time entry form page."""
    return project_web_service.new_time_entry_form(
        request, project_id, task_id, auth, db
    )


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
    db: Session = Depends(get_db_for_org),
):
    """Log a time entry."""
    return project_web_service.create_time_entry(
        request,
        project_id,
        auth,
        task_id,
        employee_id,
        entry_date,
        hours,
        description,
        is_billable,
        db,
    )


@router.get("/{project_id}/time/{entry_id}/edit", response_class=HTMLResponse)
def edit_time_entry_form(
    request: Request,
    project_id: str,
    entry_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit time entry form page."""
    return project_web_service.edit_time_entry_form(
        request, project_id, entry_id, auth, db
    )


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
    db: Session = Depends(get_db_for_org),
):
    """Update a time entry."""
    return project_web_service.update_time_entry(
        request,
        project_id,
        entry_id,
        auth,
        task_id,
        entry_date,
        hours,
        description,
        is_billable,
        db,
    )


@router.post("/{project_id}/time/{entry_id}/delete", response_class=RedirectResponse)
def delete_time_entry(
    request: Request,
    project_id: str,
    entry_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a time entry."""
    return project_web_service.delete_time_entry(
        request, project_id, entry_id, auth, db
    )


@router.post("/{project_id}/time/{entry_id}/bill", response_class=RedirectResponse)
def bill_time_entry(
    request: Request,
    project_id: str,
    entry_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Mark a single time entry as billed."""
    return project_web_service.bill_time_entry(request, project_id, entry_id, auth, db)


@router.post("/{project_id}/time/bulk-bill", response_class=RedirectResponse)
def bulk_bill_time_entries(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    entry_ids: str = Form(...),
    db: Session = Depends(get_db_for_org),
):
    """Mark multiple time entries as billed."""
    return project_web_service.bulk_bill_time_entries(
        request, project_id, auth, entry_ids, db
    )


@router.get("/timesheet", response_class=HTMLResponse)
def employee_timesheet(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    week_start: str | None = None,
    db: Session = Depends(get_db_for_org),
):
    """Employee weekly timesheet page."""
    return project_web_service.employee_timesheet(request, auth, week_start, db)


@router.post("/timesheet/log", response_class=RedirectResponse)
def log_timesheet_entry(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    entry_date: str = Form(...),
    project_id: str = Form(...),
    task_id: str = Form(default=""),
    hours: str = Form(...),
    description: str = Form(default=""),
    db: Session = Depends(get_db_for_org),
):
    """Log a time entry from the employee timesheet view."""
    return project_web_service.log_timesheet_entry(
        request, auth, entry_date, project_id, task_id, hours, description, db
    )


@router.get("/{project_id}/expenses", response_class=HTMLResponse)
def project_expenses(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Project expenses page (read-only view)."""
    return project_web_service.project_expenses(request, project_id, auth, db)


@router.get("/{project_id}/attachments", response_class=HTMLResponse)
def project_attachments(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """List project attachments."""
    return project_web_service.project_attachments(request, project_id, auth, db)


@router.post("/{project_id}/attachments", response_class=RedirectResponse)
async def upload_project_attachment(
    request: Request,
    project_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Upload attachment to project."""
    return await project_web_service.upload_project_attachment(
        request, project_id, auth, db
    )


@router.get("/{project_id}/attachments/{attachment_id}/download")
def download_project_attachment(
    request: Request,
    project_id: str,
    attachment_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Download project attachment."""
    return project_web_service.download_project_attachment(
        request, project_id, attachment_id, auth, db
    )


@router.post(
    "/{project_id}/attachments/{attachment_id}/delete", response_class=RedirectResponse
)
def delete_project_attachment(
    request: Request,
    project_id: str,
    attachment_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete project attachment."""
    return project_web_service.delete_project_attachment(
        request, project_id, attachment_id, auth, db
    )


@router.get("/import", response_class=HTMLResponse)
def project_import_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Project import dashboard page."""
    return project_web_service.project_import_dashboard(request, auth, db)


@router.get("/import/{entity_type}", response_class=HTMLResponse)
def project_import_form(
    request: Request,
    entity_type: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Project import form for a specific entity type."""
    return project_web_service.project_import_form(request, entity_type, auth, db)


@router.post("/import/{entity_type}/preview", response_class=JSONResponse)
async def project_import_preview(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Preview project import with validation and column mapping."""
    return await project_web_service.project_import_preview(
        request, entity_type, file, auth, db
    )


@router.post("/import/{entity_type}", response_class=JSONResponse)
async def project_execute_import(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    skip_duplicates: str | None = Form(default=None),
    dry_run: str | None = Form(default=None),
    column_mapping: str | None = Form(default=None),
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db_for_org),
):
    """Execute project import operation (web route)."""
    return await project_web_service.project_execute_import(
        request, entity_type, file, skip_duplicates, dry_run, column_mapping, auth, db
    )
