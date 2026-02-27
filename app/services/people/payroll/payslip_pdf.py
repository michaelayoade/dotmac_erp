"""
Payslip PDF Generation Service.

Generates PDF payslips using WeasyPrint and Jinja2 templates.
"""

import base64
import logging
import mimetypes
import os
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlparse
from uuid import UUID

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.models.people.payroll.salary_slip import SalarySlip
from app.services.formatters import format_currency_compact
from app.services.storage import get_storage

logger = logging.getLogger(__name__)

# Template environment for PDF generation
_template_env: Environment | None = None


def _get_template_env() -> Environment:
    """Get or create the Jinja2 template environment."""
    global _template_env
    if _template_env is None:
        import os

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
        # Add custom filters
        _template_env.filters["format_currency"] = _format_currency
    return _template_env


def _format_currency(value: Decimal | float | int, decimals: int = 2) -> str:
    """Format a number as currency with thousands separator."""
    return format_currency_compact(value, none_value="0.00", decimal_places=decimals)


class PayslipPDFService:
    """Service for generating payslip PDFs."""

    def __init__(self, db: Session):
        self.db = db

    def generate_pdf(
        self,
        slip: SalarySlip,
        organization_name: str | None = None,
        organization_address: str | None = None,
        logo_url: str | None = None,
    ) -> bytes:
        """
        Generate a PDF payslip document.

        Args:
            slip: The SalarySlip model instance
            organization_name: Company name for header (optional, uses org if available)
            organization_address: Company address for header (optional)
            logo_url: URL or path to company logo (optional)

        Returns:
            PDF file contents as bytes
        """
        try:
            from weasyprint import HTML
        except ImportError:
            logger.error("WeasyPrint not installed. Run: pip install weasyprint")
            raise ImportError("WeasyPrint is required for PDF generation")

        # Get organization info and branding
        primary_color = "#0d9488"
        accent_color = "#14b8a6"
        resolved_logo_url = logo_url
        resolved_org_name = organization_name
        resolved_org_address = organization_address
        render_base_url = self._pdf_asset_base_url()

        org = None
        if hasattr(slip, "organization") and slip.organization:
            org = slip.organization
        elif getattr(slip, "employee", None) and getattr(
            slip.employee, "organization", None
        ):
            org = slip.employee.organization

        if org:
            if not resolved_org_name:
                branding_name = (
                    org.branding.display_name
                    if org.branding and org.branding.display_name
                    else None
                )
                resolved_org_name = (
                    branding_name or org.trading_name or org.legal_name or "Company"
                )
            if not resolved_org_address:
                addr_parts = [org.address_line1, org.address_line2, org.city, org.state]
                resolved_org_address = ", ".join([p for p in addr_parts if p]) or None
            if org.branding:
                primary_color = org.branding.primary_color or primary_color
                accent_color = org.branding.accent_color or accent_color
                if not resolved_logo_url and org.branding.logo_url:
                    resolved_logo_url = org.branding.logo_url
            if not resolved_logo_url and org.logo_url:
                resolved_logo_url = org.logo_url

        resolved_logo_url = self._resolve_logo_url(resolved_logo_url, render_base_url)
        embedded_logo_data = self._try_embed_branding_logo(
            resolved_logo_url,
            organization_id=slip.organization_id,
        )
        if embedded_logo_data:
            resolved_logo_url = embedded_logo_data

        # Get employee info
        employee = slip.employee
        employee_name = slip.employee_name or (
            employee.full_name if employee else "Unknown"
        )
        employee_code = employee.employee_code if employee else "N/A"
        department_name = (
            employee.department.department_name
            if employee and employee.department
            else None
        )
        designation_name = (
            employee.designation.designation_name
            if employee and employee.designation
            else None
        )

        # Prepare template context
        context = {
            "slip": slip,
            "organization_name": resolved_org_name or "Company",
            "organization_address": resolved_org_address,
            "logo_url": resolved_logo_url,
            "primary_color": primary_color,
            "accent_color": accent_color,
            "employee_name": employee_name,
            "employee_code": employee_code,
            "department_name": department_name,
            "designation_name": designation_name,
            "earnings": list(slip.earnings),
            "deductions": list(slip.deductions),
            "gross_pay": slip.gross_pay,
            "total_deduction": slip.total_deduction,
            "net_pay": slip.net_pay,
            "currency_code": slip.currency_code,
            "pay_period_start": slip.start_date,
            "pay_period_end": slip.end_date,
            "payment_days": slip.payment_days,
            "total_working_days": slip.total_working_days,
            "bank_name": slip.bank_name,
            "bank_account_number": slip.bank_account_number,
            "bank_account_name": slip.bank_account_name,
            "now": datetime.now,  # Function for template to call
        }

        # Render HTML template
        template = _get_template_env().get_template("people/payroll/payslip_pdf.html")
        html_content = template.render(**context)

        # Generate PDF
        html = HTML(string=html_content, base_url=render_base_url)
        pdf_bytes: bytes = html.write_pdf()

        logger.info("Generated PDF for payslip %s", slip.slip_number)
        return pdf_bytes

    def generate_pdf_by_id(
        self,
        slip_id: UUID,
        organization_name: str | None = None,
        organization_address: str | None = None,
        logo_url: str | None = None,
    ) -> bytes | None:
        """
        Generate a PDF payslip by slip ID.

        Args:
            slip_id: The salary slip UUID
            organization_name: Company name for header (optional)
            organization_address: Company address for header (optional)
            logo_url: URL or path to company logo (optional)

        Returns:
            PDF file contents as bytes, or None if slip not found
        """
        slip = self.db.get(SalarySlip, slip_id)
        if not slip:
            logger.warning("Salary slip %s not found for PDF generation", slip_id)
            return None

        return self.generate_pdf(
            slip,
            organization_name=organization_name,
            organization_address=organization_address,
            logo_url=logo_url,
        )

    @staticmethod
    def _pdf_asset_base_url() -> str:
        """
        Base URL used by WeasyPrint to resolve relative asset paths.

        Prefer explicit PDF_ASSET_BASE_URL; fallback to APP_URL; then internal service URL.
        """
        return (
            os.getenv("PDF_ASSET_BASE_URL") or os.getenv("APP_URL") or "http://app:8002"
        ).rstrip("/")

    @staticmethod
    def _resolve_logo_url(logo_url: str | None, base_url: str) -> str | None:
        """Resolve logo URL to absolute URL for WeasyPrint fetcher."""
        if not logo_url:
            return None
        parsed = urlparse(logo_url)
        if parsed.scheme in {"http", "https"}:
            return logo_url
        if logo_url.startswith("/"):
            return f"{base_url}{logo_url}"
        return f"{base_url}/{logo_url.lstrip('/')}"

    @staticmethod
    def _extract_branding_s3_key(logo_url: str) -> str | None:
        """Extract branding S3 key from logo URL path."""
        parsed = urlparse(logo_url)
        path = parsed.path or logo_url
        marker = "/files/branding/"
        legacy_marker = "/branding/"

        remainder: str | None = None
        if marker in path:
            remainder = path.split(marker, 1)[1]
        elif path.startswith(legacy_marker):
            remainder = path[len(legacy_marker) :]

        if not remainder:
            return None

        parts = [p for p in remainder.split("/") if p]
        if len(parts) < 2:
            return None

        org_id = parts[0]
        filename = parts[1]
        if not org_id or not filename:
            return None
        return f"branding/{org_id}/{filename}"

    @staticmethod
    def _try_embed_branding_logo(
        logo_url: str | None,
        organization_id: UUID | None = None,
    ) -> str | None:
        """Inline branding logo as a data URI to avoid auth-gated HTTP fetches."""
        if not logo_url:
            return None

        s3_key = PayslipPDFService._extract_branding_s3_key(logo_url)
        if not s3_key:
            return None

        # Guard against cross-organization logo leakage in generated PDFs.
        if organization_id:
            key_org = s3_key.split("/", 2)[1]
            if key_org != str(organization_id):
                return None

        try:
            data = get_storage().download(s3_key)
        except Exception as exc:
            logger.warning("Failed to download branding logo %s: %s", s3_key, exc)
            return None

        if not data:
            return None

        content_type = mimetypes.guess_type(s3_key)[0] or "image/png"
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{content_type};base64,{encoded}"
