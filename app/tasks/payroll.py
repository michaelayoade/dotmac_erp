"""
Payroll Background Tasks - Celery tasks for payroll workflows.

Handles:
- Sending payslip emails with PDF attachments
- Batch notification processing for payroll runs
- Auto-generating draft payroll before month end
"""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date
from typing import Any

from celery import shared_task

from app.db import SessionLocal

logger = logging.getLogger(__name__)


def _last_day_of_month(d: date) -> date:
    """Get last day of the month for given date."""
    _, last_day = monthrange(d.year, d.month)
    return date(d.year, d.month, last_day)


def _get_org_setting(db, org_id, domain, key, default=None):
    """
    Get organization-specific setting with fallback to global.

    Checks for org-specific setting first, then falls back to global (org_id=NULL),
    then to the provided default.

    Args:
        db: Database session
        org_id: Organization UUID
        domain: SettingDomain enum
        key: Setting key string
        default: Default value if not found

    Returns:
        Setting value or default
    """
    from sqlalchemy import select, or_
    from app.models.domain_settings import DomainSetting, SettingValueType

    # Query for org-specific or global setting
    stmt = (
        select(DomainSetting)
        .where(
            DomainSetting.domain == domain,
            DomainSetting.key == key,
            DomainSetting.is_active == True,
            or_(
                DomainSetting.organization_id == org_id,
                DomainSetting.organization_id.is_(None),
            ),
        )
        # Prefer org-specific over global
        .order_by(DomainSetting.organization_id.desc().nullslast())
        .limit(1)
    )

    setting = db.scalar(stmt)
    if not setting:
        return default

    # Extract value based on type
    if setting.value_json is not None:
        return setting.value_json
    if setting.value_text is not None:
        if setting.value_type == SettingValueType.boolean:
            return setting.value_text.lower() in ("true", "1", "yes", "on")
        if setting.value_type == SettingValueType.integer:
            try:
                return int(setting.value_text)
            except (TypeError, ValueError):
                return setting.value_text
        return setting.value_text
    return default


def _first_day_of_month(d: date) -> date:
    """Get first day of the month for given date."""
    return date(d.year, d.month, 1)


@shared_task
def send_payslip_email(slip_id: str, org_id: str) -> dict[str, Any]:
    """
    Send payslip email with PDF attachment to employee.

    Called asynchronously after a payslip is posted to avoid blocking
    the HTTP request. Generates PDF on-demand and sends email.

    Args:
        slip_id: UUID string of the salary slip
        org_id: UUID string of the organization

    Returns:
        Dict with status and any error message
    """
    import uuid

    logger.info("Sending payslip email for slip %s", slip_id)

    result: dict[str, Any] = {
        "success": False,
        "slip_id": slip_id,
        "error": None,
    }

    with SessionLocal() as db:
        try:
            from app.models.people.payroll.salary_slip import SalarySlip
            from app.services.people.payroll.payslip_pdf import PayslipPDFService
            from app.models.email_profile import EmailModule
            from app.services.email import send_email

            slip_uuid = uuid.UUID(slip_id)
            slip = db.get(SalarySlip, slip_uuid)

            if not slip:
                result["error"] = f"Salary slip {slip_id} not found"
                logger.warning(result["error"])
                return result

            employee = slip.employee
            if not employee:
                result["error"] = f"Employee not found for slip {slip_id}"
                logger.warning(result["error"])
                return result

            # Get employee's work email
            email_address = employee.work_email
            if not email_address:
                result["error"] = (
                    f"No email address for employee {employee.employee_id}"
                )
                logger.warning(result["error"])
                return result

            # Generate PDF (fallback to email-only if PDF rendering fails)
            pdf_service = PayslipPDFService(db)
            # Get organization name from employee's organization
            org_name: str | None = None
            if employee.organization:
                org_name = (
                    employee.organization.trading_name
                    or employee.organization.legal_name
                )

            pdf_bytes: bytes | None = None
            pdf_error: str | None = None
            try:
                pdf_bytes = pdf_service.generate_pdf(
                    slip,
                    organization_name=org_name,
                )
            except Exception as e:
                pdf_error = str(e)
                logger.exception(
                    "Payslip PDF generation failed for slip %s: %s",
                    slip_id,
                    e,
                )

            # Prepare email content
            employee_name = slip.employee_name or employee.full_name or "Employee"
            first_name = employee.first_name or employee_name.split()[0]
            period_str = slip.start_date.strftime("%B %Y")

            subject = f"Your Payslip for {period_str} - {slip.slip_number}"

            pdf_notice_html = ""
            pdf_notice_text = ""
            if pdf_error:
                pdf_notice_html = (
                    '<p style="color:#b45309;">'
                    "We couldn’t attach the PDF due to a system error. "
                    "Please contact HR if you need a copy."
                    "</p>"
                )
                pdf_notice_text = (
                    "\nNote: We couldn’t attach the PDF due to a system error. "
                    "Please contact HR if you need a copy.\n"
                )

            body_html = f"""
            <html>
            <body style="font-family: Arial, sans-serif; color: #1e293b; line-height: 1.6;">
                <p>Dear {first_name},</p>

                <p>Your payslip for <strong>{period_str}</strong> is now available.</p>

                <table style="margin: 20px 0; border-collapse: collapse;">
                    <tr>
                        <td style="padding: 8px 16px 8px 0; color: #64748b;">Payslip Number:</td>
                        <td style="padding: 8px 0; font-weight: 600;">{slip.slip_number}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 16px 8px 0; color: #64748b;">Pay Period:</td>
                        <td style="padding: 8px 0;">{slip.start_date.strftime("%d %b")} - {slip.end_date.strftime("%d %b %Y")}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 16px 8px 0; color: #64748b;">Gross Pay:</td>
                        <td style="padding: 8px 0;">{slip.currency_code} {slip.gross_pay:,.2f}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 16px 8px 0; color: #64748b;">Deductions:</td>
                        <td style="padding: 8px 0; color: #dc2626;">({slip.currency_code} {slip.total_deduction:,.2f})</td>
                    </tr>
                    <tr style="border-top: 2px solid #e2e8f0;">
                        <td style="padding: 12px 16px 8px 0; color: #64748b; font-weight: 600;">Net Pay:</td>
                        <td style="padding: 12px 0 8px 0; font-weight: 700; font-size: 18px; color: #0d9488;">{slip.currency_code} {slip.net_pay:,.2f}</td>
                    </tr>
                </table>

                <p>Please find your detailed payslip attached to this email as a PDF document.</p>
                {pdf_notice_html}

                <p style="margin-top: 24px; padding-top: 16px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #94a3b8;">
                    This is an automated message. Please do not reply to this email.<br>
                    For any queries regarding your payslip, please contact HR.
                </p>
            </body>
            </html>
            """

            body_text = f"""
Dear {first_name},

Your payslip for {period_str} is now available.

Payslip Number: {slip.slip_number}
Pay Period: {slip.start_date.strftime("%d %b")} - {slip.end_date.strftime("%d %b %Y")}
Gross Pay: {slip.currency_code} {slip.gross_pay:,.2f}
Deductions: ({slip.currency_code} {slip.total_deduction:,.2f})
Net Pay: {slip.currency_code} {slip.net_pay:,.2f}

Please find your detailed payslip attached to this email as a PDF document.
{pdf_notice_text}

This is an automated message. Please do not reply to this email.
For any queries regarding your payslip, please contact HR.
            """

            # Send email with PDF attachment
            attachments: list[tuple[str, bytes, str]] = []
            if pdf_bytes:
                pdf_filename = (
                    f"Payslip_{slip.slip_number}_{period_str.replace(' ', '_')}.pdf"
                )
                attachments = [(pdf_filename, pdf_bytes, "application/pdf")]

            success = send_email(
                db,
                to_email=email_address,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                attachments=attachments,
                module=EmailModule.PEOPLE_PAYROLL,
                organization_id=slip.organization_id,
            )

            if success:
                result["success"] = True
                logger.info(
                    "Sent payslip email to %s for slip %s",
                    email_address,
                    slip.slip_number,
                )
            else:
                result["error"] = "Email sending failed"
                logger.error(
                    "Failed to send payslip email to %s for slip %s",
                    email_address,
                    slip.slip_number,
                )

        except Exception as e:
            result["error"] = str(e)
            logger.exception("Error sending payslip email for slip %s: %s", slip_id, e)

    return result


@shared_task
def process_payroll_entry_notifications(entry_id: str, org_id: str) -> dict[str, Any]:
    """
    Process notifications for all slips in a payroll entry.

    Called after a payroll run is posted. Sends notifications to all
    employees with posted payslips in the batch.

    Args:
        entry_id: UUID string of the payroll entry
        org_id: UUID string of the organization

    Returns:
        Dict with processing statistics
    """
    import uuid

    logger.info("Processing notifications for payroll entry %s", entry_id)

    results: dict[str, Any] = {
        "processed": 0,
        "skipped": 0,
        "errors": [],
    }
    errors_list: list[str] = results["errors"]

    with SessionLocal() as db:
        try:
            from app.models.people.payroll.payroll_entry import PayrollEntry
            from app.models.people.payroll.salary_slip import SalarySlipStatus
            from app.services.people.payroll.payroll_notifications import (
                PayrollNotificationService,
            )

            entry_uuid = uuid.UUID(entry_id)
            entry = db.get(PayrollEntry, entry_uuid)

            if not entry:
                errors_list.append(f"Payroll entry {entry_id} not found")
                logger.warning("Payroll entry %s not found", entry_id)
                return results

            notification_service = PayrollNotificationService(db)

            for slip in entry.salary_slips or []:
                if slip.status != SalarySlipStatus.POSTED:
                    results["skipped"] += 1
                    continue

                try:
                    employee = slip.employee
                    if not employee:
                        results["skipped"] += 1
                        errors_list.append(f"Slip {slip.slip_id}: no employee")
                        continue

                    notification_service.notify_payslip_posted(
                        slip,
                        employee,
                        queue_email=True,
                    )
                    results["processed"] += 1

                except Exception as e:
                    errors_list.append(f"Slip {slip.slip_id}: {str(e)}")
                    logger.exception(
                        "Error processing notification for slip %s: %s",
                        slip.slip_id,
                        e,
                    )

            db.commit()

        except Exception as e:
            errors_list.append(str(e))
            logger.exception("Error processing payroll entry notifications: %s", e)

    logger.info(
        "Payroll entry %s notifications: %d processed, %d skipped, %d errors",
        entry_id,
        results["processed"],
        results["skipped"],
        len(errors_list),
    )

    return results


@shared_task
def auto_generate_draft_payroll() -> dict[str, Any]:
    """
    Daily task: Generate draft payroll N days before month end.

    For each organization with auto-generation enabled:
    1. Check if today is N days before month end
    2. Skip if payroll already exists for period
    3. Run data completeness check
    4. Generate draft payroll with auto-fetched data
    5. Notify HR/Finance (in-app + email)

    Returns:
        Dict with processing statistics per organization
    """

    from sqlalchemy import select, func

    from app.models.finance.core_org import Organization
    from app.models.people.payroll.payroll_entry import PayrollEntry, PayrollEntryStatus
    from app.models.people.payroll.salary_structure import PayrollFrequency
    from app.models.domain_settings import SettingDomain
    from app.services.people.payroll.payroll_service import PayrollService
    from app.services.people.payroll.data_completeness import PayrollReadinessService

    today = date.today()
    month_end = _last_day_of_month(today)
    days_until_end = (month_end - today).days

    logger.info(
        "Auto-generate draft payroll task started: today=%s, month_end=%s, days_until_end=%d",
        today,
        month_end,
        days_until_end,
    )

    results: dict[str, Any] = {
        "date": str(today),
        "days_until_month_end": days_until_end,
        "organizations_checked": 0,
        "payrolls_generated": [],
        "skipped": [],
        "errors": [],
    }

    with SessionLocal() as db:
        # Get all active organizations
        orgs = db.scalars(
            select(Organization).where(Organization.is_active == True)
        ).all()

        for org in orgs:
            results["organizations_checked"] += 1

            try:
                # Check if auto-generation is enabled for this org
                enabled = _get_org_setting(
                    db,
                    org.organization_id,
                    SettingDomain.payroll,
                    "auto_generate_enabled",
                    default=False,
                )

                if not enabled:
                    continue

                # Check if today is the right day (N days before month end)
                days_before = _get_org_setting(
                    db,
                    org.organization_id,
                    SettingDomain.payroll,
                    "auto_generate_days_before",
                    default=5,
                )

                if days_until_end != days_before:
                    continue

                period_start = _first_day_of_month(today)
                period_end = month_end

                # Check if payroll already exists for this period
                existing_count = (
                    db.scalar(
                        select(func.count(PayrollEntry.entry_id)).where(
                            PayrollEntry.organization_id == org.organization_id,
                            PayrollEntry.start_date == period_start,
                            PayrollEntry.end_date == period_end,
                            PayrollEntry.status != PayrollEntryStatus.CANCELLED,
                        )
                    )
                    or 0
                )

                if existing_count > 0:
                    results["skipped"].append(
                        {
                            "org_id": str(org.organization_id),
                            "org_name": org.legal_name,
                            "reason": "Payroll already exists for period",
                        }
                    )
                    continue

                # Run data completeness check
                readiness_service = PayrollReadinessService(db)
                readiness_report = readiness_service.check_readiness(
                    organization_id=org.organization_id,
                    period_start=period_start,
                    period_end=period_end,
                )

                # Create payroll entry
                payroll_service = PayrollService(db)
                entry = payroll_service.create_payroll_entry(
                    org_id=org.organization_id,
                    posting_date=period_end,
                    start_date=period_start,
                    end_date=period_end,
                    payroll_frequency=PayrollFrequency.MONTHLY,
                    currency_code=org.functional_currency_code or "NGN",
                    notes="Auto-generated draft payroll",
                )

                # Generate salary slips with auto-fetched data
                generation_result = payroll_service.generate_salary_slips_auto(
                    org_id=org.organization_id,
                    entry_id=entry.entry_id,
                    include_attendance=True,
                    include_lwp=True,
                    prorate_joiners=True,
                    prorate_exits=True,
                )

                db.commit()

                # Send notifications
                _notify_draft_ready(
                    db=db,
                    org=org,
                    entry=entry,
                    readiness_report=readiness_report,
                    generation_result=generation_result,
                )

                results["payrolls_generated"].append(
                    {
                        "org_id": str(org.organization_id),
                        "org_name": org.legal_name,
                        "entry_id": str(entry.entry_id),
                        "entry_number": entry.entry_number,
                        "employee_count": generation_result.created,
                        "flagged_for_review": len(generation_result.flagged_for_review),
                        "incomplete_employees": readiness_report.needs_review_count,
                    }
                )

                logger.info(
                    "Generated draft payroll %s for %s: %d employees, %d flagged",
                    entry.entry_number,
                    org.legal_name,
                    generation_result.created,
                    len(generation_result.flagged_for_review),
                )

            except Exception as e:
                logger.exception(
                    "Failed to generate payroll for org %s: %s",
                    org.organization_id,
                    e,
                )
                results["errors"].append(
                    {
                        "org_id": str(org.organization_id),
                        "org_name": getattr(org, "legal_name", "Unknown"),
                        "error": str(e),
                    }
                )
                db.rollback()

    logger.info(
        "Auto-generate draft payroll completed: %d orgs checked, %d generated, %d skipped, %d errors",
        results["organizations_checked"],
        len(results["payrolls_generated"]),
        len(results["skipped"]),
        len(results["errors"]),
    )

    return results


def _notify_draft_ready(
    db,
    org,
    entry,
    readiness_report,
    generation_result,
) -> None:
    """Send in-app and email notifications for draft payroll."""
    from app.services.notification import NotificationService
    from app.services.rbac import get_users_with_permission
    from app.models.notification import (
        EntityType,
        NotificationType,
        NotificationChannel,
    )
    from app.models.domain_settings import SettingDomain
    from app.models.email_profile import EmailModule
    from app.services.email import send_email

    notification_service = NotificationService()

    # Build notification message
    period_name = entry.start_date.strftime("%B %Y")
    message_parts = [
        f"Draft payroll for {period_name} is ready for review.",
        f"{generation_result.created} employees included.",
    ]

    if generation_result.flagged_for_review:
        message_parts.append(
            f"{len(generation_result.flagged_for_review)} slips flagged for review."
        )

    if readiness_report.needs_review_count > 0:
        message_parts.append(
            f"{readiness_report.needs_review_count} employees need data review."
        )

    message = " ".join(message_parts)

    # Get users with payroll permission
    try:
        users = get_users_with_permission(
            db, org.organization_id, "payroll.entry.approve"
        )

        for user in users:
            notification_service.create(
                db,
                organization_id=org.organization_id,
                recipient_id=user.person_id,
                entity_type=EntityType.PAYROLL,
                entity_id=entry.entry_id,
                notification_type=NotificationType.INFO,
                title=f"Draft Payroll Ready: {entry.entry_number}",
                message=message,
                channel=NotificationChannel.BOTH,
                action_url=f"/people/payroll/runs/{entry.entry_id}",
            )
    except Exception as e:
        logger.warning("Failed to send in-app notifications: %s", e)

    # Send email to configured recipients
    try:
        email_recipients = _get_org_setting(
            db,
            org.organization_id,
            SettingDomain.payroll,
            "auto_generate_notify_emails",
            default=[],
        )

        if email_recipients and isinstance(email_recipients, list):
            org_name = org.trading_name or org.legal_name or "Organization"

            # Build email body
            body_html = f"""
            <html>
            <body style="font-family: Arial, sans-serif; color: #1e293b; line-height: 1.6;">
                <h2 style="color: #0d9488;">Draft Payroll Ready</h2>

                <p>A draft payroll for <strong>{period_name}</strong> has been automatically generated and is ready for review.</p>

                <table style="margin: 20px 0; border-collapse: collapse; width: 100%; max-width: 500px;">
                    <tr style="background-color: #f1f5f9;">
                        <td style="padding: 10px; border: 1px solid #e2e8f0;"><strong>Payroll Number</strong></td>
                        <td style="padding: 10px; border: 1px solid #e2e8f0;">{entry.entry_number}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px; border: 1px solid #e2e8f0;"><strong>Period</strong></td>
                        <td style="padding: 10px; border: 1px solid #e2e8f0;">{entry.start_date.strftime("%d %b")} - {entry.end_date.strftime("%d %b %Y")}</td>
                    </tr>
                    <tr style="background-color: #f1f5f9;">
                        <td style="padding: 10px; border: 1px solid #e2e8f0;"><strong>Employees</strong></td>
                        <td style="padding: 10px; border: 1px solid #e2e8f0;">{generation_result.created}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px; border: 1px solid #e2e8f0;"><strong>Flagged for Review</strong></td>
                        <td style="padding: 10px; border: 1px solid #e2e8f0; color: {"#dc2626" if generation_result.flagged_for_review else "#16a34a"};">{len(generation_result.flagged_for_review)}</td>
                    </tr>
                </table>

                <p>Please review and approve the payroll before month end.</p>

                <p style="margin-top: 24px;">
                    <a href="/people/payroll/runs/{entry.entry_id}"
                       style="display: inline-block; background-color: #0d9488; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px;">
                        Review Payroll
                    </a>
                </p>

                <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;">

                <p style="font-size: 12px; color: #94a3b8;">
                    This is an automated message from {org_name}.<br>
                    Payroll was auto-generated based on your organization settings.
                </p>
            </body>
            </html>
            """

            body_text = f"""
Draft Payroll Ready

A draft payroll for {period_name} has been automatically generated and is ready for review.

Payroll Number: {entry.entry_number}
Period: {entry.start_date.strftime("%d %b")} - {entry.end_date.strftime("%d %b %Y")}
Employees: {generation_result.created}
Flagged for Review: {len(generation_result.flagged_for_review)}

Please review and approve the payroll before month end.

---
This is an automated message from {org_name}.
            """

            for email in email_recipients:
                if email and "@" in email:
                    send_email(
                        db,
                        to_email=email,
                        subject=f"Draft Payroll Ready: {entry.entry_number} - {period_name}",
                        body_html=body_html,
                        body_text=body_text,
                        module=EmailModule.PEOPLE_PAYROLL,
                        organization_id=org.organization_id,
                    )

    except Exception as e:
        logger.warning("Failed to send email notifications: %s", e)

    db.commit()
