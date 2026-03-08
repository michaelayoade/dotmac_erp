"""
HR Notification Service - Email notifications for HR workflows.

Handles:
- Probation period notifications
- Contract expiry notifications
- Work anniversary notifications
- Birthday notifications
- Performance review reminders
- Certification expiry warnings
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.email_profile import EmailModule
from app.services.email import employee_can_receive_email, send_email
from app.services.email_branding import render_branded_email

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.hr.employee_ext import EmployeeCertification
    from app.models.people.perf.appraisal_cycle import AppraisalCycle

logger = logging.getLogger(__name__)

__all__ = ["HRNotificationService"]


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
    employee_number = getattr(employee, "employee_number", None)
    if isinstance(employee_number, str) and employee_number:
        return employee_number
    return "Employee"


def _employee_first_name(employee: Employee | None) -> str:
    """Best-effort first name for an employee."""
    if not employee:
        return "there"
    full_name = _employee_full_name(employee)
    return full_name.split(" ")[0] if full_name else "there"


def _get_employee_email(employee: Employee | None) -> str | None:
    """Get employee email address."""
    if not employee or not employee_can_receive_email(employee):
        return None

    # Try company email first
    company_email = getattr(employee, "company_email", None)
    if isinstance(company_email, str) and company_email:
        return company_email

    # Try personal email
    personal_email = getattr(employee, "personal_email", None)
    if isinstance(personal_email, str) and personal_email:
        return personal_email

    # Try person's email
    person = getattr(employee, "person", None)
    if person:
        person_email = getattr(person, "email", None)
        if isinstance(person_email, str) and person_email:
            return person_email

    return None


def _org_id(employee: Employee | None) -> UUID | None:
    """Safely extract organization_id from employee."""
    if employee:
        return getattr(employee, "organization_id", None)
    return None


class HRNotificationService:
    """
    Service for sending HR-related email notifications.

    All send methods return True on success, False on failure.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.app_url = _get_app_url()

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
                body_html=body_html,
                body_text=body_text,
                module=EmailModule.PEOPLE_PAYROLL,
                organization_id=organization_id,
            )
        except Exception as e:
            logger.error("Failed to send %s: %s", template, e)
            return False

    def send_probation_ending_notification(
        self,
        employee: Employee,
        manager: Employee,
        *,
        days_remaining: int,
    ) -> bool:
        """
        Send notification about employee's probation period ending.

        Args:
            employee: The employee on probation
            manager: The employee's manager
            days_remaining: Days until probation ends

        Returns:
            True if notification sent successfully
        """
        manager_email = _get_employee_email(manager)
        if not manager_email:
            logger.warning(
                "Cannot send probation notification: no email for manager %s",
                manager.employee_id,
            )
            return False

        employee_name = _employee_full_name(employee)

        if days_remaining == 0:
            subject = f"Probation Review Due Today: {employee_name}"
            urgency = "today"
        elif days_remaining <= 7:
            subject = f"Probation Review Due Soon: {employee_name}"
            urgency = f"in {days_remaining} days"
        else:
            subject = f"Upcoming Probation Review: {employee_name}"
            urgency = f"in {days_remaining} days"

        probation_end = getattr(employee, "probation_end_date", None)
        context = {
            "manager_first": _employee_first_name(manager),
            "employee_name": employee_name,
            "urgency": urgency,
            "probation_end_date": (
                probation_end.strftime("%B %d, %Y") if probation_end else "N/A"
            ),
            "employee_url": f"{self.app_url}/people/hr/employees/{employee.employee_id}",
        }

        return self._send(
            "emails/hr/probation_ending.html",
            context,
            to_email=manager_email,
            subject=subject,
            organization_id=_org_id(employee),
        )

    def send_contract_expiry_notification(
        self,
        employee: Employee,
        manager: Employee,
        *,
        days_remaining: int,
    ) -> bool:
        """
        Send notification about employee's contract expiring.

        Args:
            employee: The employee with expiring contract
            manager: The employee's manager
            days_remaining: Days until contract expires

        Returns:
            True if notification sent successfully
        """
        manager_email = _get_employee_email(manager)
        if not manager_email:
            logger.warning(
                "Cannot send contract expiry notification: no email for manager %s",
                manager.employee_id,
            )
            return False

        employee_name = _employee_full_name(employee)

        if days_remaining <= 7:
            subject = f"Urgent: Contract Expiring Soon - {employee_name}"
        elif days_remaining <= 14:
            subject = f"Contract Expiry Notice: {employee_name}"
        else:
            subject = f"Upcoming Contract Expiry: {employee_name}"

        contract_end_date = getattr(employee, "contract_end_date", None)
        context = {
            "manager_first": _employee_first_name(manager),
            "employee_name": employee_name,
            "days_remaining": days_remaining,
            "contract_end_date": (
                contract_end_date.strftime("%B %d, %Y") if contract_end_date else "N/A"
            ),
            "employee_url": f"{self.app_url}/people/hr/employees/{employee.employee_id}",
        }

        return self._send(
            "emails/hr/contract_expiry.html",
            context,
            to_email=manager_email,
            subject=subject,
            organization_id=_org_id(employee),
        )

    def send_work_anniversary_notification(
        self,
        employee: Employee,
        manager: Employee | None,
        *,
        years_of_service: int,
        is_milestone: bool = False,
    ) -> bool:
        """
        Send work anniversary notification.

        Args:
            employee: The employee with the anniversary
            manager: The employee's manager (optional)
            years_of_service: Number of years
            is_milestone: True if this is a milestone year (5, 10, 15, etc.)

        Returns:
            True if notification sent successfully
        """
        if not manager:
            return False

        manager_email = _get_employee_email(manager)
        if not manager_email:
            return False

        employee_name = _employee_full_name(employee)

        if is_milestone:
            subject = (
                f"Milestone Anniversary: {employee_name} - {years_of_service} Years!"
            )
        else:
            subject = f"Work Anniversary: {employee_name} - {years_of_service} Year(s)"

        context = {
            "manager_first": _employee_first_name(manager),
            "employee_name": employee_name,
            "years_of_service": years_of_service,
            "is_milestone": is_milestone,
        }

        return self._send(
            "emails/hr/work_anniversary.html",
            context,
            to_email=manager_email,
            subject=subject,
            organization_id=_org_id(employee),
        )

    def send_birthday_notification(
        self,
        employee: Employee,
        manager: Employee,
        *,
        is_advance_notice: bool = True,
    ) -> bool:
        """
        Send birthday notification to manager.

        Args:
            employee: The employee with the birthday
            manager: The employee's manager
            is_advance_notice: True if this is a day-ahead notice

        Returns:
            True if notification sent successfully
        """
        manager_email = _get_employee_email(manager)
        if not manager_email:
            return False

        employee_name = _employee_full_name(employee)

        if is_advance_notice:
            subject = f"Reminder: {employee_name}'s Birthday Tomorrow"
            timing = "tomorrow"
        else:
            subject = f"Today: {employee_name}'s Birthday"
            timing = "today"

        context = {
            "manager_first": _employee_first_name(manager),
            "employee_name": employee_name,
            "timing": timing,
        }

        return self._send(
            "emails/hr/birthday.html",
            context,
            to_email=manager_email,
            subject=subject,
            organization_id=_org_id(employee),
        )

    def send_self_assessment_reminder(
        self,
        employee: Employee,
        cycle: AppraisalCycle,
        *,
        days_remaining: int,
    ) -> bool:
        """
        Send self-assessment deadline reminder to employee.

        Args:
            employee: The employee who needs to complete self-assessment
            cycle: The appraisal cycle
            days_remaining: Days until deadline

        Returns:
            True if notification sent successfully
        """
        employee_email = _get_employee_email(employee)
        if not employee_email:
            return False

        cycle_name = cycle.cycle_name if cycle.cycle_name else "Performance Review"

        if days_remaining == 0:
            subject = f"Due Today: Self-Assessment for {cycle_name}"
            urgency = "due today"
        elif days_remaining == 1:
            subject = f"Due Tomorrow: Self-Assessment for {cycle_name}"
            urgency = "due tomorrow"
        else:
            subject = f"Reminder: Self-Assessment Due in {days_remaining} Days"
            urgency = f"due in {days_remaining} days"

        deadline = getattr(cycle, "self_assessment_deadline", None)
        context = {
            "employee_first": _employee_first_name(employee),
            "cycle_name": cycle_name,
            "urgency": urgency,
            "deadline": (deadline.strftime("%B %d, %Y") if deadline else "N/A"),
            "appraisal_url": f"{self.app_url}/people/perf/appraisals",
        }

        return self._send(
            "emails/hr/self_assessment_reminder.html",
            context,
            to_email=employee_email,
            subject=subject,
            organization_id=cycle.organization_id,
        )

    def send_manager_review_reminder(
        self,
        manager: Employee,
        employee: Employee,
        cycle: AppraisalCycle,
        *,
        days_remaining: int,
    ) -> bool:
        """
        Send manager review deadline reminder.

        Args:
            manager: The manager who needs to complete the review
            employee: The employee being reviewed
            cycle: The appraisal cycle
            days_remaining: Days until deadline

        Returns:
            True if notification sent successfully
        """
        manager_email = _get_employee_email(manager)
        if not manager_email:
            return False

        employee_name = _employee_full_name(employee)
        cycle_name = cycle.cycle_name if cycle.cycle_name else "Performance Review"

        if days_remaining == 0:
            subject = f"Due Today: Review for {employee_name}"
        elif days_remaining == 1:
            subject = f"Due Tomorrow: Review for {employee_name}"
        else:
            subject = f"Reminder: {employee_name}'s Review Due in {days_remaining} Days"

        review_deadline = getattr(cycle, "manager_review_deadline", None)
        context = {
            "manager_first": _employee_first_name(manager),
            "employee_name": employee_name,
            "cycle_name": cycle_name,
            "days_remaining": days_remaining,
            "review_deadline": (
                review_deadline.strftime("%B %d, %Y") if review_deadline else "N/A"
            ),
            "appraisal_url": f"{self.app_url}/people/perf/appraisals",
        }

        return self._send(
            "emails/hr/manager_review_reminder.html",
            context,
            to_email=manager_email,
            subject=subject,
            organization_id=cycle.organization_id,
        )

    def send_certification_expiry_notification(
        self,
        employee: Employee,
        certification: EmployeeCertification,
        *,
        days_remaining: int,
    ) -> bool:
        """
        Send certification expiry notification to employee.

        Args:
            employee: The employee with the expiring certification
            certification: The certification that's expiring
            days_remaining: Days until expiry

        Returns:
            True if notification sent successfully
        """
        employee_email = _get_employee_email(employee)
        if not employee_email:
            return False

        cert_name = certification.certification_name

        if days_remaining <= 7:
            subject = f"Urgent: Certification Expiring Soon - {cert_name}"
        elif days_remaining <= 30:
            subject = f"Reminder: Certification Expiry - {cert_name}"
        else:
            subject = f"Notice: Certification Renewal Due - {cert_name}"

        valid_until = getattr(certification, "valid_until", None)
        context = {
            "employee_first": _employee_first_name(employee),
            "cert_name": cert_name,
            "days_remaining": days_remaining,
            "expiry_date": (
                valid_until.strftime("%B %d, %Y") if valid_until else "N/A"
            ),
            "issuing_org": getattr(certification, "issuing_organization", None)
            or "N/A",
        }

        return self._send(
            "emails/hr/certification_expiry.html",
            context,
            to_email=employee_email,
            subject=subject,
            organization_id=_org_id(employee),
        )
