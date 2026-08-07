"""
Payslip Email Rendering Service.

Builds the email subject/body and applies org branding + email header/footer settings.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.people.payroll.salary_slip import SalarySlip
from app.services.email_branding import render_branded_email
from app.services.settings_cache import get_setting_value


def render_payslip_email(db: Session, slip: SalarySlip) -> tuple[str, str, str]:
    """
    Render subject, HTML body, and text body for a payslip email.
    """
    employee = slip.employee
    employee_name = slip.employee_name or (
        employee.full_name if employee else "Employee"
    )
    first_name = (employee.first_name if employee else None) or employee_name.split()[0]
    period_str = slip.start_date.strftime("%B %Y")

    # The slip's own organization is the authority here; the employee row is a
    # weaker source (it may be unloaded, and it must never widen the scope).
    org_id = slip.organization_id
    if org_id is None and employee and employee.organization_id:
        org_id = employee.organization_id

    header_html = get_setting_value(
        db, SettingDomain.email, "email_header_html", "", organization_id=org_id
    )
    footer_html = get_setting_value(
        db, SettingDomain.email, "email_footer_html", "", organization_id=org_id
    )
    header_text = get_setting_value(
        db, SettingDomain.email, "email_header_text", "", organization_id=org_id
    )
    footer_text = get_setting_value(
        db, SettingDomain.email, "email_footer_text", "", organization_id=org_id
    )

    context = {
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
    body_html, body_text = render_branded_email(
        "emails/payslip.html",
        context,
        db,
        org_id,
    )

    return subject, body_html, body_text
