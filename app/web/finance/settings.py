"""
IFRS Settings Web Routes.

Configuration pages for IFRS modules including numbering sequences,
organization profile, email, automation, reporting, and feature flags.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.services.finance.settings_web import settings_web_service
from app.services.finance.branding import BrandingService
from app.templates import templates
from app.web.deps import get_async_db, get_db, require_finance_access, WebAuthContext, base_context


router = APIRouter(prefix="/settings", tags=["ifrs-settings"])


@router.get("/numbering", response_class=HTMLResponse)
async def numbering_sequences_list(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_async_db),
    sync_db: Session = Depends(get_db),
):
    """List all numbering sequences for the organization."""
    result = await settings_web_service.get_numbering_list_context(db, auth.organization_id)

    context = base_context(request, auth, "Numbering Sequences", "settings", db=sync_db)
    context.update(result)

    return templates.TemplateResponse(request, "finance/settings/numbering.html", context)


@router.get("/numbering/{sequence_id}", response_class=HTMLResponse)
async def edit_numbering_sequence(
    request: Request,
    sequence_id: uuid.UUID,
    auth: WebAuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_async_db),
    sync_db: Session = Depends(get_db),
):
    """Edit a numbering sequence configuration."""
    result, error = await settings_web_service.get_numbering_edit_context(
        db, auth.organization_id, sequence_id
    )

    if error:
        return RedirectResponse(url="/settings/numbering", status_code=302)

    context = base_context(request, auth, "Edit Numbering Sequence", "settings", db=sync_db)
    context.update(result)

    return templates.TemplateResponse(request, "finance/settings/numbering_edit.html", context)


@router.post("/numbering/{sequence_id}", response_class=HTMLResponse)
async def update_numbering_sequence(
    request: Request,
    sequence_id: uuid.UUID,
    prefix: str = Form(""),
    suffix: str = Form(""),
    separator: str = Form("-"),
    min_digits: int = Form(4),
    year_format: int = Form(4),
    reset_frequency: str = Form("MONTHLY"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a numbering sequence configuration."""
    # Parse checkbox values from form (checkboxes only submit when checked)
    form_data = await request.form()
    include_year = "include_year" in form_data
    include_month = "include_month" in form_data

    success, error = await settings_web_service.update_numbering_sequence(
        db=db,
        sequence_id=sequence_id,
        prefix=prefix,
        suffix=suffix,
        separator=separator,
        min_digits=min_digits,
        include_year=include_year,
        include_month=include_month,
        year_format=year_format,
        reset_frequency=reset_frequency,
    )

    return RedirectResponse(url="/settings/numbering", status_code=303)


@router.post("/numbering/{sequence_id}/reset", response_class=HTMLResponse)
async def reset_numbering_sequence(
    request: Request,
    sequence_id: uuid.UUID,
    new_value: int = Form(0),
    auth: WebAuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_async_db),
):
    """Reset a sequence counter to a specific value."""
    await settings_web_service.reset_numbering_sequence(db, sequence_id, new_value)

    return RedirectResponse(url="/settings/numbering", status_code=303)


@router.get("", response_class=HTMLResponse)
async def settings_index(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Settings index page."""
    context = base_context(request, auth, "Settings", "settings", db=db)
    context.update({
        "settings_sections": [
            {
                "title": "Organization Profile",
                "description": "Company information, address, and regional settings.",
                "url": "/settings/organization",
                "icon": "building-office",
            },
            {
                "title": "Branding",
                "description": "Logo, colors, typography, and visual identity.",
                "url": "/settings/branding",
                "icon": "swatch",
            },
            {
                "title": "Numbering Sequences",
                "description": "Configure document number formats for invoices, quotes, orders, and more.",
                "url": "/settings/numbering",
                "icon": "hashtag",
            },
            {
                "title": "Email Configuration",
                "description": "SMTP server settings for sending notifications and documents.",
                "url": "/settings/email",
                "icon": "envelope",
            },
            {
                "title": "Automation Settings",
                "description": "Configure recurring transactions, workflows, and custom fields.",
                "url": "/settings/automation-settings",
                "icon": "arrow-path",
            },
            {
                "title": "Report Settings",
                "description": "Default export formats, page layout, and report branding.",
                "url": "/settings/reports",
                "icon": "document-text",
            },
            {
                "title": "Feature Flags",
                "description": "Enable or disable optional modules and functionality.",
                "url": "/settings/features",
                "icon": "flag",
            },
            {
                "title": "Payments",
                "description": "Configure payment gateway integration (Paystack).",
                "url": "/settings/payments",
                "icon": "credit-card",
            },
        ],
    })
    return templates.TemplateResponse(request, "finance/settings/index.html", context)


# ========== Organization Profile ==========

@router.get("/organization", response_class=HTMLResponse)
async def organization_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_async_db),
    sync_db: Session = Depends(get_db),
):
    """Organization profile settings page."""
    result = await settings_web_service.get_organization_context(db, auth.organization_id)

    context = base_context(request, auth, "Organization Profile", "settings", db=sync_db)
    context.update(result)

    return templates.TemplateResponse(request, "finance/settings/organization.html", context)


@router.post("/organization", response_class=HTMLResponse)
async def update_organization_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_async_db),
):
    """Update organization profile."""
    form_data = getattr(request.state, "csrf_form", None)
    if form_data is None:
        form_data = await request.form()
    data = dict(form_data)

    success, error = await settings_web_service.update_organization(
        db, auth.organization_id, data
    )

    if not success:
        result = await settings_web_service.get_organization_context(db, auth.organization_id)
        context = base_context(request, auth, "Organization Profile", "settings")
        context.update(result)
        context["error"] = error
        return templates.TemplateResponse(request, "finance/settings/organization.html", context)

    return RedirectResponse(url="/settings/organization?saved=1", status_code=303)


# ========== Email Configuration ==========

@router.get("/email", response_class=HTMLResponse)
async def email_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Email configuration page."""
    result = settings_web_service.get_email_settings_context(db, auth.organization_id)

    context = base_context(request, auth, "Email Configuration", "settings", db=db)
    context.update(result)

    return templates.TemplateResponse(request, "finance/settings/email.html", context)


@router.post("/email", response_class=HTMLResponse)
async def update_email_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Update email settings."""
    form_data = getattr(request.state, "csrf_form", None)
    if form_data is None:
        form_data = await request.form()
    data = dict(form_data)

    success, error = settings_web_service.update_email_settings(
        db, auth.organization_id, data
    )

    if not success:
        result = settings_web_service.get_email_settings_context(db, auth.organization_id)
        context = base_context(request, auth, "Email Configuration", "settings", db=db)
        context.update(result)
        context["error"] = error
        return templates.TemplateResponse(request, "finance/settings/email.html", context)

    return RedirectResponse(url="/settings/email?saved=1", status_code=303)


# ========== Automation Settings ==========

@router.get("/automation-settings", response_class=HTMLResponse)
async def automation_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Automation settings page."""
    result = settings_web_service.get_automation_settings_context(db, auth.organization_id)

    context = base_context(request, auth, "Automation Settings", "settings", db=db)
    context.update(result)
    context["is_admin"] = "admin" in auth.roles

    return templates.TemplateResponse(request, "finance/settings/automation_settings.html", context)


@router.post("/automation-settings", response_class=HTMLResponse)
async def update_automation_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Update automation settings."""
    form_data = getattr(request.state, "csrf_form", None)
    if form_data is None:
        form_data = await request.form()
    data = dict(form_data)
    is_admin = "admin" in auth.roles
    if not is_admin:
        restricted_keys = {
            "webhook_allowed_hosts",
            "webhook_allowed_domains",
            "webhook_allow_insecure",
            "webhook_allow_localhost",
            "webhook_timeout_seconds",
            "openbao_allow_insecure",
        }
        for key in restricted_keys:
            data.pop(key, None)

    success, error = settings_web_service.update_automation_settings(
        db, auth.organization_id, data
    )

    if not success:
        result = settings_web_service.get_automation_settings_context(db, auth.organization_id)
        context = base_context(request, auth, "Automation Settings", "settings", db=db)
        context.update(result)
        context["is_admin"] = is_admin
        context["error"] = error
        return templates.TemplateResponse(request, "finance/settings/automation_settings.html", context)

    return RedirectResponse(url="/settings/automation-settings?saved=1", status_code=303)


# ========== Report Settings ==========

@router.get("/reports", response_class=HTMLResponse)
async def report_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Report settings page."""
    result = settings_web_service.get_reporting_context(db, auth.organization_id)

    context = base_context(request, auth, "Report Settings", "settings", db=db)
    context.update(result)

    return templates.TemplateResponse(request, "finance/settings/reports.html", context)


@router.post("/reports", response_class=HTMLResponse)
async def update_report_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Update report settings."""
    form_data = getattr(request.state, "csrf_form", None)
    if form_data is None:
        form_data = await request.form()
    data = dict(form_data)

    success, error = settings_web_service.update_reporting_settings(
        db, auth.organization_id, data
    )

    if not success:
        result = settings_web_service.get_reporting_context(db, auth.organization_id)
        context = base_context(request, auth, "Report Settings", "settings", db=db)
        context.update(result)
        context["error"] = error
        return templates.TemplateResponse(request, "finance/settings/reports.html", context)

    return RedirectResponse(url="/settings/reports?saved=1", status_code=303)


# ========== Feature Flags ==========

@router.get("/features", response_class=HTMLResponse)
async def feature_flags(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Feature flags page."""
    result = settings_web_service.get_features_context(db, auth.organization_id)

    context = base_context(request, auth, "Feature Flags", "settings", db=db)
    context.update(result)

    return templates.TemplateResponse(request, "finance/settings/features.html", context)


@router.post("/features/{feature_key}/toggle", response_class=HTMLResponse)
async def toggle_feature(
    request: Request,
    feature_key: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Toggle a feature flag."""
    form_data = getattr(request.state, "csrf_form", None)
    if form_data is None:
        form_data = await request.form()
    enabled = form_data.get("enabled", "false").lower() == "true"

    success, error = settings_web_service.toggle_feature(
        db, auth.organization_id, feature_key, enabled
    )

    if not success:
        result = settings_web_service.get_features_context(db, auth.organization_id)
        context = base_context(request, auth, "Feature Flags", "settings", db=db)
        context.update(result)
        context["error"] = error
        return templates.TemplateResponse(request, "finance/settings/features.html", context)

    return RedirectResponse(url="/settings/features?saved=1", status_code=303)


# ========== Payments Settings ==========


@router.get("/payments", response_class=HTMLResponse)
async def payments_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Payments configuration page."""
    result = settings_web_service.get_payments_settings_context(db, auth.organization_id)

    context = base_context(request, auth, "Payments Configuration", "settings", db=db)
    context.update(result)

    return templates.TemplateResponse(request, "finance/settings/payments.html", context)


@router.post("/payments", response_class=HTMLResponse)
async def update_payments_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Update payments settings."""
    form_data = getattr(request.state, "csrf_form", None)
    if form_data is None:
        form_data = await request.form()
    data = dict(form_data)

    success, error = settings_web_service.update_payments_settings(
        db, auth.organization_id, data
    )

    if not success:
        result = settings_web_service.get_payments_settings_context(db, auth.organization_id)
        context = base_context(request, auth, "Payments Configuration", "settings", db=db)
        context.update(result)
        context["error"] = error
        return templates.TemplateResponse(request, "finance/settings/payments.html", context)

    return RedirectResponse(url="/settings/payments?saved=1", status_code=303)


# ========== Branding Settings ==========


@router.get("/branding", response_class=HTMLResponse)
async def branding_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Branding settings page with live preview."""
    service = BrandingService(db)

    # Get or create branding for this organization
    branding = service.get_or_create(auth.organization_id, auth.person_id)
    db.commit()

    context = base_context(request, auth, "Branding", "settings", db=db)
    context.update({
        "branding": branding,
        "org_id": auth.organization_id,
    })

    return templates.TemplateResponse(request, "finance/settings/branding.html", context)
