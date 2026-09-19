"""Tenant-scoped organization calendar models.

ERP owns organizational intent.  Nextcloud receives a synchronized
representation through the platform outbox and never becomes the authority for
these records.  Personal Nextcloud calendars are intentionally not represented
here.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class CalendarBusinessStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"


class ParticipantMembershipStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    REMOVED = "REMOVED"
    CANCELLED = "CANCELLED"


class OrganizationCalendarEvent(Base):
    __tablename__ = "organization_calendar_events"
    __table_args__ = (
        CheckConstraint(
            "(all_day = false AND start_at IS NOT NULL AND end_at IS NOT NULL "
            "AND start_date IS NULL AND end_date_exclusive IS NULL) OR "
            "(all_day = true AND start_date IS NOT NULL "
            "AND end_date_exclusive IS NOT NULL AND start_at IS NULL "
            "AND end_at IS NULL)",
            name="ck_org_calendar_event_time_shape",
        ),
        CheckConstraint(
            "(all_day = false AND end_at > start_at) OR "
            "(all_day = true AND end_date_exclusive > start_date)",
            name="ck_org_calendar_event_positive_duration",
        ),
        CheckConstraint("version >= 1", name="ck_org_calendar_event_version"),
        CheckConstraint(
            "business_status IN ('DRAFT', 'PUBLISHED', 'CANCELLED')",
            name="ck_org_calendar_event_business_status",
        ),
        UniqueConstraint("ical_uid", name="uq_org_calendar_event_ical_uid"),
        Index(
            "idx_org_calendar_event_org_start",
            "organization_id",
            "start_at",
            "start_date",
        ),
        Index(
            "idx_org_calendar_event_org_status",
            "organization_id",
            "business_status",
        ),
        {"schema": "public"},
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
    )
    ical_uid: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    event_details: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(255))
    meeting_url: Mapped[str | None] = mapped_column(String(1000))
    color: Mapped[str] = mapped_column(
        String(7), nullable=False, default="#4F46E5", server_default="#4F46E5"
    )

    all_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date_exclusive: Mapped[date | None] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Africa/Lagos",
        server_default="Africa/Lagos",
    )

    business_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CalendarBusinessStatus.DRAFT.value,
        server_default=CalendarBusinessStatus.DRAFT.value,
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    updated_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )
    cancelled_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    participants: Mapped[list[OrganizationCalendarParticipant]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    reminders: Mapped[list[OrganizationCalendarReminder]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class OrganizationCalendarParticipant(Base):
    __tablename__ = "organization_calendar_participants"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "person_id", name="uq_org_calendar_participant_event_person"
        ),
        Index(
            "idx_org_calendar_participant_org_event",
            "organization_id",
            "event_id",
        ),
        Index(
            "idx_org_calendar_participant_person",
            "organization_id",
            "person_id",
            "membership_status",
        ),
        CheckConstraint(
            "identity_status IN ('VERIFIED', 'MISSING_NEXTCLOUD_ACCOUNT', "
            "'MISSING_ERP_MAPPING', 'EMAIL_MISMATCH', 'DUPLICATE_IDENTITY', "
            "'DISABLED', 'UNRESOLVED')",
            name="ck_org_calendar_participant_identity_status",
        ),
        CheckConstraint(
            "membership_status IN ('ACTIVE', 'REMOVED', 'CANCELLED')",
            name="ck_org_calendar_participant_membership_status",
        ),
        {"schema": "public"},
    )

    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.organization_calendar_events.event_id", ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hr.employee.employee_id"), nullable=False
    )
    participant_name: Mapped[str] = mapped_column(String(160), nullable=False)
    participant_email: Mapped[str] = mapped_column(String(255), nullable=False)
    nextcloud_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    identity_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="VERIFIED", server_default="VERIFIED"
    )
    membership_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ParticipantMembershipStatus.ACTIVE.value,
        server_default=ParticipantMembershipStatus.ACTIVE.value,
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    event: Mapped[OrganizationCalendarEvent] = relationship(
        back_populates="participants"
    )


class OrganizationCalendarReminder(Base):
    __tablename__ = "organization_calendar_reminders"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "offset_minutes", name="uq_org_calendar_reminder_offset"
        ),
        CheckConstraint(
            "offset_minutes >= 0", name="ck_org_calendar_reminder_nonnegative"
        ),
        Index("idx_org_calendar_reminder_org_event", "organization_id", "event_id"),
        {"schema": "public"},
    )

    reminder_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.organization_calendar_events.event_id", ondelete="CASCADE"),
        nullable=False,
    )
    offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    event: Mapped[OrganizationCalendarEvent] = relationship(back_populates="reminders")


class OrganizationCalendarAudit(Base):
    __tablename__ = "organization_calendar_audit"
    __table_args__ = (
        Index("idx_org_calendar_audit_org_event", "organization_id", "event_id"),
        {"schema": "public"},
    )

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.organization_calendar_events.event_id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    old_values: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    new_values: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "CalendarBusinessStatus",
    "OrganizationCalendarAudit",
    "OrganizationCalendarEvent",
    "OrganizationCalendarParticipant",
    "OrganizationCalendarReminder",
    "ParticipantMembershipStatus",
]
