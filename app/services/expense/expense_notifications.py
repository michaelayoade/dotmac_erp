"""
Expense Notification Service - Email notifications for expense workflows.

Handles:
- Approval request notifications
- Claim approved/rejected notifications
- Limit exceeded warnings
- Pending approval reminders
"""

from __future__ import annotations

import logging
import os
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Session

from app.models.email_profile import EmailModule
from app.models.expense import (
    ExpenseClaim,
    ExpenseLimitRule,
)
from app.services.email import send_email

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee

logger = logging.getLogger(__name__)

__all__ = ["ExpenseNotificationService"]


def _get_app_url() -> str:
    """Get the application URL from environment."""
    return os.getenv("APP_URL", "http://localhost:8000").rstrip("/")


def _employee_full_name(employee: Optional["Employee"]) -> str:
    """Best-effort full name for an employee."""
    if not employee:
        return "Employee"
    full_name = getattr(employee, "full_name", None)
    if isinstance(full_name, str) and full_name:
        return full_name
    person = getattr(employee, "person", None)
    if person:
        name = getattr(person, "name", None)
        if isinstance(name, str) and name:
            return name
        first = getattr(person, "first_name", None)
        last = getattr(person, "last_name", None)
        if isinstance(first, str) or isinstance(last, str):
            first_str = first if isinstance(first, str) else ""
            last_str = last if isinstance(last, str) else ""
            if first_str or last_str:
                return f"{first_str} {last_str}".strip()
    employee_code = getattr(employee, "employee_code", None)
    if isinstance(employee_code, str) and employee_code:
        return employee_code
    return "Employee"


def _employee_first_name(employee: Optional["Employee"]) -> str:
    """Best-effort first name for an employee."""
    if not employee:
        return "there"
    person = getattr(employee, "person", None)
    if person:
        first = getattr(person, "first_name", None)
        if isinstance(first, str) and first:
            return first
        name = getattr(person, "name", None)
        if isinstance(name, str) and name:
            return name.split(" ")[0]
    full_name = _employee_full_name(employee)
    return full_name.split(" ")[0] if full_name else "there"


def _employee_work_email(employee: Optional["Employee"]) -> Optional[str]:
    """Get employee work email if available."""
    if not employee:
        return None
    work_email = getattr(employee, "work_email", None)
    if isinstance(work_email, str) and work_email:
        return work_email
    return None


class ExpenseNotificationService:
    """
    Service for sending expense-related email notifications.

    All methods are idempotent and fail gracefully - notification
    failures should not block expense workflow operations.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # =========================================================================
    # Approval Notifications
    # =========================================================================

    def notify_approval_needed(
        self,
        claim: ExpenseClaim,
        approver: "Employee",
        *,
        is_escalation: bool = False,
        escalated_from: Optional[str] = None,
    ) -> bool:
        """
        Notify an approver that a claim needs their approval.

        Args:
            claim: The expense claim pending approval
            approver: The employee who needs to approve
            is_escalation: Whether this is an escalated approval
            escalated_from: Name of person who escalated (if applicable)

        Returns:
            True if notification was sent successfully
        """
        if not approver.work_email:
            logger.warning(f"No email for approver {approver.employee_id}")
            return False

        # Build claimant name
        claimant_name = "An employee"
        if claim.employee:
            claimant_name = _employee_full_name(claim.employee)

        # Format amount
        amount = claim.total_claimed_amount
        currency = claim.currency_code

        # Build claim URL
        app_url = _get_app_url()
        claim_url = f"{app_url}/expense/claims/{claim.claim_id}"

        # Subject
        subject = f"Expense Approval Needed: {claim.claim_number}"
        if is_escalation:
            subject = f"[ESCALATED] {subject}"

        # Build email body
        escalation_note = ""
        if is_escalation and escalated_from:
            escalation_note = f"""
            <p style="color: #856404; background-color: #fff3cd; padding: 10px; border-radius: 4px;">
                <strong>Note:</strong> This approval was escalated from {escalated_from}.
            </p>
            """

        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #333;">Expense Approval Required</h2>

            <p>Hi {_employee_first_name(approver)},</p>

            <p>{claimant_name} has submitted an expense claim that requires your approval:</p>

            {escalation_note}

            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Claim Number:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{claim.claim_number}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Amount:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{currency} {amount:,.2f}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Purpose:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{claim.purpose}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Date:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{claim.claim_date}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Items:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{len(claim.items)} expense item(s)</td>
                </tr>
            </table>

            <p>
                <a href="{claim_url}"
                   style="display: inline-block; background-color: #007bff; color: white;
                          padding: 12px 24px; text-decoration: none; border-radius: 4px;">
                    Review Claim
                </a>
            </p>

            <p style="color: #666; font-size: 12px; margin-top: 30px;">
                This is an automated notification from the expense management system.
            </p>
        </div>
        """

        body_text = f"""
Expense Approval Required

Hi {_employee_first_name(approver)},

{claimant_name} has submitted an expense claim that requires your approval:

Claim Number: {claim.claim_number}
Amount: {currency} {amount:,.2f}
Purpose: {claim.purpose}
Date: {claim.claim_date}
Items: {len(claim.items)} expense item(s)

Review the claim at: {claim_url}

This is an automated notification from the expense management system.
        """

        try:
            return send_email(
                self.db,
                approver.work_email,
                subject,
                body_html,
                body_text,
                module=EmailModule.EXPENSE,
                organization_id=claim.organization_id,
            )
        except Exception as e:
            logger.error(f"Failed to send approval notification: {e}")
            return False

    def notify_claim_approved(
        self,
        claim: ExpenseClaim,
        *,
        approver_name: Optional[str] = None,
    ) -> bool:
        """
        Notify the claimant that their expense claim was approved.

        Args:
            claim: The approved expense claim
            approver_name: Name of the approver (optional)

        Returns:
            True if notification was sent successfully
        """
        if not claim.employee or not claim.employee.work_email:
            logger.warning(f"No email for claimant on claim {claim.claim_id}")
            return False

        employee = claim.employee
        amount = claim.total_approved_amount or claim.total_claimed_amount
        currency = claim.currency_code
        net_payable = claim.net_payable_amount or amount

        app_url = _get_app_url()
        claim_url = f"{app_url}/expense/claims/{claim.claim_id}"

        subject = f"Expense Claim Approved: {claim.claim_number}"

        approver_info = ""
        if approver_name:
            approver_info = f"<p>Approved by: <strong>{approver_name}</strong></p>"

        advance_note = ""
        if claim.advance_adjusted and claim.advance_adjusted > Decimal("0"):
            advance_note = f"""
            <p style="background-color: #f8f9fa; padding: 10px; border-radius: 4px;">
                <strong>Note:</strong> {currency} {claim.advance_adjusted:,.2f} has been adjusted
                against your cash advance. Net payable amount: {currency} {net_payable:,.2f}
            </p>
            """

        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #28a745;">Expense Claim Approved</h2>

            <p>Hi {_employee_first_name(employee)},</p>

            <p>Great news! Your expense claim has been approved.</p>

            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Claim Number:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{claim.claim_number}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Approved Amount:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; color: #28a745;">
                        <strong>{currency} {amount:,.2f}</strong>
                    </td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Purpose:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{claim.purpose}</td>
                </tr>
            </table>

            {approver_info}
            {advance_note}

            <p>Your reimbursement will be processed according to the payment schedule.</p>

            <p>
                <a href="{claim_url}"
                   style="display: inline-block; background-color: #28a745; color: white;
                          padding: 12px 24px; text-decoration: none; border-radius: 4px;">
                    View Claim Details
                </a>
            </p>
        </div>
        """

        body_text = f"""
Expense Claim Approved

Hi {_employee_first_name(employee)},

Great news! Your expense claim has been approved.

Claim Number: {claim.claim_number}
Approved Amount: {currency} {amount:,.2f}
Purpose: {claim.purpose}

Your reimbursement will be processed according to the payment schedule.

View claim at: {claim_url}
        """

        try:
            to_email = _employee_work_email(employee)
            if not to_email:
                return False
            return send_email(
                self.db,
                to_email,
                subject,
                body_html,
                body_text,
                module=EmailModule.EXPENSE,
                organization_id=claim.organization_id,
            )
        except Exception as e:
            logger.error(f"Failed to send approval notification: {e}")
            return False

    def notify_claim_rejected(
        self,
        claim: ExpenseClaim,
        reason: str,
        *,
        approver_name: Optional[str] = None,
    ) -> bool:
        """
        Notify the claimant that their expense claim was rejected.

        Args:
            claim: The rejected expense claim
            reason: Reason for rejection
            approver_name: Name of the approver (optional)

        Returns:
            True if notification was sent successfully
        """
        if not claim.employee or not claim.employee.work_email:
            logger.warning(f"No email for claimant on claim {claim.claim_id}")
            return False

        employee = claim.employee
        amount = claim.total_claimed_amount
        currency = claim.currency_code

        app_url = _get_app_url()
        claim_url = f"{app_url}/expense/claims/{claim.claim_id}"

        subject = f"Expense Claim Rejected: {claim.claim_number}"

        approver_info = ""
        if approver_name:
            approver_info = f"<p>Reviewed by: <strong>{approver_name}</strong></p>"

        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #dc3545;">Expense Claim Rejected</h2>

            <p>Hi {_employee_first_name(employee)},</p>

            <p>Unfortunately, your expense claim has been rejected.</p>

            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Claim Number:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{claim.claim_number}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Amount:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{currency} {amount:,.2f}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Purpose:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{claim.purpose}</td>
                </tr>
            </table>

            {approver_info}

            <div style="background-color: #f8d7da; padding: 15px; border-radius: 4px; margin: 20px 0;">
                <strong>Reason for rejection:</strong>
                <p style="margin: 10px 0 0 0;">{reason}</p>
            </div>

            <p>You may update your claim and resubmit if appropriate.</p>

            <p>
                <a href="{claim_url}"
                   style="display: inline-block; background-color: #6c757d; color: white;
                          padding: 12px 24px; text-decoration: none; border-radius: 4px;">
                    View Claim Details
                </a>
            </p>
        </div>
        """

        body_text = f"""
Expense Claim Rejected

Hi {_employee_first_name(employee)},

Unfortunately, your expense claim has been rejected.

Claim Number: {claim.claim_number}
Amount: {currency} {amount:,.2f}
Purpose: {claim.purpose}

Reason for rejection:
{reason}

You may update your claim and resubmit if appropriate.

View claim at: {claim_url}
        """

        try:
            to_email = _employee_work_email(employee)
            if not to_email:
                return False
            return send_email(
                self.db,
                to_email,
                subject,
                body_html,
                body_text,
                module=EmailModule.EXPENSE,
                organization_id=claim.organization_id,
            )
        except Exception as e:
            logger.error(f"Failed to send rejection notification: {e}")
            return False

    # =========================================================================
    # Limit Notifications
    # =========================================================================

    def notify_limit_exceeded(
        self,
        claim: ExpenseClaim,
        rule: ExpenseLimitRule,
        *,
        is_blocked: bool = False,
    ) -> bool:
        """
        Notify the claimant that their claim exceeds spending limits.

        Args:
            claim: The expense claim that exceeded limits
            rule: The limit rule that was triggered
            is_blocked: Whether the claim was blocked (vs. warning)

        Returns:
            True if notification was sent successfully
        """
        if not claim.employee or not claim.employee.work_email:
            logger.warning(f"No email for claimant on claim {claim.claim_id}")
            return False

        employee = claim.employee
        amount = claim.total_claimed_amount
        currency = claim.currency_code

        app_url = _get_app_url()
        claim_url = f"{app_url}/expense/claims/{claim.claim_id}"

        if is_blocked:
            subject = f"Expense Claim Blocked: Limit Exceeded - {claim.claim_number}"
            status_color = "#dc3545"
            status_text = "BLOCKED"
            action_text = "Please review and adjust your claim before resubmitting."
        else:
            subject = f"Expense Claim Warning: Limit Exceeded - {claim.claim_number}"
            status_color = "#ffc107"
            status_text = "WARNING"
            action_text = (
                "Your claim has been submitted but may require additional approval."
            )

        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: {status_color};">Expense Limit {status_text}</h2>

            <p>Hi {_employee_first_name(employee)},</p>

            <p>Your expense claim has triggered a spending limit:</p>

            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Claim Number:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{claim.claim_number}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Claim Amount:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{currency} {amount:,.2f}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Limit:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{currency} {rule.limit_amount:,.2f}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Rule:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{rule.rule_name}</td>
                </tr>
            </table>

            <p>{action_text}</p>

            <p>
                <a href="{claim_url}"
                   style="display: inline-block; background-color: #6c757d; color: white;
                          padding: 12px 24px; text-decoration: none; border-radius: 4px;">
                    View Claim
                </a>
            </p>
        </div>
        """

        body_text = f"""
Expense Limit {status_text}

Hi {_employee_first_name(employee)},

Your expense claim has triggered a spending limit:

Claim Number: {claim.claim_number}
Claim Amount: {currency} {amount:,.2f}
Limit: {currency} {rule.limit_amount:,.2f}
Rule: {rule.rule_name}

{action_text}

View claim at: {claim_url}
        """

        try:
            to_email = _employee_work_email(employee)
            if not to_email:
                return False
            return send_email(
                self.db,
                to_email,
                subject,
                body_html,
                body_text,
                module=EmailModule.EXPENSE,
                organization_id=claim.organization_id,
            )
        except Exception as e:
            logger.error(f"Failed to send limit notification: {e}")
            return False

    # =========================================================================
    # Reminder Notifications
    # =========================================================================

    def send_pending_approval_reminder(
        self,
        claim: ExpenseClaim,
        approver: "Employee",
        *,
        days_pending: int = 0,
    ) -> bool:
        """
        Send a reminder to an approver about a pending claim.

        Args:
            claim: The pending expense claim
            approver: The employee who needs to approve
            days_pending: Number of days the claim has been pending

        Returns:
            True if notification was sent successfully
        """
        if not approver.work_email:
            logger.warning(f"No email for approver {approver.employee_id}")
            return False

        claimant_name = "An employee"
        if claim.employee:
            claimant_name = _employee_full_name(claim.employee)

        amount = claim.total_claimed_amount
        currency = claim.currency_code

        app_url = _get_app_url()
        claim_url = f"{app_url}/expense/claims/{claim.claim_id}"

        subject = (
            f"Reminder: Expense Claim Awaiting Your Approval - {claim.claim_number}"
        )

        urgency_note = ""
        if days_pending >= 7:
            urgency_note = f"""
            <p style="color: #856404; background-color: #fff3cd; padding: 10px; border-radius: 4px;">
                <strong>This claim has been pending for {days_pending} days.</strong>
                Please review at your earliest convenience.
            </p>
            """

        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #17a2b8;">Pending Approval Reminder</h2>

            <p>Hi {_employee_first_name(approver)},</p>

            <p>This is a reminder that an expense claim is awaiting your approval:</p>

            {urgency_note}

            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Claim Number:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{claim.claim_number}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>From:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{claimant_name}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Amount:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{currency} {amount:,.2f}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Submitted:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{claim.claim_date}</td>
                </tr>
            </table>

            <p>
                <a href="{claim_url}"
                   style="display: inline-block; background-color: #17a2b8; color: white;
                          padding: 12px 24px; text-decoration: none; border-radius: 4px;">
                    Review Now
                </a>
            </p>
        </div>
        """

        body_text = f"""
Pending Approval Reminder

Hi {_employee_first_name(approver)},

This is a reminder that an expense claim is awaiting your approval:

Claim Number: {claim.claim_number}
From: {claimant_name}
Amount: {currency} {amount:,.2f}
Submitted: {claim.claim_date}

This claim has been pending for {days_pending} days.

Review now at: {claim_url}
        """

        try:
            return send_email(
                self.db,
                approver.work_email,
                subject,
                body_html,
                body_text,
                module=EmailModule.EXPENSE,
                organization_id=claim.organization_id,
            )
        except Exception as e:
            logger.error(f"Failed to send reminder notification: {e}")
            return False

    def notify_claim_paid(
        self,
        claim: ExpenseClaim,
        *,
        payment_reference: Optional[str] = None,
        payment_date: Optional[date] = None,
    ) -> bool:
        """
        Notify the claimant that their expense has been paid.

        Args:
            claim: The paid expense claim
            payment_reference: Payment reference number
            payment_date: Date of payment

        Returns:
            True if notification was sent successfully
        """
        if not claim.employee or not claim.employee.work_email:
            logger.warning(f"No email for claimant on claim {claim.claim_id}")
            return False

        employee = claim.employee
        amount = (
            claim.net_payable_amount
            or claim.total_approved_amount
            or claim.total_claimed_amount
        )
        currency = claim.currency_code

        app_url = _get_app_url()
        claim_url = f"{app_url}/expense/claims/{claim.claim_id}"

        subject = f"Expense Reimbursement Paid: {claim.claim_number}"

        payment_info = ""
        if payment_reference:
            payment_info = (
                f"<p><strong>Payment Reference:</strong> {payment_reference}</p>"
            )
        if payment_date:
            payment_info += f"<p><strong>Payment Date:</strong> {payment_date}</p>"

        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #28a745;">Expense Reimbursement Paid</h2>

            <p>Hi {_employee_first_name(employee)},</p>

            <p>Your expense reimbursement has been processed!</p>

            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Claim Number:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{claim.claim_number}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Amount Paid:</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; color: #28a745;">
                        <strong>{currency} {amount:,.2f}</strong>
                    </td>
                </tr>
            </table>

            {payment_info}

            <p>
                <a href="{claim_url}"
                   style="display: inline-block; background-color: #28a745; color: white;
                          padding: 12px 24px; text-decoration: none; border-radius: 4px;">
                    View Details
                </a>
            </p>
        </div>
        """

        body_text = f"""
Expense Reimbursement Paid

Hi {_employee_first_name(employee)},

Your expense reimbursement has been processed!

Claim Number: {claim.claim_number}
Amount Paid: {currency} {amount:,.2f}
{f"Payment Reference: {payment_reference}" if payment_reference else ""}
{f"Payment Date: {payment_date}" if payment_date else ""}

View details at: {claim_url}
        """

        try:
            to_email = _employee_work_email(employee)
            if not to_email:
                return False
            return send_email(
                self.db,
                to_email,
                subject,
                body_html,
                body_text,
                module=EmailModule.EXPENSE,
                organization_id=claim.organization_id,
            )
        except Exception as e:
            logger.error(f"Failed to send payment notification: {e}")
            return False


# Module-level instance factory
def get_expense_notification_service(db: Session) -> ExpenseNotificationService:
    """Get an instance of ExpenseNotificationService."""
    return ExpenseNotificationService(db)
