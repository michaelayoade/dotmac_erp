"""
Purchase order PDF generation service.

Generates supplier-facing PO PDFs for email delivery and future export use.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import cast

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.purchase_order import PurchaseOrder
from app.models.finance.ap.purchase_order_line import PurchaseOrderLine
from app.models.finance.ap.supplier import Supplier
from app.models.finance.core_org.organization import Organization
from app.services.email_branding import get_email_branding
from app.services.formatters import format_currency_compact

logger = logging.getLogger(__name__)

_template_env: Environment | None = None


def _get_template_env() -> Environment:
    """Get or create the Jinja2 template environment."""
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
    return _template_env


def _format_amount(value: Decimal | None) -> str:
    """Format a monetary amount without duplicating the currency code."""
    return format_currency_compact(value, none_value="0.00", decimal_places=2)


def _join_address(address: dict | None) -> str | None:
    """Flatten address dicts into a printable single block."""
    if not isinstance(address, dict):
        return None
    parts = []
    for key in (
        "address",
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
        "country",
    ):
        value = address.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return ", ".join(parts) or None


class PurchaseOrderPDFService:
    """Generate PDF documents for purchase orders."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def generate_pdf(
        self,
        po: PurchaseOrder,
        supplier: Supplier,
    ) -> bytes:
        """Render the purchase order as a PDF."""
        try:
            from weasyprint import HTML
        except ImportError as exc:
            logger.error("WeasyPrint not installed. Run: pip install weasyprint")
            raise ImportError("WeasyPrint is required for PDF generation") from exc

        organization = self.db.get(Organization, po.organization_id)
        branding = get_email_branding(self.db, po.organization_id)
        lines = list(
            self.db.scalars(
                select(PurchaseOrderLine)
                .where(PurchaseOrderLine.po_id == po.po_id)
                .order_by(PurchaseOrderLine.line_number)
            ).all()
        )
        primary_contact = supplier.primary_contact or {}

        context = {
            "po": po,
            "organization_name": branding.get("brand_name")
            or (organization.trading_name if organization else None)
            or (organization.legal_name if organization else None)
            or "Company",
            "organization_address": ", ".join(
                [
                    part
                    for part in [
                        getattr(organization, "address_line1", None),
                        getattr(organization, "address_line2", None),
                        getattr(organization, "city", None),
                        getattr(organization, "state", None),
                        getattr(organization, "country", None),
                    ]
                    if isinstance(part, str) and part.strip()
                ]
            )
            or None,
            "organization_email": branding.get("contact_email")
            or getattr(organization, "contact_email", None),
            "organization_phone": getattr(organization, "contact_phone", None),
            "logo_url": branding.get("brand_logo_url"),
            "primary_color": branding.get("primary_color", "#0d9488"),
            "accent_color": branding.get("accent_color", "#d97706"),
            "supplier_name": supplier.trading_name or supplier.legal_name,
            "supplier_email": primary_contact.get("email"),
            "supplier_phone": primary_contact.get("phone"),
            "supplier_address": _join_address(supplier.billing_address),
            "shipping_address": _join_address(po.shipping_address),
            "subtotal": _format_amount(po.subtotal),
            "tax_amount": _format_amount(po.tax_amount),
            "total_amount": _format_amount(po.total_amount),
            "lines": [
                {
                    "line_number": line.line_number,
                    "description": line.description,
                    "quantity_ordered": line.quantity_ordered,
                    "unit_price": _format_amount(line.unit_price),
                    "tax_amount": _format_amount(line.tax_amount),
                    "line_amount": _format_amount(line.line_amount),
                    "delivery_date": line.delivery_date.strftime("%d %b %Y")
                    if line.delivery_date
                    else None,
                }
                for line in lines
            ],
            "generated_at": datetime.utcnow().strftime("%d %b %Y %H:%M UTC"),
        }

        template = _get_template_env().get_template(
            "finance/ap/purchase_order_pdf.html"
        )
        html_content = template.render(**context)
        base_url = (
            os.getenv("PDF_ASSET_BASE_URL") or os.getenv("APP_URL") or "http://app:8002"
        ).rstrip("/")
        return cast(bytes, HTML(string=html_content, base_url=base_url).write_pdf())
