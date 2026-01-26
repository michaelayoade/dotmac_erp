"""
General Notification Model.

App-wide notification system for all modules (tickets, expenses, leave, etc.).
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class EntityType(str, enum.Enum):
    """Types of entities that can generate notifications."""

    TICKET = "TICKET"
    EXPENSE = "EXPENSE"
    LEAVE = "LEAVE"
    ATTENDANCE = "ATTENDANCE"
    PAYROLL = "PAYROLL"
    EMPLOYEE = "EMPLOYEE"
    APPROVAL = "APPROVAL"
    SYSTEM = "SYSTEM"
    # Finance module entity types
    FISCAL_PERIOD = "FISCAL_PERIOD"
    TAX_PERIOD = "TAX_PERIOD"
    BANK_RECONCILIATION = "BANK_RECONCILIATION"
    INVOICE = "INVOICE"
    SUBLEDGER = "SUBLEDGER"


class NotificationType(str, enum.Enum):
    """Types of notification events."""

    # Assignment
    ASSIGNED = "ASSIGNED"
    REASSIGNED = "REASSIGNED"

    # Status changes
    STATUS_CHANGE = "STATUS_CHANGE"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUBMITTED = "SUBMITTED"

    # Communication
    COMMENT = "COMMENT"
    REPLY = "REPLY"
    MENTION = "MENTION"

    # Deadlines
    DUE_SOON = "DUE_SOON"
    OVERDUE = "OVERDUE"

    # Completion
    RESOLVED = "RESOLVED"
    COMPLETED = "COMPLETED"

    # System
    REMINDER = "REMINDER"
    ALERT = "ALERT"
    INFO = "INFO"


class NotificationChannel(str, enum.Enum):
    """Delivery channels for notifications."""

    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    BOTH = "BOTH"


class Notification(Base):
    """
    General notification for any app event.

    Supports in-app notifications (displayed in UI) and email notifications.
    Can be linked to any entity type (tickets, expenses, leave requests, etc.).
    """

    __tablename__ = "notification"
    __table_args__ = (
        Index("ix_notification_recipient_unread", "recipient_id", "is_read"),
        Index("ix_notification_entity", "entity_type", "entity_id"),
        {"schema": "public"},
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Organization scope
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )

    # Target user
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=False,
        index=True,
    )

    # Entity reference (polymorphic)
    entity_type: Mapped[EntityType] = mapped_column(
        Enum(EntityType),
        nullable=False,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Notification details
    notification_type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType),
        nullable=False,
    )

    channel: Mapped[NotificationChannel] = mapped_column(
        Enum(NotificationChannel),
        nullable=False,
        default=NotificationChannel.IN_APP,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Action URL (relative path)
    action_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Status
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Email status
    email_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Actor who triggered the notification (optional)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )

    # Relationships
    recipient = relationship(
        "Person",
        primaryjoin="Notification.recipient_id == Person.id",
        foreign_keys="Notification.recipient_id",
        lazy="joined",
        viewonly=True,
    )
    actor = relationship(
        "Person",
        primaryjoin="Notification.actor_id == Person.id",
        foreign_keys="Notification.actor_id",
        lazy="joined",
        viewonly=True,
    )

    def mark_read(self) -> None:
        """Mark notification as read."""
        self.is_read = True
        self.read_at = datetime.utcnow()

    def __repr__(self) -> str:
        return f"<Notification {self.notification_id} {self.entity_type.value}:{self.notification_type.value}>"
