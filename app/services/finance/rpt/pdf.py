"""
Report PDF generation service.

Renders financial report templates to PDF using WeasyPrint.
Follows the same pattern as ``PurchaseOrderPDFService`` and
``PayslipPDFService`` — lazy WeasyPrint import, singleton template
environment, and organization branding context.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, cast

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.services.email_branding import get_email_branding
from app.services.formatters import format_currency_compact

logger = logging.getLogger(__name__)

_template_env: Environment | None = None


def _get_template_env() -> Environment:
    """Get or create the Jinja2 template environment for PDF rendering."""
    global _template_env
    if _template_env is None:
        template_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                )
            ),
            "templates",
        )
        _template_env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=True,
        )
        _template_env.filters["format_currency"] = _format_currency
    return _template_env


def _format_currency(value: Any) -> str:
    """Jinja2 filter for formatting currency values in PDF templates."""
    return format_currency_compact(value, none_value="0.00", decimal_places=2)


class ReportPDFService:
    """Generate PDF documents for financial reports.

    Usage::

        pdf_bytes = ReportPDFService(db).render(
            report_name="trial_balance",
            organization_id=org_id,
            context=trial_balance_context(db, org_id, as_of_date=...),
        )
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def render(
        self,
        report_name: str,
        organization_id: str,
        context: dict[str, Any],
    ) -> bytes:
        """Render a named report to PDF bytes.

        Args:
            report_name: Template stem, e.g. ``"trial_balance"``.
            organization_id: Used to fetch organization branding.
            context: Dict returned by the report's ``*_context()`` function.

        Returns:
            PDF file content as bytes.
        """
        try:
            from weasyprint import HTML
        except ImportError as exc:
            logger.error("WeasyPrint not installed. Run: pip install weasyprint")
            raise ImportError("WeasyPrint is required for PDF generation") from exc

        from app.services.common import coerce_uuid

        # ── Build branding context ──
        org_uuid = coerce_uuid(organization_id)
        branding = get_email_branding(self.db, org_uuid)

        org_context: dict[str, Any] = {
            "org_name": branding.get("brand_name", "Company"),
            "org_address": branding.get("org_address"),
            "org_email": branding.get("contact_email"),
            "org_phone": None,
            "logo_url": branding.get("brand_logo_url"),
            "primary_color": branding.get("primary_color", "#0d9488"),
            "accent_color": branding.get("accent_color", "#d97706"),
            "currency_code": "NGN",
            "generated_at": datetime.utcnow().strftime("%d %b %Y %H:%M UTC"),
        }

        # Fetch org phone/address if available
        from app.models.finance.core_org.organization import Organization

        org = self.db.get(Organization, org_uuid)
        if org:
            if not org_context["org_address"]:
                parts = [
                    getattr(org, "address_line1", None),
                    getattr(org, "city", None),
                    getattr(org, "state", None),
                    getattr(org, "country", None),
                ]
                address = ", ".join(
                    p.strip() for p in parts if isinstance(p, str) and p.strip()
                )
                org_context["org_address"] = address or None
            org_context["org_phone"] = getattr(org, "contact_phone", None)
            org_context["currency_code"] = (
                getattr(org, "presentation_currency_code", None) or "NGN"
            )

        # ── Merge contexts (report data takes precedence) ──
        merged = {**org_context, **context}

        # ── Render HTML ──
        template_path = f"finance/reports/{report_name}_pdf.html"
        template = _get_template_env().get_template(template_path)
        html_content = template.render(**merged)

        # ── Generate PDF ──
        base_url = (
            os.getenv("PDF_ASSET_BASE_URL") or os.getenv("APP_URL") or "http://app:8002"
        ).rstrip("/")
        return cast(bytes, HTML(string=html_content, base_url=base_url).write_pdf())
