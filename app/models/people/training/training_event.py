"""
Training Event Model - Training Schema.

Tracks scheduled training sessions and attendees.
"""

import enum
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.training.training_program import TrainingProgram


class TrainingEventStatus(str, enum.Enum):
    """Training event status."""

    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AttendeeStatus(str, enum.Enum):
    """Training attendee status."""

    INVITED = "INVITED"
    CONFIRMED = "CONFIRMED"
    ATTENDED = "ATTENDED"
    ABSENT = "ABSENT"
    CANCELLED = "CANCELLED"


class TrainingEvent(Base, AuditMixin, ERPNextSyncMixin):
    """
    Training Event - scheduled training session.

    Tracks dates, location, trainer, and attendees.
    """

    __tablename__ = "training_event"
    __table_args__ = (
        Index("idx_training_event_program", "program_id"),
        Index("idx_training_event_dates", "organization_id", "start_date", "end_date"),
        Index("idx_training_event_status", "organization_id", "status"),
        {"schema": "training"},
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )

    # Program
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_program.program_id"),
        nullable=False,
    )

    # Event details
    event_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Schedule
    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    end_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    start_time: Mapped[time | None] = mapped_column(
        Time,
        nullable=True,
    )
    end_time: Mapped[time | None] = mapped_column(
        Time,
        nullable=True,
    )

    # Location
    event_type: Mapped[str] = mapped_column(
        String(20),
        default="IN_PERSON",
        comment="IN_PERSON, ONLINE, HYBRID",
    )
    location: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    meeting_link: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # Trainer
    trainer_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    trainer_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    trainer_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="If trainer is internal employee",
    )

    # Capacity
    max_attendees: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Cost tracking
    total_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )

    # Status
    status: Mapped[TrainingEventStatus] = mapped_column(
        Enum(TrainingEventStatus, name="training_event_status"),
        default=TrainingEventStatus.DRAFT,
    )

    # Feedback (post-event)
    average_rating: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2),
        nullable=True,
        comment="Average feedback rating 1.00-5.00",
    )
    feedback_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    program: Mapped["TrainingProgram"] = relationship(
        "TrainingProgram",
        back_populates="events",
    )
    trainer: Mapped[Optional["Employee"]] = relationship("Employee")
    attendees: Mapped[list["TrainingAttendee"]] = relationship(
        "TrainingAttendee",
        back_populates="event",
    )

    @property
    def attendee_count(self) -> int:
        """Get confirmed attendee count."""
        return sum(
            1
            for a in self.attendees
            if a.status in (AttendeeStatus.CONFIRMED, AttendeeStatus.ATTENDED)
        )

    @property
    def spots_remaining(self) -> int | None:
        """Calculate remaining spots."""
        if self.max_attendees is None:
            return None
        return max(0, self.max_attendees - self.attendee_count)

    def __repr__(self) -> str:
        return f"<TrainingEvent {self.event_name} ({self.start_date})>"


class TrainingAttendee(Base, AuditMixin):
    """
    Training Attendee - employee assigned to a training event.

    Tracks attendance and feedback.
    """

    __tablename__ = "training_attendee"
    __table_args__ = (
        Index("idx_training_attendee_event", "event_id"),
        Index("idx_training_attendee_employee", "employee_id"),
        {"schema": "training"},
    )

    attendee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )

    # Event & Employee
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.training_event.event_id"),
        nullable=False,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # Status
    status: Mapped[AttendeeStatus] = mapped_column(
        Enum(AttendeeStatus, name="attendee_status"),
        default=AttendeeStatus.INVITED,
    )

    # Attendance tracking
    invited_on: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    confirmed_on: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    attended_on: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Date marked as attended",
    )

    # Feedback
    rating: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="1-5 rating",
    )
    feedback: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Certification
    certificate_issued: Mapped[bool] = mapped_column(
        default=False,
    )
    certificate_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    # Notes
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    event: Mapped["TrainingEvent"] = relationship(
        "TrainingEvent",
        back_populates="attendees",
    )
    employee: Mapped["Employee"] = relationship("Employee")

    def __repr__(self) -> str:
        return f"<TrainingAttendee {self.employee_id} for {self.event_id}>"
