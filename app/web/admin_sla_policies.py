"""Administrator-only SLA policy authoring pages."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.services.sla_policies_admin_web import (
    SLAPolicyAdminService,
    SLAPolicyNotFoundError,
    SLAPolicyValidationError,
)
from app.services.upload_utils import read_upload_bytes
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    optional_web_auth,
    require_admin_access,
)

router = APIRouter(
    prefix="/admin/sla-policies",
    tags=["admin-sla-policies-web"],
    dependencies=[Depends(require_admin_access)],
)


def _page_context(
    request: Request,
    auth: WebAuthContext,
    db: Session,
    title: str,
) -> dict[str, Any]:
    context = base_context(request, auth, title, "admin", db=db)
    context["active_page"] = "sla_policies"
    return context


def _form_response(
    request: Request,
    auth: WebAuthContext,
    db: Session,
    *,
    policy: Any = None,
    form_data: dict[str, Any] | None = None,
    error: str | None = None,
    status_code: int = 200,
):
    page_title = "Edit SLA Policy" if policy else "Create SLA Policy"
    context = _page_context(request, auth, db, page_title)
    context.update(
        {
            "policy": policy,
            "form_data": form_data or SLAPolicyAdminService.empty_form_data(),
            "error": error,
        }
    )
    return templates.TemplateResponse(
        request,
        "admin/sla_policies/form.html",
        context,
        status_code=status_code,
    )


def _not_found(exc: SLAPolicyNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _upload_form_response(
    request: Request,
    auth: WebAuthContext,
    db: Session,
    *,
    form_data: dict[str, str] | None = None,
    error: str | None = None,
    status_code: int = 200,
):
    context = _page_context(request, auth, db, "Upload SLA Policy Document")
    context.update(
        {
            "form_data": form_data or {"title": "", "summary": ""},
            "error": error,
        }
    )
    return templates.TemplateResponse(
        request,
        "admin/sla_policies/upload.html",
        context,
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse, name="admin_sla_policy_list")
def admin_sla_policy_list(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """List SLA policies in every lifecycle status."""
    context = _page_context(request, auth, db, "SLA Policies")
    context["policies"] = SLAPolicyAdminService(db).list_for_org(auth.organization_id)
    return templates.TemplateResponse(request, "admin/sla_policies/index.html", context)


@router.get("/new", response_class=HTMLResponse, name="admin_sla_policy_new")
def admin_sla_policy_new(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show the new SLA policy form."""
    return _form_response(request, auth, db)


@router.get("/upload", response_class=HTMLResponse, name="admin_sla_policy_upload")
def admin_sla_policy_upload(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show the SLA policy document upload form."""
    return _upload_form_response(request, auth, db)


@router.post(
    "/upload",
    response_class=HTMLResponse,
    name="admin_sla_policy_upload_create",
)
async def admin_sla_policy_upload_create(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Upload an inline-viewable SLA policy document as a draft."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    service = SLAPolicyAdminService(db)
    uploaded_file = form.get("document")
    try:
        if not isinstance(uploaded_file, UploadFile) or not uploaded_file.filename:
            raise SLAPolicyValidationError("Select a PDF, JPEG, or PNG document.")
        try:
            file_data = await read_upload_bytes(
                uploaded_file,
                service.MAX_DOCUMENT_SIZE_BYTES,
                error_detail="The selected document exceeds the 10 MiB limit.",
            )
        except HTTPException as exc:
            raise SLAPolicyValidationError(str(exc.detail)) from exc
        document_input = service.build_document_input(
            form,
            file_name=uploaded_file.filename,
            file_content_type=uploaded_file.content_type,
            file_data=file_data,
        )
        service.create_document(auth.organization_id, document_input)
    except SLAPolicyValidationError as exc:
        return _upload_form_response(
            request,
            auth,
            db,
            form_data=service.document_form_data(form),
            error=str(exc),
            status_code=422,
        )
    finally:
        if isinstance(uploaded_file, UploadFile):
            await uploaded_file.close()

    db.commit()
    return RedirectResponse(
        url=f"{request.url_for('admin_sla_policy_list')}?uploaded=1",
        status_code=303,
    )


@router.post("/new", response_class=HTMLResponse, name="admin_sla_policy_create")
async def admin_sla_policy_create(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Create a draft SLA policy."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    service = SLAPolicyAdminService(db)
    try:
        policy_input = service.build_policy_input(form)
        service.create(auth.organization_id, policy_input)
    except SLAPolicyValidationError as exc:
        return _form_response(
            request,
            auth,
            db,
            form_data=service.form_data(form),
            error=str(exc),
            status_code=422,
        )
    db.commit()
    return RedirectResponse(
        url=f"{request.url_for('admin_sla_policy_list')}?created=1",
        status_code=303,
    )


@router.get(
    "/{article_id}/edit",
    response_class=HTMLResponse,
    name="admin_sla_policy_edit",
)
def admin_sla_policy_edit(
    request: Request,
    article_id: UUID,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Show the edit form for one scoped SLA policy."""
    service = SLAPolicyAdminService(db)
    try:
        policy = service.get_for_org(auth.organization_id, article_id)
    except SLAPolicyNotFoundError as exc:
        raise _not_found(exc) from exc
    return _form_response(
        request,
        auth,
        db,
        policy=policy,
        form_data=service.policy_form_data(policy),
    )


@router.post(
    "/{article_id}/edit",
    response_class=HTMLResponse,
    name="admin_sla_policy_update",
)
async def admin_sla_policy_update(
    request: Request,
    article_id: UUID,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Update one scoped SLA policy."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    service = SLAPolicyAdminService(db)
    try:
        policy_input = service.build_policy_input(form)
        policy = service.update(auth.organization_id, article_id, policy_input)
    except SLAPolicyValidationError as exc:
        try:
            policy = service.get_for_org(auth.organization_id, article_id)
        except SLAPolicyNotFoundError as not_found:
            raise _not_found(not_found) from not_found
        return _form_response(
            request,
            auth,
            db,
            policy=policy,
            form_data=service.form_data(form),
            error=str(exc),
            status_code=422,
        )
    except SLAPolicyNotFoundError as exc:
        raise _not_found(exc) from exc
    db.commit()
    return RedirectResponse(
        url=f"{request.url_for('admin_sla_policy_list')}?updated=1",
        status_code=303,
    )


@router.post("/{article_id}/publish", name="admin_sla_policy_publish")
def admin_sla_policy_publish(
    request: Request,
    article_id: UUID,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Publish one scoped SLA policy."""
    try:
        SLAPolicyAdminService(db).publish(auth.organization_id, article_id)
    except SLAPolicyNotFoundError as exc:
        raise _not_found(exc) from exc
    db.commit()
    return RedirectResponse(
        url=f"{request.url_for('admin_sla_policy_list')}?published=1",
        status_code=303,
    )


@router.post("/{article_id}/archive", name="admin_sla_policy_archive")
def admin_sla_policy_archive(
    request: Request,
    article_id: UUID,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Archive one scoped SLA policy."""
    try:
        SLAPolicyAdminService(db).archive(auth.organization_id, article_id)
    except SLAPolicyNotFoundError as exc:
        raise _not_found(exc) from exc
    db.commit()
    return RedirectResponse(
        url=f"{request.url_for('admin_sla_policy_list')}?archived=1",
        status_code=303,
    )
