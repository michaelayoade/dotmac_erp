"""
Finance Settings Web Routes.

Configuration pages for Finance modules including numbering sequences,
automation settings, and report configuration.

Note: Org-wide settings (organization profile, branding, email, features,
payments) have moved to Admin settings at /admin/settings/hub.
"""

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.services.finance.settings_web import settings_web_service
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_async_db,
    get_db,
    require_finance_access,
)

router = APIRouter(prefix="/settings", tags=["finance-settings"])


# ========== Settings Index ==========


@router.get("", response_class=HTMLResponse)
async def settings_index(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Settings index page."""
    context = base_context(request, auth, "Settings", "settings", db=db)
    # Finance-specific settings only - org-wide settings moved to Admin
    context.update(
        {
            "settings_sections": [
                {
                    "title": "Numbering Sequences",
                    "description": "Configure document number formats for invoices, quotes, orders, and more.",
                    "url": "/settings/numbering",
                    "icon": "hashtag",
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
                    "title": "Payroll Settings",
                    "description": "Auto-posting behavior and rounding account for payroll journals.",
                    "url": "/settings/payroll",
                    "icon": "cog",
                },
                {
                    "title": "Exchange Rates",
                    "description": "View, add, and auto-fetch currency exchange rates.",
                    "url": "/settings/exchange-rates",
                    "icon": "currency",
                },
            ],
            "admin_settings_url": "/admin/settings/hub",
        }
    )
    return templates.TemplateResponse(request, "finance/settings/index.html", context)


# ========== Numbering Sequences ==========


@router.get("/numbering", response_class=HTMLResponse)
async def numbering_sequences_list(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_async_db),
    sync_db: Session = Depends(get_db),
):
    """List all numbering sequences for the organization."""
    result = await settings_web_service.get_numbering_list_context(
        db, auth.organization_id
    )

    context = base_context(request, auth, "Numbering Sequences", "settings", db=sync_db)
    context.update(result)

    return templates.TemplateResponse(
        request, "finance/settings/numbering.html", context
    )


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

    context = base_context(
        request, auth, "Edit Numbering Sequence", "settings", db=sync_db
    )
    context.update(result)

    return templates.TemplateResponse(
        request, "finance/settings/numbering_edit.html", context
    )


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
    form_data = await request.form()
    include_year = "include_year" in form_data
    include_month = "include_month" in form_data

    await settings_web_service.update_numbering_sequence(
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

    return RedirectResponse(
        url="/settings/numbering?success=Record+updated+successfully", status_code=303
    )


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

    return RedirectResponse(
        url="/settings/numbering?success=Record+saved+successfully", status_code=303
    )


# ========== Automation Settings ==========


@router.get("/automation-settings", response_class=HTMLResponse)
async def automation_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Automation settings page."""
    result = settings_web_service.get_automation_settings_context(
        db, auth.organization_id
    )

    context = base_context(request, auth, "Automation Settings", "settings", db=db)
    context.update(result)
    context["is_admin"] = "admin" in auth.roles

    return templates.TemplateResponse(
        request, "finance/settings/automation_settings.html", context
    )


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
        result = settings_web_service.get_automation_settings_context(
            db, auth.organization_id
        )
        context = base_context(request, auth, "Automation Settings", "settings", db=db)
        context.update(result)
        context["is_admin"] = is_admin
        context["error"] = error
        return templates.TemplateResponse(
            request, "finance/settings/automation_settings.html", context
        )

    return RedirectResponse(
        url="/settings/automation-settings?saved=1", status_code=303
    )


# ========== Payroll Settings ==========


@router.get("/payroll", response_class=HTMLResponse)
async def payroll_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Payroll settings page."""
    result = settings_web_service.get_payroll_settings_context(db, auth.organization_id)

    context = base_context(request, auth, "Payroll Settings", "settings", db=db)
    context.update(result)

    return templates.TemplateResponse(request, "finance/settings/payroll.html", context)


@router.post("/payroll", response_class=HTMLResponse)
async def update_payroll_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Update payroll settings."""
    form_data = getattr(request.state, "csrf_form", None)
    if form_data is None:
        form_data = await request.form()
    data = dict(form_data)

    success, error = settings_web_service.update_payroll_settings(
        db, auth.organization_id, data
    )

    if not success:
        result = settings_web_service.get_payroll_settings_context(
            db, auth.organization_id
        )
        context = base_context(request, auth, "Payroll Settings", "settings", db=db)
        context.update(result)
        context["error"] = error
        return templates.TemplateResponse(
            request, "finance/settings/payroll.html", context
        )

    return RedirectResponse(url="/settings/payroll?saved=1", status_code=303)


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
        return templates.TemplateResponse(
            request, "finance/settings/reports.html", context
        )

    return RedirectResponse(url="/settings/reports?saved=1", status_code=303)


# ========== Exchange Rates ==========


@router.get("/exchange-rates", response_class=HTMLResponse)
async def exchange_rates_list(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """List exchange rates."""
    from app.services.finance.platform.fx_settings_web import FXSettingsWebService

    ws = FXSettingsWebService(db)
    search = request.query_params.get("search")
    offset = int(request.query_params.get("offset", "0"))

    context = base_context(request, auth, "Exchange Rates", "settings", db=db)
    context.update(
        ws.rates_list_context(auth.organization_id, search=search, offset=offset)
    )

    return templates.TemplateResponse(
        request, "finance/settings/exchange_rates.html", context
    )


@router.post("/exchange-rates", response_class=HTMLResponse)
async def create_exchange_rate(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create a manual exchange rate."""
    from app.services.finance.platform.fx_settings_web import FXSettingsWebService

    form_data = await request.form()
    ws = FXSettingsWebService(db)

    success, error = ws.create_manual_rate(
        auth.organization_id, dict(form_data), auth.person_id
    )

    if not success:
        context = base_context(request, auth, "Exchange Rates", "settings", db=db)
        context.update(ws.rates_list_context(auth.organization_id))
        context["error"] = error
        return templates.TemplateResponse(
            request, "finance/settings/exchange_rates.html", context
        )

    db.commit()
    return RedirectResponse(
        url="/settings/exchange-rates?saved=Rate+saved+successfully", status_code=303
    )


@router.post("/exchange-rates/fetch", response_class=HTMLResponse)
async def fetch_exchange_rates(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Trigger exchange rate fetch from Currency API."""
    from app.services.finance.platform.fx_settings_web import FXSettingsWebService

    ws = FXSettingsWebService(db)
    fetch_result = ws.fetch_latest(auth.organization_id, auth.person_id)

    db.commit()

    context = base_context(request, auth, "Exchange Rates", "settings", db=db)
    context.update(ws.rates_list_context(auth.organization_id))
    context["fetch_result"] = fetch_result

    return templates.TemplateResponse(
        request, "finance/settings/exchange_rates.html", context
    )


@router.post("/exchange-rates/{rate_id}/delete", response_class=HTMLResponse)
async def delete_exchange_rate(
    request: Request,
    rate_id: uuid.UUID,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Delete an exchange rate."""
    from app.services.finance.platform.fx_settings_web import FXSettingsWebService

    ws = FXSettingsWebService(db)
    success, error = ws.delete_rate(auth.organization_id, rate_id)

    if not success:
        context = base_context(request, auth, "Exchange Rates", "settings", db=db)
        context.update(ws.rates_list_context(auth.organization_id))
        context["error"] = error
        return templates.TemplateResponse(
            request, "finance/settings/exchange_rates.html", context
        )

    db.commit()
    return RedirectResponse(
        url="/settings/exchange-rates?saved=Rate+deleted", status_code=303
    )


# ========== Legacy Route Redirects ==========
# These routes previously lived in Finance but have moved to Admin.
# Redirects are provided for backwards compatibility.


@router.get("/organization")
async def redirect_organization():
    """Redirect to Admin organization settings."""
    return RedirectResponse(url="/admin/settings/organization", status_code=301)


@router.get("/branding")
async def redirect_branding():
    """Redirect to Admin branding settings."""
    return RedirectResponse(url="/admin/settings/branding", status_code=301)


@router.get("/email")
async def redirect_email():
    """Redirect to Admin email settings."""
    return RedirectResponse(url="/admin/settings/email", status_code=301)


@router.get("/features")
async def redirect_features():
    """Redirect to Admin feature flags."""
    return RedirectResponse(url="/admin/settings/features", status_code=301)


@router.get("/payments")
async def redirect_payments():
    """Redirect to Admin payments settings."""
    return RedirectResponse(url="/admin/settings/payments", status_code=301)


@router.get("/payments/paystack")
async def redirect_paystack():
    """Redirect to Admin Paystack settings."""
    return RedirectResponse(url="/admin/settings/payments/paystack", status_code=301)
