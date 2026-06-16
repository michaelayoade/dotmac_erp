"""Project web project pages handlers."""

from app.services.pm.web.project_parts.base import (
    Decimal,
    Depends,
    File,
    Form,
    MANUAL_PROJECT_CREATION_ENABLED,
    NotFoundError,
    Query,
    RedirectResponse,
    Request,
    Session,
    UploadFile,
    WebAuthContext,
    _apply_project_template,
    _build_pm_comment_attachment_map,
    _format_project_error,
    _get_project_templates,
    _get_services,
    _manual_project_creation_disabled_response,
    _normalize_uploads,
    _project_type_duration_days,
    _project_url,
    _resolve_project_ref,
    _safe_date,
    _safe_decimal,
    _safe_form_text,
    base_context,
    build_active_filters,
    coerce_uuid,
    date,
    get_db,
    get_recent_activity_for_record,
    require_projects_access,
    templates,
    timedelta,
)


def projects_index(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Projects landing page."""
    context = {
        "request": request,
        **base_context(request, auth, "Projects", "projects", db=db),
    }
    return templates.TemplateResponse(request, "projects/index.html", context)


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
    from app.services.common import PaginationParams, apply_search, paginate

    org_id = coerce_uuid(auth.organization_id)

    # Build query
    stmt = select(Project).where(Project.organization_id == org_id)

    stmt = apply_search(stmt, search, Project.project_name, Project.project_code)
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
    result = paginate(db, stmt, PaginationParams.from_page(page, per_page))
    total = result.total
    projects = list(result.items)

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
        **base_context(request, auth, "All Projects", "projects", db=db),
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
        "active_filters": build_active_filters(
            params={"status": status, "search": search},
            labels={"search": "Search"},
        ),
    }

    return templates.TemplateResponse(request, "projects/list.html", context)


def new_project_form(
    request: Request,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """New project form page."""
    if not MANUAL_PROJECT_CREATION_ENABLED:
        return _manual_project_creation_disabled_response(request)

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

    return templates.TemplateResponse(request, "projects/form.html", context)


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

    return templates.TemplateResponse(request, "projects/form.html", context)


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
        "recent_activity": get_recent_activity_for_record(
            db,
            org_id,
            record=project,
            limit=10,
        ),
        "dashboard": dashboard_data,
        "customer_info": customer_info,
        "comments": comments,
        "comment_attachments": comment_attachment_map,
        "attachments": project_attachments,
    }

    return templates.TemplateResponse(request, "projects/detail.html", context)


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

    except Exception:
        attachment_errors.append("Comment upload failed")

    base_url = _project_url(project)
    if attachment_errors:
        base_url += "?warning=Some+attachments+failed+to+upload"
    return RedirectResponse(url=base_url + "#comments", status_code=303)


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
    except (ValueError, RuntimeError):
        pass
    return RedirectResponse(
        url=_project_url(project) + "?saved=1" + "#comments", status_code=303
    )


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
    if not MANUAL_PROJECT_CREATION_ENABLED:
        return _manual_project_creation_disabled_response(request)

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

        return RedirectResponse(url=_project_url(project) + "?saved=1", status_code=303)
    except Exception as exc:
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

    return RedirectResponse(
        url=_project_url(project) + "?saved=1",
        status_code=303,
    )


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

    return RedirectResponse(
        url="/projects?success=Record+deleted+successfully", status_code=303
    )


__all__ = [
    "projects_index",
    "list_projects",
    "new_project_form",
    "edit_project_form",
    "project_dashboard",
    "add_project_comment",
    "delete_project_comment",
    "create_project",
    "update_project",
    "delete_project",
]
