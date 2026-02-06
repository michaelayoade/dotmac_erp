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
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Session

from app.services.email import send_email

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.hr.employee_ext import EmployeeCertification
    from app.models.people.perf.appraisal_cycle import AppraisalCycle

logger = logging.getLogger(__name__)

__all__ = ["HRNotificationService"]


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
    employee_number = getattr(employee, "employee_number", None)
    if isinstance(employee_number, str) and employee_number:
        return employee_number
    return "Employee"


def _employee_first_name(employee: Optional["Employee"]) -> str:
    """Best-effort first name for an employee."""
    if not employee:
        return "there"
    full_name = _employee_full_name(employee)
    return full_name.split(" ")[0] if full_name else "there"


def _get_employee_email(employee: Optional["Employee"]) -> Optional[str]:
    """Get employee email address."""
    if not employee:
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


class HRNotificationService:
    """
    Service for sending HR-related email notifications.

    All send methods return True on success, False on failure.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.app_url = _get_app_url()

    def send_probation_ending_notification(
        self,
        employee: "Employee",
        manager: "Employee",
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
        manager_first = _employee_first_name(manager)

        if days_remaining == 0:
            subject = f"Probation Review Due Today: {employee_name}"
            urgency = "today"
        elif days_remaining <= 7:
            subject = f"Probation Review Due Soon: {employee_name}"
            urgency = f"in {days_remaining} days"
        else:
            subject = f"Upcoming Probation Review: {employee_name}"
            urgency = f"in {days_remaining} days"

        employee_url = f"{self.app_url}/people/hr/employees/{employee.employee_id}"

        body = f"""Hi {manager_first},

This is a reminder that the probation period for {employee_name} is ending {urgency}.

Probation End Date: {employee.probation_end_date.strftime("%B %d, %Y") if employee.probation_end_date else "N/A"}

Please review their performance and make a decision about their confirmation.

View Employee: {employee_url}

Best regards,
HR Notifications
"""

        try:
            send_email(
                self.db,
                manager_email,
                subject,
                body_html=body.replace("\n", "<br>"),
                body_text=body,
            )
            logger.info(
                "Sent probation notification for %s to %s",
                employee.employee_id,
                manager_email,
            )
            return True
        except Exception as e:
            logger.error("Failed to send probation notification: %s", e)
            return False

    def send_contract_expiry_notification(
        self,
        employee: "Employee",
        manager: "Employee",
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
        manager_first = _employee_first_name(manager)

        if days_remaining <= 7:
            subject = f"Urgent: Contract Expiring Soon - {employee_name}"
        elif days_remaining <= 14:
            subject = f"Contract Expiry Notice: {employee_name}"
        else:
            subject = f"Upcoming Contract Expiry: {employee_name}"

        employee_url = f"{self.app_url}/people/hr/employees/{employee.employee_id}"

        contract_end_date = getattr(employee, "contract_end_date", None)
        body = f"""Hi {manager_first},

This is to inform you that the contract for {employee_name} will expire in {days_remaining} days.

Contract End Date: {contract_end_date.strftime("%B %d, %Y") if contract_end_date else "N/A"}

Please take necessary action regarding contract renewal or extension.

View Employee: {employee_url}

Best regards,
HR Notifications
"""

        try:
            send_email(
                self.db,
                manager_email,
                subject,
                body_html=body.replace("\n", "<br>"),
                body_text=body,
            )
            logger.info(
                "Sent contract expiry notification for %s to %s",
                employee.employee_id,
                manager_email,
            )
            return True
        except Exception as e:
            logger.error("Failed to send contract expiry notification: %s", e)
            return False

    def send_work_anniversary_notification(
        self,
        employee: "Employee",
        manager: Optional["Employee"],
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
        manager_first = _employee_first_name(manager)

        if is_milestone:
            subject = (
                f"Milestone Anniversary: {employee_name} - {years_of_service} Years!"
            )
        else:
            subject = f"Work Anniversary: {employee_name} - {years_of_service} Year(s)"

        milestone_note = ""
        if is_milestone:
            milestone_note = f"\n\nThis is a significant milestone! Consider recognizing {employee_name}'s dedication and contributions."

        body = f"""Hi {manager_first},

{employee_name} will be celebrating {years_of_service} year(s) with the organization this week!{milestone_note}

This is a great opportunity to acknowledge their contribution to the team.

Best regards,
HR Notifications
"""

        try:
            send_email(
                self.db,
                manager_email,
                subject,
                body_html=body.replace("\n", "<br>"),
                body_text=body,
            )
            logger.info(
                "Sent anniversary notification for %s to %s",
                employee.employee_id,
                manager_email,
            )
            return True
        except Exception as e:
            logger.error("Failed to send anniversary notification: %s", e)
            return False

    def send_birthday_notification(
        self,
        employee: "Employee",
        manager: "Employee",
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
        manager_first = _employee_first_name(manager)

        if is_advance_notice:
            subject = f"Reminder: {employee_name}'s Birthday Tomorrow"
            timing = "tomorrow"
        else:
            subject = f"Today: {employee_name}'s Birthday"
            timing = "today"

        body = f"""Hi {manager_first},

This is a friendly reminder that {employee_name}'s birthday is {timing}.

Consider wishing them a happy birthday!

Best regards,
HR Notifications
"""

        try:
            send_email(
                self.db,
                manager_email,
                subject,
                body_html=body.replace("\n", "<br>"),
                body_text=body,
            )
            logger.info(
                "Sent birthday notification for %s to %s",
                employee.employee_id,
                manager_email,
            )
            return True
        except Exception as e:
            logger.error("Failed to send birthday notification: %s", e)
            return False

    def send_self_assessment_reminder(
        self,
        employee: "Employee",
        cycle: "AppraisalCycle",
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

        employee_first = _employee_first_name(employee)
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

        appraisal_url = f"{self.app_url}/people/perf/appraisals"

        body = f"""Hi {employee_first},

This is a reminder that your self-assessment for the {cycle_name} cycle is {urgency}.

Deadline: {cycle.self_assessment_deadline.strftime("%B %d, %Y") if cycle.self_assessment_deadline else "N/A"}

Please complete your self-assessment to ensure a timely review process.

Complete Self-Assessment: {appraisal_url}

Best regards,
HR Notifications
"""

        try:
            send_email(
                self.db,
                employee_email,
                subject,
                body_html=body.replace("\n", "<br>"),
                body_text=body,
            )
            logger.info(
                "Sent self-assessment reminder to %s",
                employee_email,
            )
            return True
        except Exception as e:
            logger.error("Failed to send self-assessment reminder: %s", e)
            return False

    def send_manager_review_reminder(
        self,
        manager: "Employee",
        employee: "Employee",
        cycle: "AppraisalCycle",
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

        manager_first = _employee_first_name(manager)
        employee_name = _employee_full_name(employee)
        cycle_name = cycle.cycle_name if cycle.cycle_name else "Performance Review"

        if days_remaining == 0:
            subject = f"Due Today: Review for {employee_name}"
        elif days_remaining == 1:
            subject = f"Due Tomorrow: Review for {employee_name}"
        else:
            subject = f"Reminder: {employee_name}'s Review Due in {days_remaining} Days"

        appraisal_url = f"{self.app_url}/people/perf/appraisals"

        body = f"""Hi {manager_first},

This is a reminder that your review for {employee_name} in the {cycle_name} cycle is due in {days_remaining} day(s).

Review Deadline: {cycle.manager_review_deadline.strftime("%B %d, %Y") if cycle.manager_review_deadline else "N/A"}

Please complete the review to ensure a timely appraisal process.

Complete Review: {appraisal_url}

Best regards,
HR Notifications
"""

        try:
            send_email(
                self.db,
                manager_email,
                subject,
                body_html=body.replace("\n", "<br>"),
                body_text=body,
            )
            logger.info(
                "Sent manager review reminder to %s for employee %s",
                manager_email,
                employee.employee_id,
            )
            return True
        except Exception as e:
            logger.error("Failed to send manager review reminder: %s", e)
            return False

    def send_certification_expiry_notification(
        self,
        employee: "Employee",
        certification: "EmployeeCertification",
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

        employee_first = _employee_first_name(employee)
        cert_name = certification.certification_name

        if days_remaining <= 7:
            subject = f"Urgent: Certification Expiring Soon - {cert_name}"
        elif days_remaining <= 30:
            subject = f"Reminder: Certification Expiry - {cert_name}"
        else:
            subject = f"Notice: Certification Renewal Due - {cert_name}"

        body = f"""Hi {employee_first},

Your certification "{cert_name}" is expiring in {days_remaining} days.

Expiry Date: {certification.valid_until.strftime("%B %d, %Y") if certification.valid_until else "N/A"}
Issuing Organization: {certification.issuing_organization or "N/A"}

Please take necessary steps to renew your certification before it expires.

Best regards,
HR Notifications
"""

        try:
            send_email(
                self.db,
                employee_email,
                subject,
                body_html=body.replace("\n", "<br>"),
                body_text=body,
            )
            logger.info(
                "Sent certification expiry notification to %s for cert %s",
                employee_email,
                certification.certification_id,
            )
            return True
        except Exception as e:
            logger.error("Failed to send certification expiry notification: %s", e)
            return False
