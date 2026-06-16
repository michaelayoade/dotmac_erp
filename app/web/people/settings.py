"""
People Settings Web Routes.

Configuration pages for HR/People module including employee ID formats,
payroll settings, leave configuration, and attendance modes.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.services.people.hr.invite_attachment import (
    get_default_invite_attachment_metadata,
)
from app.services.people.settings_web import people_settings_web_service
from app.services.storage import get_storage
from app.templates import templates
from app.web.deps import (
    get_db_for_org,
    WebAuthContext,
    base_context,
    get_async_db_for_org,
    require_hr_access,
)

router = APIRouter(prefix="/settings", tags=["people-settings"])


@router.get("", response_class=HTMLResponse)
async def people_settings_index(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """People settings index page."""
    context = base_context(request, auth, "Settings", "settings", db=db)
    context.update(
        {
            "settings_sections": [
                {
                    "title": "HR Settings",
                    "description": "Employee ID format, probation period, and attendance configuration.",
                    "url": "/people/settings/hr",
                    "icon": "users",
                },
                {
                    "title": "Payroll Settings",
                    "description": "Payroll frequency and payment configuration.",
                    "url": "/people/settings/payroll",
                    "icon": "banknotes",
                },
                {
                    "title": "Leave Settings",
                    "description": "Leave year start and accrual policies.",
                    "url": "/people/settings/leave",
                    "icon": "calendar",
                },
                {
                    "title": "Organization Profile",
                    "description": "View company information and contact details.",
                    "url": "/people/settings/organization",
                    "icon": "building-office",
                },
            ],
        }
    )
    return templates.TemplateResponse(request, "people/settings/index.html", context)


@router.get("/hr", response_class=HTMLResponse)
async def hr_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: AsyncSession = Depends(get_async_db_for_org),
    sync_db: Session = Depends(get_db_for_org),
):
    """HR settings page - employee ID format, attendance mode, probation."""
    result = await people_settings_web_service.get_hr_settings_context(
        db, auth.organization_id
    )
    result["default_invite_attachment"] = (
        people_settings_web_service.get_default_invite_attachment_context(
            sync_db, auth.organization_id
        )
    )
    result["employee_invite_email"] = (
        people_settings_web_service.get_employee_invite_email_context(
            sync_db, auth.organization_id
        )
    )

    context = base_context(request, auth, "HR Settings", "settings", db=sync_db)
    context.update(result)

    return templates.TemplateResponse(request, "people/settings/hr.html", context)


@router.post("/hr", response_class=HTMLResponse)
async def update_hr_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: AsyncSession = Depends(get_async_db_for_org),
    sync_db: Session = Depends(get_db_for_org),
):
    """Update HR settings."""
    form_data = await request.form()
    data = dict(form_data)

    success, error = await people_settings_web_service.update_hr_settings(
        db, auth.organization_id, data
    )
    if success:
        success, error = (
            people_settings_web_service.update_employee_invite_email_template(
                sync_db,
                auth.organization_id,
                data,
            )
        )
    if success:
        (
            success,
            error,
        ) = await people_settings_web_service.update_default_invite_attachment(
            sync_db,
            auth.organization_id,
            file=form_data.get("default_invite_attachment"),
            remove_existing=form_data.get("remove_default_invite_attachment") == "on",
        )

    if not success:
        result = await people_settings_web_service.get_hr_settings_context(
            db, auth.organization_id
        )
        result["default_invite_attachment"] = (
            people_settings_web_service.get_default_invite_attachment_context(
                sync_db, auth.organization_id
            )
        )
        result["employee_invite_email"] = (
            people_settings_web_service.get_employee_invite_email_context(
                sync_db, auth.organization_id
            )
        )
        context = base_context(request, auth, "HR Settings", "settings", db=sync_db)
        context.update(result)
        context["error"] = error
        return templates.TemplateResponse(request, "people/settings/hr.html", context)

    return RedirectResponse(url="/people/settings/hr?saved=1", status_code=303)


@router.get("/hr/default-invite-attachment/download")
async def download_default_invite_attachment(
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    """Download the configured default employee invite attachment."""
    metadata = get_default_invite_attachment_metadata(db, auth.organization_id)
    if not metadata or not metadata.get("s3_key"):
        return RedirectResponse(url="/people/settings/hr?error=Attachment+not+found")

    filename = Path(str(metadata.get("filename") or "welcome-pack")).name
    content_type = str(metadata.get("content_type") or "application/octet-stream")
    try:
        data = get_storage().download(str(metadata["s3_key"]))
    except (ModuleNotFoundError, RuntimeError):
        return RedirectResponse(url="/people/settings/hr?error=Attachment+not+found")
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/payroll", response_class=HTMLResponse)
async def payroll_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: AsyncSession = Depends(get_async_db_for_org),
    sync_db: Session = Depends(get_db_for_org),
):
    """Payroll settings page - frequency and payment configuration."""
    result = await people_settings_web_service.get_hr_settings_context(
        db, auth.organization_id
    )

    context = base_context(request, auth, "Payroll Settings", "settings", db=sync_db)
    context.update(result)

    return templates.TemplateResponse(request, "people/settings/payroll.html", context)


@router.post("/payroll", response_class=HTMLResponse)
async def update_payroll_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: AsyncSession = Depends(get_async_db_for_org),
    sync_db: Session = Depends(get_db_for_org),
):
    """Update payroll settings."""
    form_data = await request.form()
    data = dict(form_data)

    success, error = await people_settings_web_service.update_hr_settings(
        db, auth.organization_id, data
    )

    if not success:
        result = await people_settings_web_service.get_hr_settings_context(
            db, auth.organization_id
        )
        context = base_context(
            request, auth, "Payroll Settings", "settings", db=sync_db
        )
        context.update(result)
        context["error"] = error
        return templates.TemplateResponse(
            request, "people/settings/payroll.html", context
        )

    return RedirectResponse(url="/people/settings/payroll?saved=1", status_code=303)


@router.get("/leave", response_class=HTMLResponse)
async def leave_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: AsyncSession = Depends(get_async_db_for_org),
    sync_db: Session = Depends(get_db_for_org),
):
    """Leave settings page - leave year start and policies."""
    result = await people_settings_web_service.get_hr_settings_context(
        db, auth.organization_id
    )

    context = base_context(request, auth, "Leave Settings", "settings", db=sync_db)
    context.update(result)

    return templates.TemplateResponse(request, "people/settings/leave.html", context)


@router.post("/leave", response_class=HTMLResponse)
async def update_leave_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: AsyncSession = Depends(get_async_db_for_org),
    sync_db: Session = Depends(get_db_for_org),
):
    """Update leave settings."""
    form_data = await request.form()
    data = dict(form_data)

    success, error = await people_settings_web_service.update_hr_settings(
        db, auth.organization_id, data
    )

    if not success:
        result = await people_settings_web_service.get_hr_settings_context(
            db, auth.organization_id
        )
        context = base_context(request, auth, "Leave Settings", "settings", db=sync_db)
        context.update(result)
        context["error"] = error
        return templates.TemplateResponse(
            request, "people/settings/leave.html", context
        )

    return RedirectResponse(url="/people/settings/leave?saved=1", status_code=303)


@router.get("/organization", response_class=HTMLResponse)
async def organization_profile(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: AsyncSession = Depends(get_async_db_for_org),
    sync_db: Session = Depends(get_db_for_org),
):
    """Organization profile page (read-only for HR users)."""
    result = await people_settings_web_service.get_organization_context(
        db, auth.organization_id
    )

    context = base_context(
        request, auth, "Organization Profile", "settings", db=sync_db
    )
    context.update(result)

    return templates.TemplateResponse(
        request, "people/settings/organization.html", context
    )
