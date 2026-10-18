"""Project web attachments import handlers."""

from uuid import UUID

from app.services.pm.web.project_parts.base import (
    Depends,
    File,
    FileResponse,
    Form,
    HTTPException,
    JSONResponse,
    Path,
    RedirectResponse,
    Request,
    Session,
    StreamingResponse,
    UploadFile,
    WebAuthContext,
    _resolve_project_ref,
    base_context,
    coerce_uuid,
    get_db,
    get_storage,
    project_import_web_service,
    require_projects_access,
    templates,
)


def _require_auth_uuid(value: UUID | None) -> UUID:
    if value is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return value


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

    return templates.TemplateResponse(request, "projects/expenses.html", context)


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

    return templates.TemplateResponse(request, "projects/attachments.html", context)


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
    uploaded_by_id = _require_auth_uuid(auth.person_id)
    description_text = description if isinstance(description, str) else None

    attachment, error = project_attachment_service.save_file(
        db=db,
        organization_id=org_id,
        entity_type="PROJECT",
        entity_id=project.project_id,
        filename=file.filename or "attachment",
        file_data=file.file,
        content_type=file.content_type or "application/octet-stream",
        uploaded_by_id=uploaded_by_id,
        description=description_text if description_text else None,
    )

    if error:
        return RedirectResponse(
            url=(
                f"/projects/{project.project_code}/attachments"
                f"?error={(error or 'Failed to delete attachment').replace(' ', '+')}"
            ),
            status_code=303,
        )

    return RedirectResponse(
        url=f"/projects/{project.project_code}/attachments?success=File+uploaded",
        status_code=303,
    )


def download_project_attachment(
    request: Request,
    project_id: str,
    attachment_id: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Download project attachment."""
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

    from app.services.pm.attachment import staged_download_response

    staged = staged_download_response(db, org_id, attachment)
    if staged is not None:
        return staged

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
        path=file_path,
        filename=attachment.file_name,
        media_type=attachment.content_type,
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
        return RedirectResponse(
            url=(
                f"/projects/{project.project_code}/attachments"
                f"?error={(error or 'Failed to delete attachment').replace(' ', '+')}"
            ),
            status_code=303,
        )

    return RedirectResponse(
        url=f"/projects/{project.project_code}/attachments?success=Attachment+deleted",
        status_code=303,
    )


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


def project_import_form(
    request: Request,
    entity_type: str,
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Project import form for a specific entity type."""
    from app.services.finance.import_export.base import build_alias_map

    entity_names = project_import_web_service.ENTITY_TYPES
    columns = project_import_web_service.get_entity_columns(entity_type)
    context = base_context(
        request,
        auth,
        f"Import {entity_names.get(entity_type, entity_type)}",
        "projects",
        db=db,
    )
    context["entity_type"] = entity_type
    context["entity_name"] = entity_names.get(entity_type, entity_type)
    context["columns"] = columns
    # Wizard context
    target_fields: list[dict[str, str | bool]] = []
    for col in columns.get("required", []):
        target_fields.append(
            {"source_field": col, "target_field": col, "required": True}
        )
    for col in columns.get("optional", []):
        target_fields.append(
            {"source_field": col, "target_field": col, "required": False}
        )
    context["preview_url"] = f"/projects/import/{entity_type}/preview"
    context["import_url"] = f"/projects/import/{entity_type}"
    context["cancel_url"] = "/projects/import"
    context["alias_map"] = build_alias_map()
    context["target_fields"] = target_fields
    context["accent_color"] = "indigo"
    return templates.TemplateResponse(
        request, "projects/import_export/import_form.html", context
    )


async def project_import_preview(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Preview project import with validation and column mapping."""
    try:
        organization_id = _require_auth_uuid(auth.organization_id)
        user_id = _require_auth_uuid(auth.person_id)
        result = await project_import_web_service.preview_import(
            db=db,
            organization_id=organization_id,
            user_id=user_id,
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


async def project_execute_import(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    skip_duplicates: str | None = Form(default=None),
    dry_run: str | None = Form(default=None),
    column_mapping: str | None = Form(default=None),
    auth: WebAuthContext = Depends(require_projects_access),
    db: Session = Depends(get_db),
):
    """Execute project import operation (web route)."""
    import json

    try:
        organization_id = _require_auth_uuid(auth.organization_id)
        user_id = _require_auth_uuid(auth.person_id)
        skip_dups = skip_duplicates is not None and skip_duplicates.lower() in (
            "true",
            "1",
            "on",
            "",
        )
        is_dry_run = dry_run is not None and dry_run.lower() in ("true", "1", "on", "")
        mapping = json.loads(column_mapping) if column_mapping else None

        result = await project_import_web_service.execute_import(
            db=db,
            organization_id=organization_id,
            user_id=user_id,
            entity_type=entity_type,
            file=file,
            skip_duplicates=skip_dups,
            dry_run=is_dry_run,
            column_mapping=mapping,
        )
        return JSONResponse(content=result)
    except ValueError as exc:
        return JSONResponse(content={"detail": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse(
            content={"detail": f"Import failed: {str(exc)}"}, status_code=500
        )


__all__ = [
    "project_expenses",
    "project_attachments",
    "upload_project_attachment",
    "download_project_attachment",
    "delete_project_attachment",
    "project_import_dashboard",
    "project_import_form",
    "project_import_preview",
    "project_execute_import",
]
