"""Project web time handlers."""

from app.services.pm.web.project_parts.base import (
    Decimal,
    Depends,
    Form,
    NotFoundError,
    PaginationParams,
    Query,
    RedirectResponse,
    Request,
    Session,
    ValidationError,
    WebAuthContext,
    _get_employees,
    _get_projects,
    _get_services,
    _resolve_project_ref,
    _safe_date,
    _safe_decimal,
    base_context,
    coerce_uuid,
    date,
    get_db,
    logger,
    require_projects_access,
    templates,
)


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
        params=PaginationParams.from_page(page, per_page),
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
        "total_pages": result.total_pages,
        "time_summary": time_summary,
        "tasks": tasks,
        "employees": employees,
        "start_date": start_date,
        "end_date": end_date,
        "billable_filter": billable,
        "billing_status_filter": billing_status,
    }

    return templates.TemplateResponse(request, "projects/time/list.html", context)


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

    return templates.TemplateResponse(request, "projects/time/form.html", context)


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

    return RedirectResponse(
        url=f"/projects/{project.project_code}/time?saved=1",
        status_code=303,
    )


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

    return templates.TemplateResponse(request, "projects/time/form.html", context)


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
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/time?saved=1",
        status_code=303,
    )


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
    except NotFoundError:
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/time?saved=1",
        status_code=303,
    )


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
    except (NotFoundError, ValidationError):
        pass

    return RedirectResponse(
        url=f"/projects/{project.project_code}/time?saved=1",
        status_code=303,
    )


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

    return RedirectResponse(
        url=f"/projects/{project.project_code}/time?saved=1",
        status_code=303,
    )


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

    return templates.TemplateResponse(request, "projects/time/timesheet.html", context)


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

    return RedirectResponse(
        url="/projects/timesheet?success=Record+saved+successfully", status_code=303
    )


__all__ = [
    "project_time_entries",
    "new_time_entry_form",
    "create_time_entry",
    "edit_time_entry_form",
    "update_time_entry",
    "delete_time_entry",
    "bill_time_entry",
    "bulk_bill_time_entries",
    "employee_timesheet",
    "log_timesheet_entry",
]
