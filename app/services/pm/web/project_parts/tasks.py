"""Project web tasks handlers."""

from app.services.pm.web.project_parts.base import (
    Decimal,
    Depends,
    File,
    FileResponse,
    Form,
    HTTPException,
    NotFoundError,
    PaginationParams,
    Path,
    Query,
    RedirectResponse,
    Request,
    Session,
    StreamingResponse,
    UploadFile,
    ValidationError,
    WebAuthContext,
    _build_pm_comment_attachment_map,
    _ensure_task_code,
    _get_employees,
    _get_projects,
    _get_services,
    _get_tickets,
    _normalize_uploads,
    _resolve_project_ref,
    _resolve_task_ref,
    _safe_date,
    _safe_decimal,
    _task_url,
    base_context,
    coerce_uuid,
    get_db,
    get_recent_activity_for_record,
    get_storage,
    logger,
    require_projects_access,
    templates,
)


def global_task_list(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    status: str | None = None,
    priority: str | None = None,
    project_id: str | None = None,
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    """Global task list page."""
    from app.models.pm.task import TaskPriority, TaskStatus
    from app.services.common import PaginationParams

    org_id = coerce_uuid(auth.organization_id)
    services = _get_services(db, org_id)

    result = services["task"].list_tasks(
        project_id=coerce_uuid(project_id) if project_id else None,
        status=TaskStatus(status) if status else None,
        priority=TaskPriority(priority) if priority else None,
        include_subtasks=True,
        params=PaginationParams.from_page(page),
    )

    context = {
        "request": request,
        **base_context(request, auth, "Project Tasks", "tasks", db=db),
        "tasks": result.items,
        "projects": _get_projects(db, org_id),
        "statuses": [s.value for s in TaskStatus],
        "priorities": [p.value for p in TaskPriority],
        "status_filter": status,
        "priority_filter": priority,
        "project_filter": project_id,
        "page": result.page,
        "total_pages": result.total_pages,
        "total_count": result.total,
        "limit": result.limit,
    }
    return templates.TemplateResponse(
        request, "projects/tasks/global_list.html", context
    )


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
    return templates.TemplateResponse(
        request, "projects/tasks/global_form.html", context
    )


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

    return RedirectResponse(
        url=f"/projects/tasks?project_id={task.project_id}&saved=1",
        status_code=303,
    )


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
        params=PaginationParams.from_page(page, per_page),
    )

    # Compute subtask counts for each parent task
    tasks = result.items
    subtask_counts: dict[str, int] = {}
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
        "total_pages": result.total_pages,
        "status_filter": status,
        "priority_filter": priority,
        "statuses": [s.value for s in TaskStatus],
        "priorities": [p.value for p in TaskPriority],
        "employees": employees,
        "view_mode": "tree",
    }

    return templates.TemplateResponse(request, "projects/tasks/list.html", context)


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
        "recent_activity": get_recent_activity_for_record(
            db,
            org_id,
            record=task,
            limit=10,
        ),
        "subtasks": subtasks,
        "dependencies": dependencies,
        "dependents": dependents,
        "time_entries": time_entries.items,
        "comments": comments,
        "comment_attachments": comment_attachment_map,
        "attachments": task_attachments,
    }

    return templates.TemplateResponse(request, "projects/tasks/detail.html", context)


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

    except Exception:
        attachment_errors.append("Comment upload failed")

    base_url = (
        f"/projects/{project.project_code}/tasks/{task.task_code or task.task_id}"
    )
    if attachment_errors:
        base_url += "?warning=Some+attachments+failed+to+upload"
    return RedirectResponse(url=base_url + "#comments", status_code=303)


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
    except (ValueError, RuntimeError):
        pass
    base_url = (
        f"/projects/{project.project_code}/tasks/{task.task_code or task.task_id}"
    )
    return RedirectResponse(url=base_url + "#comments", status_code=303)


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
        # Preferred: stream from S3 (FileUploadService stores there).
        if attachment.file_path and not Path(attachment.file_path).is_absolute():
            s3_key = attachment.file_path
            if not s3_key.startswith("projects/"):
                s3_key = f"projects/{s3_key}"

            storage = get_storage()
            if storage.exists(s3_key):
                chunks, content_type, content_length = storage.stream(s3_key)
                headers: dict[str, str] = {}
                if content_length is not None:
                    headers["Content-Length"] = str(content_length)
                headers["Content-Disposition"] = (
                    f'attachment; filename="{attachment.file_name}"'
                )
                return StreamingResponse(
                    chunks,
                    media_type=content_type
                    or attachment.content_type
                    or "application/octet-stream",
                    headers=headers,
                )

        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=str(file_path),
        filename=attachment.file_name,
        media_type=attachment.content_type,
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

    base_url = (
        f"/projects/{project.project_code}/tasks/{task.task_code or task.task_id}"
    )
    return RedirectResponse(url=base_url + "#attachments", status_code=303)


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

    base_url = (
        f"/projects/{project.project_code}/tasks/{task.task_code or task.task_id}"
    )
    return RedirectResponse(url=base_url + "#attachments", status_code=303)


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

    return templates.TemplateResponse(request, "projects/tasks/form.html", context)


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

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks/{task.task_code}?saved=1",
        status_code=303,
    )


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

    return templates.TemplateResponse(request, "projects/tasks/form.html", context)


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
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks/{task.task_code or task.task_id}?saved=1",
        status_code=303,
    )


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
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks?saved=1",
        status_code=303,
    )


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
    except (NotFoundError, ValidationError):
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks/{task.task_code}?saved=1",
        status_code=303,
    )


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
    except (NotFoundError, ValidationError):
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks/{task.task_code}?saved=1",
        status_code=303,
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
    except (NotFoundError, ValidationError):
        # Redirect back with error (could flash message in future)
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks/{task.task_code}/edit?saved=1",
        status_code=303,
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
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks/{task.task_code}/edit?saved=1",
        status_code=303,
    )


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

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks?saved=1",
        status_code=303,
    )


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

    return RedirectResponse(
        url=f"/projects/{project.project_code}/tasks?saved=1",
        status_code=303,
    )


__all__ = [
    "global_task_list",
    "global_task_new_form",
    "create_global_task",
    "project_tasks",
    "task_detail",
    "add_task_comment",
    "delete_task_comment",
    "download_task_attachment",
    "upload_task_attachment",
    "delete_task_attachment",
    "new_task_form",
    "create_task",
    "edit_task_form",
    "update_task",
    "delete_task",
    "start_task",
    "complete_task",
    "add_task_dependency",
    "remove_task_dependency",
    "bulk_update_task_status",
    "bulk_delete_tasks",
]
