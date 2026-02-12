"""
General Notification Service.

Handles in-app and email notifications for all app modules.
"""

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models.notification import (
    EntityType,
    Notification,
    NotificationChannel,
    NotificationType,
)

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for managing app-wide notifications."""

    def create(
        self,
        db: Session,
        organization_id: uuid.UUID,
        recipient_id: uuid.UUID,
        entity_type: EntityType,
        entity_id: uuid.UUID,
        notification_type: NotificationType,
        title: str,
        message: str,
        *,
        channel: NotificationChannel = NotificationChannel.IN_APP,
        action_url: str | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """
        Create a notification.

        Args:
            db: Database session
            organization_id: Organization UUID
            recipient_id: Person UUID to receive the notification
            entity_type: Type of entity (TICKET, EXPENSE, etc.)
            entity_id: UUID of the related entity
            notification_type: Type of notification event
            title: Short title for display
            message: Full notification message
            channel: Delivery channel (IN_APP, EMAIL, or BOTH)
            action_url: URL to navigate to when clicking notification
            actor_id: Person who triggered the notification

        Returns:
            Created notification
        """
        notification = Notification(
            organization_id=organization_id,
            recipient_id=recipient_id,
            entity_type=entity_type,
            entity_id=entity_id,
            notification_type=notification_type,
            channel=channel,
            title=title,
            message=message,
            action_url=action_url,
            actor_id=actor_id,
        )
        db.add(notification)
        db.flush()

        logger.debug(
            "Created %s/%s notification for %s",
            entity_type.value,
            notification_type.value,
            recipient_id,
        )

        return notification

    # ========================================================================
    # Ticket-specific helpers
    # ========================================================================

    def notify_ticket_assigned(
        self,
        db: Session,
        organization_id: uuid.UUID,
        ticket_id: uuid.UUID,
        ticket_number: str,
        ticket_subject: str,
        assignee_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """Notify an employee they've been assigned a ticket."""
        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=assignee_id,
            entity_type=EntityType.TICKET,
            entity_id=ticket_id,
            notification_type=NotificationType.ASSIGNED,
            title=f"Ticket {ticket_number} assigned to you",
            message=f"You have been assigned ticket '{ticket_subject}'",
            channel=NotificationChannel.BOTH,
            action_url=f"/support/tickets/{ticket_number}",
            actor_id=actor_id,
        )

    def notify_ticket_status_change(
        self,
        db: Session,
        organization_id: uuid.UUID,
        ticket_id: uuid.UUID,
        ticket_number: str,
        recipient_id: uuid.UUID,
        old_status: str,
        new_status: str,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """Notify about a ticket status change."""
        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=recipient_id,
            entity_type=EntityType.TICKET,
            entity_id=ticket_id,
            notification_type=NotificationType.STATUS_CHANGE,
            title=f"Ticket {ticket_number} status changed",
            message=f"Status changed from {old_status} to {new_status}",
            action_url=f"/support/tickets/{ticket_number}",
            actor_id=actor_id,
        )

    def notify_ticket_resolved(
        self,
        db: Session,
        organization_id: uuid.UUID,
        ticket_id: uuid.UUID,
        ticket_number: str,
        ticket_subject: str,
        recipient_id: uuid.UUID,
        resolver_name: str,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """Notify that a ticket has been resolved."""
        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=recipient_id,
            entity_type=EntityType.TICKET,
            entity_id=ticket_id,
            notification_type=NotificationType.RESOLVED,
            title=f"Ticket {ticket_number} resolved",
            message=f"Your ticket '{ticket_subject}' has been resolved by {resolver_name}",
            channel=NotificationChannel.BOTH,
            action_url=f"/support/tickets/{ticket_number}",
            actor_id=actor_id,
        )

    def notify_ticket_comment(
        self,
        db: Session,
        organization_id: uuid.UUID,
        ticket_id: uuid.UUID,
        ticket_number: str,
        recipient_id: uuid.UUID,
        commenter_name: str,
        is_internal: bool = False,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """Notify about a new comment on a ticket."""
        action = "added an internal note" if is_internal else "commented"
        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=recipient_id,
            entity_type=EntityType.TICKET,
            entity_id=ticket_id,
            notification_type=NotificationType.COMMENT,
            title=f"New comment on {ticket_number}",
            message=f"{commenter_name} {action} on the ticket",
            action_url=f"/support/tickets/{ticket_number}#comments",
            actor_id=actor_id,
        )

    # ========================================================================
    # Expense-specific helpers
    # ========================================================================

    def notify_expense_submitted(
        self,
        db: Session,
        organization_id: uuid.UUID,
        claim_id: uuid.UUID,
        claim_number: str,
        recipient_id: uuid.UUID,
        submitter_name: str,
        amount: str,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """Notify approver of a submitted expense claim."""
        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=recipient_id,
            entity_type=EntityType.EXPENSE,
            entity_id=claim_id,
            notification_type=NotificationType.SUBMITTED,
            title=f"Expense {claim_number} needs approval",
            message=f"{submitter_name} submitted an expense claim for {amount}",
            channel=NotificationChannel.BOTH,
            action_url=f"/expense/claims/{claim_id}",
            actor_id=actor_id,
        )

    def notify_expense_approved(
        self,
        db: Session,
        organization_id: uuid.UUID,
        claim_id: uuid.UUID,
        claim_number: str,
        recipient_id: uuid.UUID,
        approver_name: str,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """Notify employee their expense was approved."""
        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=recipient_id,
            entity_type=EntityType.EXPENSE,
            entity_id=claim_id,
            notification_type=NotificationType.APPROVED,
            title=f"Expense {claim_number} approved",
            message=f"Your expense claim was approved by {approver_name}",
            channel=NotificationChannel.BOTH,
            action_url=f"/expense/claims/{claim_id}",
            actor_id=actor_id,
        )

    def notify_expense_rejected(
        self,
        db: Session,
        organization_id: uuid.UUID,
        claim_id: uuid.UUID,
        claim_number: str,
        recipient_id: uuid.UUID,
        rejector_name: str,
        reason: str | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """Notify employee their expense was rejected."""
        message = f"Your expense claim was rejected by {rejector_name}"
        if reason:
            message += f": {reason}"

        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=recipient_id,
            entity_type=EntityType.EXPENSE,
            entity_id=claim_id,
            notification_type=NotificationType.REJECTED,
            title=f"Expense {claim_number} rejected",
            message=message,
            channel=NotificationChannel.BOTH,
            action_url=f"/expense/claims/{claim_id}",
            actor_id=actor_id,
        )

    # ========================================================================
    # Discipline-specific helpers
    # ========================================================================

    def notify_discipline_query_issued(
        self,
        db: Session,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        case_number: str,
        employee_id: uuid.UUID,
        response_due_date: str,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """Notify employee that a disciplinary query has been issued."""
        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=employee_id,
            entity_type=EntityType.DISCIPLINE,
            entity_id=case_id,
            notification_type=NotificationType.ALERT,
            title=f"Disciplinary Query Issued - {case_number}",
            message=f"A formal query has been issued regarding case {case_number}. Please respond by {response_due_date}.",
            channel=NotificationChannel.BOTH,
            action_url=f"/people/self-service/discipline/{case_id}",
            actor_id=actor_id,
        )

    def notify_discipline_hearing_scheduled(
        self,
        db: Session,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        case_number: str,
        employee_id: uuid.UUID,
        hearing_date: str,
        hearing_location: str | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """Notify employee of scheduled disciplinary hearing."""
        location_text = f" at {hearing_location}" if hearing_location else ""
        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=employee_id,
            entity_type=EntityType.DISCIPLINE,
            entity_id=case_id,
            notification_type=NotificationType.ALERT,
            title=f"Hearing Scheduled - {case_number}",
            message=f"A disciplinary hearing for case {case_number} has been scheduled for {hearing_date}{location_text}.",
            channel=NotificationChannel.BOTH,
            action_url=f"/people/self-service/discipline/{case_id}",
            actor_id=actor_id,
        )

    def notify_discipline_decision_made(
        self,
        db: Session,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        case_number: str,
        employee_id: uuid.UUID,
        appeal_deadline: str | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """Notify employee that a decision has been made on their case."""
        message = f"A decision has been reached for disciplinary case {case_number}."
        if appeal_deadline:
            message += f" You may appeal by {appeal_deadline}."
        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=employee_id,
            entity_type=EntityType.DISCIPLINE,
            entity_id=case_id,
            notification_type=NotificationType.STATUS_CHANGE,
            title=f"Decision Made - {case_number}",
            message=message,
            channel=NotificationChannel.BOTH,
            action_url=f"/people/self-service/discipline/{case_id}",
            actor_id=actor_id,
        )

    def notify_discipline_case_closed(
        self,
        db: Session,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        case_number: str,
        employee_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """Notify employee that their disciplinary case has been closed."""
        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=employee_id,
            entity_type=EntityType.DISCIPLINE,
            entity_id=case_id,
            notification_type=NotificationType.INFO,
            title=f"Case Closed - {case_number}",
            message=f"Disciplinary case {case_number} has been closed.",
            channel=NotificationChannel.BOTH,
            action_url=f"/people/self-service/discipline/{case_id}",
            actor_id=actor_id,
        )

    def notify_discipline_response_received(
        self,
        db: Session,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        case_number: str,
        hr_recipient_id: uuid.UUID,
        employee_name: str,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """Notify HR that employee has submitted a response."""
        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=hr_recipient_id,
            entity_type=EntityType.DISCIPLINE,
            entity_id=case_id,
            notification_type=NotificationType.SUBMITTED,
            title=f"Response Received - {case_number}",
            message=f"{employee_name} has submitted a response to disciplinary case {case_number}.",
            channel=NotificationChannel.BOTH,
            action_url=f"/people/hr/discipline/{case_id}",
            actor_id=actor_id,
        )

    def notify_discipline_appeal_filed(
        self,
        db: Session,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        case_number: str,
        hr_recipient_id: uuid.UUID,
        employee_name: str,
        actor_id: uuid.UUID | None = None,
    ) -> Notification:
        """Notify HR that employee has filed an appeal."""
        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=hr_recipient_id,
            entity_type=EntityType.DISCIPLINE,
            entity_id=case_id,
            notification_type=NotificationType.SUBMITTED,
            title=f"Appeal Filed - {case_number}",
            message=f"{employee_name} has filed an appeal for disciplinary case {case_number}.",
            channel=NotificationChannel.BOTH,
            action_url=f"/people/hr/discipline/{case_id}",
            actor_id=actor_id,
        )

    def notify_discipline_response_due_reminder(
        self,
        db: Session,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        case_number: str,
        employee_id: uuid.UUID,
        due_date: str,
    ) -> Notification:
        """Remind employee that response is due soon."""
        return self.create(
            db,
            organization_id=organization_id,
            recipient_id=employee_id,
            entity_type=EntityType.DISCIPLINE,
            entity_id=case_id,
            notification_type=NotificationType.DUE_SOON,
            title=f"Response Due Soon - {case_number}",
            message=f"Your response to disciplinary case {case_number} is due on {due_date}. Please respond promptly.",
            channel=NotificationChannel.BOTH,
            action_url=f"/people/self-service/discipline/{case_id}",
        )

    # ========================================================================
    # Query Methods
    # ========================================================================

    def get_unread_count(
        self,
        db: Session,
        recipient_id: uuid.UUID,
        organization_id: uuid.UUID | None = None,
    ) -> int:
        """Get count of unread notifications for a user."""
        query = select(Notification).where(
            Notification.recipient_id == recipient_id,
            Notification.is_read == False,  # noqa: E712
        )
        if organization_id:
            query = query.where(Notification.organization_id == organization_id)

        count_query = select(func.count()).select_from(query.subquery())
        return db.scalar(count_query) or 0

    def list_notifications(
        self,
        db: Session,
        recipient_id: uuid.UUID,
        *,
        organization_id: uuid.UUID | None = None,
        unread_only: bool = False,
        entity_type: EntityType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Notification]:
        """
        List notifications for a user.

        Args:
            db: Database session
            recipient_id: Person UUID
            organization_id: Optional org filter
            unread_only: Only return unread notifications
            entity_type: Filter by entity type
            limit: Maximum notifications to return
            offset: Offset for pagination

        Returns:
            List of notifications ordered by creation time (newest first)
        """
        query = select(Notification).where(Notification.recipient_id == recipient_id)

        if organization_id:
            query = query.where(Notification.organization_id == organization_id)

        if unread_only:
            query = query.where(Notification.is_read == False)  # noqa: E712

        if entity_type:
            query = query.where(Notification.entity_type == entity_type)

        query = (
            query.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
        )

        return list(db.execute(query).scalars().all())

    def mark_read(
        self,
        db: Session,
        notification_id: uuid.UUID,
    ) -> bool:
        """Mark a notification as read."""
        notification = db.get(Notification, notification_id)
        if not notification:
            return False

        notification.mark_read()
        db.flush()
        return True

    def mark_all_read(
        self,
        db: Session,
        recipient_id: uuid.UUID,
        organization_id: uuid.UUID | None = None,
    ) -> int:
        """
        Mark all notifications as read for a user.

        Returns:
            Number of notifications marked as read
        """
        query = (
            update(Notification)
            .where(
                Notification.recipient_id == recipient_id,
                Notification.is_read == False,  # noqa: E712
            )
            .values(is_read=True, read_at=datetime.utcnow())
        )

        if organization_id:
            query = query.where(Notification.organization_id == organization_id)

        result = db.execute(query)
        db.flush()
        return result.rowcount

    def delete_old_notifications(
        self,
        db: Session,
        days_to_keep: int = 90,
    ) -> int:
        """
        Delete notifications older than specified days.

        Args:
            db: Database session
            days_to_keep: Keep notifications from last N days

        Returns:
            Number of deleted notifications
        """
        cutoff = datetime.utcnow() - timedelta(days=days_to_keep)

        # Only delete read notifications older than cutoff
        result = db.execute(
            delete(Notification).where(
                Notification.created_at < cutoff,
                Notification.is_read == True,  # noqa: E712
            )
        )

        db.flush()
        count = result.rowcount
        logger.info("Deleted %d old notifications", count)

        return count


# Singleton instance
notification_service = NotificationService()
