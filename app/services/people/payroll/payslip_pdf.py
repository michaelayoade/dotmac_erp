"""
Payslip PDF Generation Service.

Generates PDF payslips using WeasyPrint and Jinja2 templates.
"""
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.models.people.payroll.salary_slip import SalarySlip

logger = logging.getLogger(__name__)

# Template environment for PDF generation
_template_env: Optional[Environment] = None


def _get_template_env() -> Environment:
    """Get or create the Jinja2 template environment."""
    global _template_env
    if _template_env is None:
        import os
        template_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))),
            "templates"
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
    if value is None:
        return "0.00"
    return f"{float(value):,.{decimals}f}"


class PayslipPDFService:
    """Service for generating payslip PDFs."""

    def __init__(self, db: Session):
        self.db = db

    def generate_pdf(
        self,
        slip: SalarySlip,
        organization_name: Optional[str] = None,
        organization_address: Optional[str] = None,
        logo_url: Optional[str] = None,
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
            from weasyprint import HTML, CSS
        except ImportError:
            logger.error("WeasyPrint not installed. Run: pip install weasyprint")
            raise ImportError("WeasyPrint is required for PDF generation")

        # Get organization info if not provided
        if not organization_name and hasattr(slip, 'organization') and slip.organization:
            organization_name = slip.organization.organization_name

        # Get employee info
        employee = slip.employee
        employee_name = slip.employee_name or (employee.full_name if employee else "Unknown")
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
            "organization_name": organization_name or "Company",
            "organization_address": organization_address,
            "logo_url": logo_url,
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
        html = HTML(string=html_content)
        pdf_bytes: bytes = html.write_pdf()

        logger.info("Generated PDF for payslip %s", slip.slip_number)
        return pdf_bytes

    def generate_pdf_by_id(
        self,
        slip_id: UUID,
        organization_name: Optional[str] = None,
        organization_address: Optional[str] = None,
        logo_url: Optional[str] = None,
    ) -> Optional[bytes]:
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
