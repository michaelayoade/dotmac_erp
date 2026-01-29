"""
Payroll Background Tasks - Celery tasks for payroll workflows.

Handles:
- Sending payslip emails with PDF attachments
- Batch notification processing for payroll runs
"""
from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from app.db import SessionLocal

logger = logging.getLogger(__name__)


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
                result["error"] = f"No email address for employee {employee.employee_id}"
                logger.warning(result["error"])
                return result

            # Generate PDF
            pdf_service = PayslipPDFService(db)
            # Get organization name from employee's organization
            org_name: str | None = None
            if employee.organization:
                org_name = employee.organization.trading_name or employee.organization.legal_name

            pdf_bytes = pdf_service.generate_pdf(
                slip,
                organization_name=org_name,
            )

            # Prepare email content
            employee_name = slip.employee_name or employee.full_name or "Employee"
            first_name = employee.first_name or employee_name.split()[0]
            period_str = slip.start_date.strftime("%B %Y")

            subject = f"Your Payslip for {period_str} - {slip.slip_number}"

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
                        <td style="padding: 8px 0;">{slip.start_date.strftime('%d %b')} - {slip.end_date.strftime('%d %b %Y')}</td>
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
Pay Period: {slip.start_date.strftime('%d %b')} - {slip.end_date.strftime('%d %b %Y')}
Gross Pay: {slip.currency_code} {slip.gross_pay:,.2f}
Deductions: ({slip.currency_code} {slip.total_deduction:,.2f})
Net Pay: {slip.currency_code} {slip.net_pay:,.2f}

Please find your detailed payslip attached to this email as a PDF document.

This is an automated message. Please do not reply to this email.
For any queries regarding your payslip, please contact HR.
            """

            # Send email with PDF attachment
            pdf_filename = f"Payslip_{slip.slip_number}_{period_str.replace(' ', '_')}.pdf"
            attachments: list[tuple[str, bytes, str]] = [
                (pdf_filename, pdf_bytes, "application/pdf")
            ]

            success = send_email(
                db,
                to_email=email_address,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                attachments=attachments,
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
            logger.exception(
                "Error processing payroll entry notifications: %s", e
            )

    logger.info(
        "Payroll entry %s notifications: %d processed, %d skipped, %d errors",
        entry_id,
        results["processed"],
        results["skipped"],
        len(errors_list),
    )

    return results
