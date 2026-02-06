"""
People Settings Web Routes.

Configuration pages for HR/People module including employee ID formats,
payroll settings, leave configuration, and attendance modes.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.services.people.settings_web import people_settings_web_service
from app.templates import templates
from app.web.deps import (
    get_async_db,
    get_db,
    require_hr_access,
    WebAuthContext,
    base_context,
)


router = APIRouter(prefix="/settings", tags=["people-settings"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def settings_index(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
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
    db: AsyncSession = Depends(get_async_db),
    sync_db: Session = Depends(get_db),
):
    """HR settings page - employee ID format, attendance mode, probation."""
    result = await people_settings_web_service.get_hr_settings_context(
        db, auth.organization_id
    )

    context = base_context(request, auth, "HR Settings", "settings", db=sync_db)
    context.update(result)

    return templates.TemplateResponse(request, "people/settings/hr.html", context)


@router.post("/hr", response_class=HTMLResponse)
async def update_hr_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: AsyncSession = Depends(get_async_db),
    sync_db: Session = Depends(get_db),
):
    """Update HR settings."""
    form_data = await request.form()
    data = dict(form_data)

    success, error = await people_settings_web_service.update_hr_settings(
        db, auth.organization_id, data
    )

    if not success:
        result = await people_settings_web_service.get_hr_settings_context(
            db, auth.organization_id
        )
        context = base_context(request, auth, "HR Settings", "settings", db=sync_db)
        context.update(result)
        context["error"] = error
        return templates.TemplateResponse(request, "people/settings/hr.html", context)

    return RedirectResponse(url="/people/settings/hr?saved=1", status_code=303)


@router.get("/payroll", response_class=HTMLResponse)
async def payroll_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: AsyncSession = Depends(get_async_db),
    sync_db: Session = Depends(get_db),
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
    db: AsyncSession = Depends(get_async_db),
    sync_db: Session = Depends(get_db),
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
    db: AsyncSession = Depends(get_async_db),
    sync_db: Session = Depends(get_db),
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
    db: AsyncSession = Depends(get_async_db),
    sync_db: Session = Depends(get_db),
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
    db: AsyncSession = Depends(get_async_db),
    sync_db: Session = Depends(get_db),
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
