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
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.email_profile import EmailModule
from app.models.expense import (
    ExpenseClaim,
    ExpenseLimitRule,
)
from app.services.email import employee_can_receive_email, send_email
from app.services.email_branding import render_branded_email

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee

logger = logging.getLogger(__name__)

__all__ = ["ExpenseNotificationService"]


def _get_app_url() -> str:
    """Get the application URL from environment."""
    return os.getenv("APP_URL", "http://localhost:8000").rstrip("/")


def _employee_full_name(employee: Employee | None) -> str:
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


def _employee_first_name(employee: Employee | None) -> str:
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


def _employee_work_email(employee: Employee | None) -> str | None:
    """Get employee work email if available."""
    if not employee or not employee_can_receive_email(employee):
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

    def _send(
        self,
        template: str,
        context: dict,
        *,
        to_email: str,
        subject: str,
        organization_id: UUID | None,
    ) -> bool:
        """Render a branded email template and send it."""
        try:
            body_html, body_text = render_branded_email(
                template,
                context,
                self.db,
                organization_id,
            )
            return send_email(
                self.db,
                to_email,
                subject,
                body_html,
                body_text,
                module=EmailModule.EXPENSE,
                organization_id=organization_id,
            )
        except Exception as e:
            logger.error("Failed to send %s: %s", template, e)
            return False

    # =========================================================================
    # Approval Notifications
    # =========================================================================

    def notify_approval_needed(
        self,
        claim: ExpenseClaim,
        approver: Employee,
        *,
        is_escalation: bool = False,
        escalated_from: str | None = None,
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
        approver_email = _employee_work_email(approver)
        if not approver_email:
            logger.warning("No email for approver %s", approver.employee_id)
            return False

        claimant_name = "An employee"
        if claim.employee:
            claimant_name = _employee_full_name(claim.employee)

        subject = f"Expense Approval Needed: {claim.claim_number}"
        if is_escalation:
            subject = f"[ESCALATED] {subject}"

        app_url = _get_app_url()
        context = {
            "first_name": _employee_first_name(approver),
            "claimant_name": claimant_name,
            "claim_number": claim.claim_number,
            "currency": claim.currency_code,
            "amount": f"{claim.total_claimed_amount:,.2f}",
            "purpose": claim.purpose,
            "claim_date": str(claim.claim_date),
            "item_count": len(claim.items),
            "claim_url": f"{app_url}/expense/claims/{claim.claim_id}",
            "is_escalation": is_escalation,
            "escalated_from": escalated_from,
        }

        return self._send(
            "emails/expense/approval_needed.html",
            context,
            to_email=approver_email,
            subject=subject,
            organization_id=claim.organization_id,
        )

    def notify_claim_approved(
        self,
        claim: ExpenseClaim,
        *,
        approver_name: str | None = None,
    ) -> bool:
        """
        Notify the claimant that their expense claim was approved.

        Args:
            claim: The approved expense claim
            approver_name: Name of the approver (optional)

        Returns:
            True if notification was sent successfully
        """
        if not claim.employee or not _employee_work_email(claim.employee):
            logger.warning("No email for claimant on claim %s", claim.claim_id)
            return False

        employee = claim.employee
        amount = claim.total_approved_amount or claim.total_claimed_amount
        currency = claim.currency_code
        net_payable = claim.net_payable_amount or amount

        advance_note = ""
        if claim.advance_adjusted and claim.advance_adjusted > Decimal("0"):
            advance_note = (
                f"{currency} {claim.advance_adjusted:,.2f} has been adjusted "
                f"against your cash advance. Net payable: {currency} {net_payable:,.2f}"
            )

        app_url = _get_app_url()
        context = {
            "first_name": _employee_first_name(employee),
            "claim_number": claim.claim_number,
            "currency": currency,
            "amount": f"{amount:,.2f}",
            "purpose": claim.purpose,
            "approver_name": approver_name,
            "advance_note": advance_note,
            "claim_url": f"{app_url}/expense/claims/{claim.claim_id}",
        }

        to_email = _employee_work_email(employee)
        if not to_email:
            return False

        return self._send(
            "emails/expense/claim_approved.html",
            context,
            to_email=to_email,
            subject=f"Expense Claim Approved: {claim.claim_number}",
            organization_id=claim.organization_id,
        )

    def notify_claim_rejected(
        self,
        claim: ExpenseClaim,
        reason: str,
        *,
        approver_name: str | None = None,
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
        if not claim.employee or not _employee_work_email(claim.employee):
            logger.warning("No email for claimant on claim %s", claim.claim_id)
            return False

        employee = claim.employee
        app_url = _get_app_url()

        context = {
            "first_name": _employee_first_name(employee),
            "claim_number": claim.claim_number,
            "currency": claim.currency_code,
            "amount": f"{claim.total_claimed_amount:,.2f}",
            "purpose": claim.purpose,
            "approver_name": approver_name,
            "reason": reason,
            "claim_url": f"{app_url}/expense/claims/{claim.claim_id}",
        }

        to_email = _employee_work_email(employee)
        if not to_email:
            return False

        return self._send(
            "emails/expense/claim_rejected.html",
            context,
            to_email=to_email,
            subject=f"Expense Claim Rejected: {claim.claim_number}",
            organization_id=claim.organization_id,
        )

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
        if not claim.employee or not _employee_work_email(claim.employee):
            logger.warning("No email for claimant on claim %s", claim.claim_id)
            return False

        employee = claim.employee
        currency = claim.currency_code

        if is_blocked:
            subject = f"Expense Claim Blocked: Limit Exceeded - {claim.claim_number}"
            status_text = "BLOCKED"
            action_text = "Please review and adjust your claim before resubmitting."
        else:
            subject = f"Expense Claim Warning: Limit Exceeded - {claim.claim_number}"
            status_text = "WARNING"
            action_text = (
                "Your claim has been submitted but may require additional approval."
            )

        app_url = _get_app_url()
        context = {
            "first_name": _employee_first_name(employee),
            "claim_number": claim.claim_number,
            "currency": currency,
            "amount": f"{claim.total_claimed_amount:,.2f}",
            "limit_amount": f"{rule.limit_amount:,.2f}",
            "rule_name": rule.rule_name,
            "status_text": status_text,
            "action_text": action_text,
            "is_blocked": is_blocked,
            "claim_url": f"{app_url}/expense/claims/{claim.claim_id}",
        }

        to_email = _employee_work_email(employee)
        if not to_email:
            return False

        return self._send(
            "emails/expense/limit_exceeded.html",
            context,
            to_email=to_email,
            subject=subject,
            organization_id=claim.organization_id,
        )

    # =========================================================================
    # Reminder Notifications
    # =========================================================================

    def send_pending_approval_reminder(
        self,
        claim: ExpenseClaim,
        approver: Employee,
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
        approver_email = _employee_work_email(approver)
        if not approver_email:
            logger.warning("No email for approver %s", approver.employee_id)
            return False

        claimant_name = "An employee"
        if claim.employee:
            claimant_name = _employee_full_name(claim.employee)

        app_url = _get_app_url()
        context = {
            "first_name": _employee_first_name(approver),
            "claimant_name": claimant_name,
            "claim_number": claim.claim_number,
            "currency": claim.currency_code,
            "amount": f"{claim.total_claimed_amount:,.2f}",
            "claim_date": str(claim.claim_date),
            "days_pending": days_pending,
            "claim_url": f"{app_url}/expense/claims/{claim.claim_id}",
        }

        return self._send(
            "emails/expense/pending_reminder.html",
            context,
            to_email=approver_email,
            subject=f"Reminder: Expense Claim Awaiting Your Approval - {claim.claim_number}",
            organization_id=claim.organization_id,
        )

    def notify_claim_paid(
        self,
        claim: ExpenseClaim,
        *,
        payment_reference: str | None = None,
        payment_date: date | None = None,
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
        if not claim.employee or not _employee_work_email(claim.employee):
            logger.warning("No email for claimant on claim %s", claim.claim_id)
            return False

        employee = claim.employee
        amount = (
            claim.net_payable_amount
            or claim.total_approved_amount
            or claim.total_claimed_amount
        )
        currency = claim.currency_code

        app_url = _get_app_url()
        context = {
            "first_name": _employee_first_name(employee),
            "claim_number": claim.claim_number,
            "currency": currency,
            "amount": f"{amount:,.2f}",
            "payment_reference": payment_reference,
            "payment_date": str(payment_date) if payment_date else None,
            "claim_url": f"{app_url}/expense/claims/{claim.claim_id}",
        }

        to_email = _employee_work_email(employee)
        if not to_email:
            return False

        return self._send(
            "emails/expense/claim_paid.html",
            context,
            to_email=to_email,
            subject=f"Expense Reimbursement Paid: {claim.claim_number}",
            organization_id=claim.organization_id,
        )


# Module-level instance factory
def get_expense_notification_service(db: Session) -> ExpenseNotificationService:
    """Get an instance of ExpenseNotificationService."""
    return ExpenseNotificationService(db)
