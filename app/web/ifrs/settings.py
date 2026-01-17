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

from app.models.ifrs.core_config import SequenceType, ResetFrequency
from app.services.ifrs.common import NumberingService
from app.services.ifrs.settings_web import settings_web_service
from app.templates import templates
from app.web.deps import get_async_db, get_db, require_web_auth, WebAuthContext, base_context


router = APIRouter(prefix="/settings", tags=["ifrs-settings"])


# Friendly names for sequence types
SEQUENCE_TYPE_LABELS = {
    SequenceType.INVOICE: "Customer Invoice",
    SequenceType.CREDIT_NOTE: "Credit Note",
    SequenceType.PAYMENT: "Payment",
    SequenceType.RECEIPT: "Receipt",
    SequenceType.JOURNAL: "Journal Entry",
    SequenceType.PURCHASE_ORDER: "Purchase Order",
    SequenceType.SUPPLIER_INVOICE: "Supplier Invoice",
    SequenceType.ITEM: "Inventory Item",
    SequenceType.ASSET: "Fixed Asset",
    SequenceType.LEASE: "Lease",
    SequenceType.GOODS_RECEIPT: "Goods Receipt",
    SequenceType.QUOTE: "Quote",
    SequenceType.SALES_ORDER: "Sales Order",
    SequenceType.SHIPMENT: "Shipment",
    SequenceType.EXPENSE: "Expense",
}

RESET_FREQUENCY_LABELS = {
    ResetFrequency.NEVER: "Never",
    ResetFrequency.YEARLY: "Yearly",
    ResetFrequency.MONTHLY: "Monthly",
}


@router.get("/numbering", response_class=HTMLResponse)
async def numbering_sequences_list(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: AsyncSession = Depends(get_async_db),
):
    """List all numbering sequences for the organization."""
    numbering_service = NumberingService(db)

    # Get or initialize all sequences
    sequences = await numbering_service.get_all_sequences(auth.organization_id)

    # Ensure all sequence types exist (covers new types added after initial setup)
    existing_types = {seq.sequence_type for seq in sequences}
    if len(existing_types) < len(SequenceType):
        for seq_type in SequenceType:
            if seq_type not in existing_types:
                await numbering_service.get_or_create_sequence(auth.organization_id, seq_type)
        await db.commit()
        sequences = await numbering_service.get_all_sequences(auth.organization_id)

    # Build sequence data with labels and previews
    sequence_data = []
    for seq in sequences:
        sequence_data.append({
            "sequence": seq,
            "label": SEQUENCE_TYPE_LABELS.get(seq.sequence_type, seq.sequence_type.value),
            "preview": numbering_service.preview_format(seq),
            "reset_label": RESET_FREQUENCY_LABELS.get(seq.reset_frequency, seq.reset_frequency.value),
        })

    context = base_context(request, auth, "Numbering Sequences", "settings")
    context.update({
        "sequences": sequence_data,
        "sequence_types": SequenceType,
        "reset_frequencies": ResetFrequency,
        "reset_labels": RESET_FREQUENCY_LABELS,
    })

    return templates.TemplateResponse(request, "ifrs/settings/numbering.html", context)


@router.get("/numbering/{sequence_id}", response_class=HTMLResponse)
async def edit_numbering_sequence(
    request: Request,
    sequence_id: uuid.UUID,
    auth: WebAuthContext = Depends(require_web_auth),
    db: AsyncSession = Depends(get_async_db),
):
    """Edit a numbering sequence configuration."""
    numbering_service = NumberingService(db)
    sequence = await numbering_service.get_sequence_by_id(sequence_id)

    if not sequence or sequence.organization_id != auth.organization_id:
        return RedirectResponse(url="/settings/numbering", status_code=302)

    context = base_context(request, auth, "Edit Numbering Sequence", "settings")
    context.update({
        "sequence": sequence,
        "label": SEQUENCE_TYPE_LABELS.get(sequence.sequence_type, sequence.sequence_type.value),
        "preview": numbering_service.preview_format(sequence),
        "reset_frequencies": ResetFrequency,
        "reset_labels": RESET_FREQUENCY_LABELS,
    })

    return templates.TemplateResponse(request, "ifrs/settings/numbering_edit.html", context)


@router.post("/numbering/{sequence_id}", response_class=HTMLResponse)
async def update_numbering_sequence(
    request: Request,
    sequence_id: uuid.UUID,
    prefix: str = Form(""),
    suffix: str = Form(""),
    separator: str = Form("-"),
    min_digits: int = Form(4),
    include_year: bool = Form(False),
    include_month: bool = Form(False),
    year_format: int = Form(4),
    reset_frequency: str = Form("MONTHLY"),
    auth: WebAuthContext = Depends(require_web_auth),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a numbering sequence configuration."""
    numbering_service = NumberingService(db)

    # Convert checkbox values
    include_year_val = request.query_params.get("include_year") is not None
    include_month_val = request.query_params.get("include_month") is not None

    # Get form data properly for checkboxes
    form_data = await request.form()
    include_year_val = "include_year" in form_data
    include_month_val = "include_month" in form_data

    await numbering_service.update_sequence(
        sequence_id=sequence_id,
        prefix=prefix,
        suffix=suffix,
        separator=separator,
        min_digits=min_digits,
        include_year=include_year_val,
        include_month=include_month_val,
        year_format=year_format,
        reset_frequency=ResetFrequency(reset_frequency),
    )
    await db.commit()

    return RedirectResponse(url="/settings/numbering", status_code=303)


@router.post("/numbering/{sequence_id}/reset", response_class=HTMLResponse)
async def reset_numbering_sequence(
    request: Request,
    sequence_id: uuid.UUID,
    new_value: int = Form(0),
    auth: WebAuthContext = Depends(require_web_auth),
    db: AsyncSession = Depends(get_async_db),
):
    """Reset a sequence counter to a specific value."""
    numbering_service = NumberingService(db)

    await numbering_service.reset_sequence_counter(
        sequence_id=sequence_id,
        new_value=new_value,
    )
    await db.commit()

    return RedirectResponse(url="/settings/numbering", status_code=303)


@router.get("", response_class=HTMLResponse)
async def settings_index(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
):
    """Settings index page."""
    context = base_context(request, auth, "Settings", "settings")
    context.update({
        "settings_sections": [
            {
                "title": "Organization Profile",
                "description": "Company information, address, branding, and regional settings.",
                "url": "/settings/organization",
                "icon": "building-office",
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
        ],
    })
    return templates.TemplateResponse(request, "ifrs/settings/index.html", context)


# ========== Organization Profile ==========

@router.get("/organization", response_class=HTMLResponse)
async def organization_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: AsyncSession = Depends(get_async_db),
):
    """Organization profile settings page."""
    result = await settings_web_service.get_organization_context(db, auth.organization_id)

    context = base_context(request, auth, "Organization Profile", "settings")
    context.update(result)

    return templates.TemplateResponse(request, "ifrs/settings/organization.html", context)


@router.post("/organization", response_class=HTMLResponse)
async def update_organization_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: AsyncSession = Depends(get_async_db),
):
    """Update organization profile."""
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
        return templates.TemplateResponse(request, "ifrs/settings/organization.html", context)

    return RedirectResponse(url="/settings/organization?saved=1", status_code=303)


# ========== Email Configuration ==========

@router.get("/email", response_class=HTMLResponse)
async def email_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Email configuration page."""
    result = settings_web_service.get_email_settings_context(db, auth.organization_id)

    context = base_context(request, auth, "Email Configuration", "settings")
    context.update(result)

    return templates.TemplateResponse(request, "ifrs/settings/email.html", context)


@router.post("/email", response_class=HTMLResponse)
async def update_email_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Update email settings."""
    form_data = await request.form()
    data = dict(form_data)

    success, error = settings_web_service.update_email_settings(
        db, auth.organization_id, data
    )

    if not success:
        result = settings_web_service.get_email_settings_context(db, auth.organization_id)
        context = base_context(request, auth, "Email Configuration", "settings")
        context.update(result)
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/settings/email.html", context)

    return RedirectResponse(url="/settings/email?saved=1", status_code=303)


# ========== Automation Settings ==========

@router.get("/automation-settings", response_class=HTMLResponse)
async def automation_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Automation settings page."""
    result = settings_web_service.get_automation_settings_context(db, auth.organization_id)

    context = base_context(request, auth, "Automation Settings", "settings")
    context.update(result)
    context["is_admin"] = "admin" in auth.roles

    return templates.TemplateResponse(request, "ifrs/settings/automation_settings.html", context)


@router.post("/automation-settings", response_class=HTMLResponse)
async def update_automation_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Update automation settings."""
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
        context = base_context(request, auth, "Automation Settings", "settings")
        context.update(result)
        context["is_admin"] = is_admin
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/settings/automation_settings.html", context)

    return RedirectResponse(url="/settings/automation-settings?saved=1", status_code=303)


# ========== Report Settings ==========

@router.get("/reports", response_class=HTMLResponse)
async def report_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Report settings page."""
    result = settings_web_service.get_reporting_context(db, auth.organization_id)

    context = base_context(request, auth, "Report Settings", "settings")
    context.update(result)

    return templates.TemplateResponse(request, "ifrs/settings/reports.html", context)


@router.post("/reports", response_class=HTMLResponse)
async def update_report_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Update report settings."""
    form_data = await request.form()
    data = dict(form_data)

    success, error = settings_web_service.update_reporting_settings(
        db, auth.organization_id, data
    )

    if not success:
        result = settings_web_service.get_reporting_context(db, auth.organization_id)
        context = base_context(request, auth, "Report Settings", "settings")
        context.update(result)
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/settings/reports.html", context)

    return RedirectResponse(url="/settings/reports?saved=1", status_code=303)


# ========== Feature Flags ==========

@router.get("/features", response_class=HTMLResponse)
async def feature_flags(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Feature flags page."""
    result = settings_web_service.get_features_context(db, auth.organization_id)

    context = base_context(request, auth, "Feature Flags", "settings")
    context.update(result)

    return templates.TemplateResponse(request, "ifrs/settings/features.html", context)


@router.post("/features/{feature_key}/toggle", response_class=HTMLResponse)
async def toggle_feature(
    request: Request,
    feature_key: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Toggle a feature flag."""
    form_data = await request.form()
    enabled = form_data.get("enabled", "false").lower() == "true"

    success, error = settings_web_service.toggle_feature(
        db, auth.organization_id, feature_key, enabled
    )

    if not success:
        result = settings_web_service.get_features_context(db, auth.organization_id)
        context = base_context(request, auth, "Feature Flags", "settings")
        context.update(result)
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/settings/features.html", context)

    return RedirectResponse(url="/settings/features?saved=1", status_code=303)
