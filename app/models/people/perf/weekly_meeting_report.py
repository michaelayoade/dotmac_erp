"""Weekly meeting report models."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, VersionMixin

if TYPE_CHECKING:
    from app.models.people.hr.department import Department
    from app.models.people.hr.employee import Employee
    from app.models.person import Person


class WeeklyMeetingReportStatus(str, enum.Enum):
    """Lifecycle status for a weekly meeting report."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"


class MeetingAttendanceStatus(str, enum.Enum):
    """Attendance state recorded for one meeting participant."""

    INVITED = "INVITED"
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    EXCUSED = "EXCUSED"


class MeetingParticipantSource(str, enum.Enum):
    """How a participant was added to the report."""

    SUGGESTED = "SUGGESTED"
    EMPLOYEE = "EMPLOYEE"
    EXTERNAL = "EXTERNAL"


class MeetingActionStatus(str, enum.Enum):
    """Progress state for a meeting action item."""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class ReportEmailStatus(str, enum.Enum):
    """Delivery state for the HR submission email."""

    NOT_QUEUED = "NOT_QUEUED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    FAILED = "FAILED"


class WeeklyMeetingReport(Base, AuditMixin, VersionMixin):
    """A division's weekly meeting record."""

    __tablename__ = "weekly_meeting_report"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "department_id",
            "week_ending",
            name="uq_weekly_meeting_report_org_department_week",
        ),
        CheckConstraint(
            "meeting_date <= week_ending",
            name="ck_weekly_meeting_report_date_within_week",
        ),
        Index(
            "idx_weekly_meeting_report_org_status_week",
            "organization_id",
            "status",
            "week_ending",
        ),
        {"schema": "perf"},
    )

    report_id: Mapped[uuid.UUID] = mapped_column(
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
    report_number: Mapped[str] = mapped_column(String(80), nullable=False)
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=False,
    )
    division_name_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    division_head_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    division_head_name_snapshot: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    week_ending: Mapped[date] = mapped_column(Date, nullable=False)
    meeting_date: Mapped[date] = mapped_column(Date, nullable=False)
    meeting_time: Mapped[time] = mapped_column(Time, nullable=False)
    prepared_by_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    prepared_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=False,
    )
    prepared_by_name_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)

    purpose_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    matters_discussed: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_decisions: Mapped[str | None] = mapped_column(Text, nullable=True)
    issues_risks_support: Mapped[str | None] = mapped_column(Text, nullable=True)
    carry_forward: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[WeeklyMeetingReportStatus] = mapped_column(
        SQLEnum(
            WeeklyMeetingReportStatus,
            name="weekly_meeting_report_status",
            native_enum=False,
            length=20,
        ),
        nullable=False,
        default=WeeklyMeetingReportStatus.DRAFT,
        server_default=WeeklyMeetingReportStatus.DRAFT.value,
    )
    hr_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )

    notification_recipient: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    notification_status: Mapped[ReportEmailStatus] = mapped_column(
        SQLEnum(
            ReportEmailStatus,
            name="weekly_meeting_report_email_status",
            native_enum=False,
            length=20,
        ),
        nullable=False,
        default=ReportEmailStatus.NOT_QUEUED,
        server_default=ReportEmailStatus.NOT_QUEUED.value,
    )
    notification_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    notification_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notification_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )

    department: Mapped[Department] = relationship(
        "Department", foreign_keys=[department_id]
    )
    division_head: Mapped[Employee | None] = relationship(
        "Employee", foreign_keys=[division_head_employee_id]
    )
    prepared_by_employee: Mapped[Employee | None] = relationship(
        "Employee", foreign_keys=[prepared_by_employee_id]
    )
    prepared_by_person: Mapped[Person] = relationship(
        "Person", foreign_keys=[prepared_by_person_id]
    )
    submitted_by: Mapped[Person | None] = relationship(
        "Person", foreign_keys=[submitted_by_id]
    )
    participants: Mapped[list[WeeklyMeetingParticipant]] = relationship(
        "WeeklyMeetingParticipant",
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="WeeklyMeetingParticipant.sequence",
    )
    action_items: Mapped[list[WeeklyMeetingActionItem]] = relationship(
        "WeeklyMeetingActionItem",
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="WeeklyMeetingActionItem.sequence",
    )


class WeeklyMeetingParticipant(Base, AuditMixin):
    """A participant and their meeting-specific attendance snapshot."""

    __tablename__ = "weekly_meeting_participant"
    __table_args__ = (
        UniqueConstraint(
            "report_id",
            "employee_id",
            name="uq_weekly_meeting_participant_report_employee",
        ),
        Index(
            "idx_weekly_meeting_participant_org_report",
            "organization_id",
            "report_id",
        ),
        {"schema": "perf"},
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
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.weekly_meeting_report.report_id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hr.employee.employee_id"), nullable=True
    )
    name_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    role_snapshot: Mapped[str | None] = mapped_column(String(160), nullable=True)
    attendance_status: Mapped[MeetingAttendanceStatus] = mapped_column(
        SQLEnum(
            MeetingAttendanceStatus,
            name="meeting_attendance_status",
            native_enum=False,
            length=20,
        ),
        nullable=False,
        default=MeetingAttendanceStatus.INVITED,
        server_default=MeetingAttendanceStatus.INVITED.value,
    )
    source: Mapped[MeetingParticipantSource] = mapped_column(
        SQLEnum(
            MeetingParticipantSource,
            name="meeting_participant_source",
            native_enum=False,
            length=20,
        ),
        nullable=False,
    )
    role_overridden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )

    report: Mapped[WeeklyMeetingReport] = relationship(
        "WeeklyMeetingReport", back_populates="participants"
    )
    employee: Mapped[Employee | None] = relationship(
        "Employee", foreign_keys=[employee_id]
    )


class WeeklyMeetingActionItem(Base, AuditMixin):
    """A follow-up action captured in a weekly meeting report."""

    __tablename__ = "weekly_meeting_action_item"
    __table_args__ = (
        Index(
            "idx_weekly_meeting_action_org_report",
            "organization_id",
            "report_id",
        ),
        {"schema": "perf"},
    )

    action_item_id: Mapped[uuid.UUID] = mapped_column(
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
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.weekly_meeting_report.report_id", ondelete="CASCADE"),
        nullable=False,
    )
    action_text: Mapped[str] = mapped_column(Text, nullable=False)
    owner_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hr.employee.employee_id"), nullable=True
    )
    owner_name_snapshot: Mapped[str | None] = mapped_column(String(160), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[MeetingActionStatus] = mapped_column(
        SQLEnum(
            MeetingActionStatus,
            name="meeting_action_status",
            native_enum=False,
            length=20,
        ),
        nullable=False,
        default=MeetingActionStatus.NOT_STARTED,
        server_default=MeetingActionStatus.NOT_STARTED.value,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )

    report: Mapped[WeeklyMeetingReport] = relationship(
        "WeeklyMeetingReport", back_populates="action_items"
    )
    owner_employee: Mapped[Employee | None] = relationship(
        "Employee", foreign_keys=[owner_employee_id]
    )
