"""
Payslip Email Rendering Service.

Builds the email subject/body and applies org branding + email header/footer settings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.people.payroll.salary_slip import SalarySlip
from app.services.settings_cache import get_setting_value
from app.templates import templates


def _render_template(name: str, context: dict) -> str:
    template = templates.env.get_template(name)
    return template.render(**context)


def _get_org_email_branding(slip: SalarySlip) -> dict:
    org_name: Optional[str] = None
    org_logo_url: Optional[str] = None
    org_contact_email: Optional[str] = None

    if slip.employee and slip.employee.organization:
        org = slip.employee.organization
        org_name = org.trading_name or org.legal_name
        if org.branding and org.branding.logo_url:
            org_logo_url = org.branding.logo_url
        elif org.logo_url:
            org_logo_url = org.logo_url
        org_contact_email = org.contact_email

    return {
        "brand_name": org_name or "Company",
        "brand_logo_url": org_logo_url,
        "contact_email": org_contact_email,
    }


def render_payslip_email(db: Session, slip: SalarySlip) -> tuple[str, str, str]:
    """
    Render subject, HTML body, and text body for a payslip email.
    """
    employee = slip.employee
    employee_name = slip.employee_name or (employee.full_name if employee else "Employee")
    first_name = (employee.first_name if employee else None) or employee_name.split()[0]
    period_str = slip.start_date.strftime("%B %Y")

    header_html = get_setting_value(db, SettingDomain.email, "email_header_html", "")
    footer_html = get_setting_value(db, SettingDomain.email, "email_footer_html", "")
    header_text = get_setting_value(db, SettingDomain.email, "email_header_text", "")
    footer_text = get_setting_value(db, SettingDomain.email, "email_footer_text", "")

    context = {
        **_get_org_email_branding(slip),
        "employee_name": employee_name,
        "first_name": first_name,
        "period_str": period_str,
        "slip_number": slip.slip_number,
        "start_date": slip.start_date.strftime("%d %b"),
        "end_date": slip.end_date.strftime("%d %b %Y"),
        "currency_code": slip.currency_code,
        "gross_pay": slip.gross_pay,
        "total_deduction": slip.total_deduction,
        "net_pay": slip.net_pay,
        "email_header_html": header_html or "",
        "email_footer_html": footer_html or "",
        "email_header_text": header_text or "",
        "email_footer_text": footer_text or "",
    }

    subject = f"Your Payslip for {period_str} - {slip.slip_number}"
    body_html = _render_template("emails/payslip.html", context)
    body_text = _render_template("emails/payslip.txt", context)

    return subject, body_html, body_text
