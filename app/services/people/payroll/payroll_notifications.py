"""
Payroll Notification Service.

Handles notifications for payroll events (payslip posted, paid, etc.).
"""
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.notification import (
    EntityType,
    NotificationChannel,
    NotificationType,
)
from app.models.people.hr.employee import Employee
from app.models.people.payroll.salary_slip import SalarySlip
from app.services.notification import NotificationService

logger = logging.getLogger(__name__)


class PayrollNotificationService:
    """Service for payroll-related notifications."""

    def __init__(self, db: Session):
        self.db = db
        self._notification_service = NotificationService()

    def notify_payslip_posted(
        self,
        slip: SalarySlip,
        employee: Employee,
        queue_email: bool = True,
    ) -> None:
        """
        Notify employee that their payslip has been posted.

        Creates an in-app notification immediately and optionally queues
        a background task to send an email with PDF attachment.

        Args:
            slip: The posted salary slip
            employee: The employee receiving the payslip
            queue_email: Whether to queue the email task (default True)
        """
        if not employee.person_id:
            logger.warning(
                "Cannot notify for slip %s: employee %s has no person_id",
                slip.slip_number,
                employee.employee_id,
            )
            return

        # Create in-app notification
        self._notification_service.create(
            self.db,
            organization_id=slip.organization_id,
            recipient_id=employee.person_id,
            entity_type=EntityType.PAYROLL,
            entity_id=slip.slip_id,
            notification_type=NotificationType.INFO,
            title=f"Payslip {slip.slip_number} is ready",
            message=(
                f"Your payslip for {slip.start_date.strftime('%B %Y')} "
                f"is now available. Net pay: {slip.currency_code} {slip.net_pay:,.2f}"
            ),
            channel=NotificationChannel.BOTH if queue_email else NotificationChannel.IN_APP,
            action_url=f"/people/self/payslips/{slip.slip_id}",
        )

        logger.info(
            "Created in-app notification for payslip %s to employee %s",
            slip.slip_number,
            employee.employee_id,
        )

        # Queue email task with PDF attachment
        if queue_email:
            try:
                from app.tasks.payroll import send_payslip_email
                send_payslip_email.delay(
                    str(slip.slip_id),
                    str(slip.organization_id),
                )
                logger.info(
                    "Queued email task for payslip %s",
                    slip.slip_number,
                )
            except Exception as e:
                logger.exception(
                    "Failed to queue email task for payslip %s: %s",
                    slip.slip_number,
                    e,
                )

    def notify_payslip_paid(
        self,
        slip: SalarySlip,
        employee: Employee,
    ) -> None:
        """
        Notify employee that their payslip has been paid.

        Args:
            slip: The paid salary slip
            employee: The employee who was paid
        """
        if not employee.person_id:
            logger.warning(
                "Cannot notify for slip %s: employee %s has no person_id",
                slip.slip_number,
                employee.employee_id,
            )
            return

        payment_info = ""
        if slip.payment_reference:
            payment_info = f" Reference: {slip.payment_reference}"

        # Create in-app + email notification
        self._notification_service.create(
            self.db,
            organization_id=slip.organization_id,
            recipient_id=employee.person_id,
            entity_type=EntityType.PAYROLL,
            entity_id=slip.slip_id,
            notification_type=NotificationType.COMPLETED,
            title=f"Payment processed: {slip.slip_number}",
            message=(
                f"Your salary of {slip.currency_code} {slip.net_pay:,.2f} "
                f"for {slip.start_date.strftime('%B %Y')} has been paid.{payment_info}"
            ),
            channel=NotificationChannel.BOTH,
            action_url=f"/people/self/payslips/{slip.slip_id}",
        )

        logger.info(
            "Created payment notification for payslip %s to employee %s",
            slip.slip_number,
            employee.employee_id,
        )

    def notify_payroll_entry_processed(
        self,
        entry_id: UUID,
        org_id: UUID,
        slip_count: int,
        total_net_pay: float,
        currency_code: str = "NGN",
        recipient_id: Optional[UUID] = None,
    ) -> None:
        """
        Notify HR/Finance that a payroll entry has been processed.

        Args:
            entry_id: The payroll entry ID
            org_id: Organization ID
            slip_count: Number of slips processed
            total_net_pay: Total net pay amount
            currency_code: Currency code
            recipient_id: Person to notify (typically HR manager)
        """
        if not recipient_id:
            logger.debug("No recipient specified for payroll entry notification")
            return

        self._notification_service.create(
            self.db,
            organization_id=org_id,
            recipient_id=recipient_id,
            entity_type=EntityType.PAYROLL,
            entity_id=entry_id,
            notification_type=NotificationType.INFO,
            title="Payroll batch processed",
            message=(
                f"{slip_count} payslips processed. "
                f"Total disbursement: {currency_code} {total_net_pay:,.2f}"
            ),
            channel=NotificationChannel.IN_APP,
            action_url=f"/people/payroll/runs/{entry_id}",
        )


# Singleton-ish factory function for service creation
def get_payroll_notification_service(db: Session) -> PayrollNotificationService:
    """Get a PayrollNotificationService instance."""
    return PayrollNotificationService(db)
