"""
Email Branding Service — branded email rendering helpers.

Provides:
- ``get_email_branding(db, organization_id)`` — fetch org branding context
  suitable for email templates (logo, colors, org name).
- ``render_branded_email(template_name, context, db, organization_id)``
  — render an HTML + plain-text email pair with org branding injected.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.templates import templates

logger = logging.getLogger(__name__)

# Default teal used throughout the app when no custom branding exists.
_DEFAULT_PRIMARY = "#0d9488"
_DEFAULT_ACCENT = "#d97706"


def get_email_branding(
    db: Session,
    organization_id: UUID | None,
) -> dict[str, Any]:
    """Return a dict of branding values for email templates.

    Keys returned:
        brand_name, brand_logo_url, primary_color, accent_color,
        contact_email, org_website.

    Falls back gracefully when the org has no ``OrganizationBranding``
    record, using ``Organization.logo_url`` and system defaults.
    """
    from app.models.finance.core_org.organization import Organization

    result: dict[str, Any] = {
        "brand_name": "Company",
        "brand_logo_url": None,
        "primary_color": _DEFAULT_PRIMARY,
        "accent_color": _DEFAULT_ACCENT,
        "contact_email": None,
        "org_website": None,
    }

    if not organization_id:
        return result

    org = db.get(Organization, organization_id)
    if not org:
        return result

    result["brand_name"] = org.trading_name or org.legal_name
    result["contact_email"] = org.contact_email
    result["org_website"] = org.website_url

    # Prefer OrganizationBranding → fall back to Organization.logo_url
    branding = org.branding
    if branding:
        result["brand_logo_url"] = branding.logo_url or org.logo_url
        result["primary_color"] = branding.primary_color or _DEFAULT_PRIMARY
        result["accent_color"] = branding.accent_color or _DEFAULT_ACCENT
    else:
        result["brand_logo_url"] = org.logo_url

    return result


def render_branded_email(
    template_name: str,
    context: dict[str, Any],
    db: Session,
    organization_id: UUID | None,
) -> tuple[str, str]:
    """Render an HTML email and its plain-text fallback with branding.

    The *template_name* should point to an HTML template that
    ``{% extends "emails/base_email.html" %}``.  A companion ``.txt``
    template (same path, ``.html`` replaced by ``.txt``) is used for
    the plain-text body.

    Returns:
        (html_body, text_body)
    """
    branding = get_email_branding(db, organization_id)
    merged = {**branding, **context}

    # Render HTML
    html_tpl = templates.env.get_template(template_name)
    html_body: str = html_tpl.render(**merged)

    # Render plain-text companion (optional — fall back to empty)
    txt_name = template_name.replace(".html", ".txt")
    try:
        txt_tpl = templates.env.get_template(txt_name)
        text_body: str = txt_tpl.render(**merged)
    except Exception:
        # No .txt companion — strip tags as a rough fallback
        text_body = ""

    return html_body, text_body
