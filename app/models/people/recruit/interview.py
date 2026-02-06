"""
Interview Model - Recruit Schema.

Tracks interview scheduling, rounds, and feedback.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.people.recruit.job_applicant import JobApplicant
    from app.models.people.hr.employee import Employee


class InterviewRound(str, enum.Enum):
    """Interview round/stage."""

    PHONE_SCREENING = "PHONE_SCREENING"
    TECHNICAL_ROUND_1 = "TECHNICAL_ROUND_1"
    TECHNICAL_ROUND_2 = "TECHNICAL_ROUND_2"
    MANAGER_ROUND = "MANAGER_ROUND"
    HR_ROUND = "HR_ROUND"
    FINAL_ROUND = "FINAL_ROUND"
    CULTURE_FIT = "CULTURE_FIT"


class InterviewStatus(str, enum.Enum):
    """Interview status."""

    SCHEDULED = "SCHEDULED"
    RESCHEDULED = "RESCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"


class Interview(Base, AuditMixin, ERPNextSyncMixin):
    """
    Interview - scheduled interview session with an applicant.

    Tracks timing, interviewers, and feedback.
    """

    __tablename__ = "interview"
    __table_args__ = (
        Index("idx_interview_applicant", "applicant_id"),
        Index("idx_interview_status", "organization_id", "status"),
        Index("idx_interview_date", "organization_id", "scheduled_from"),
        {"schema": "recruit"},
    )

    interview_id: Mapped[uuid.UUID] = mapped_column(
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

    # Applicant
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recruit.job_applicant.applicant_id"),
        nullable=False,
    )

    # Interview details
    round: Mapped[InterviewRound] = mapped_column(
        Enum(InterviewRound, name="interview_round"),
        nullable=False,
    )
    interview_type: Mapped[str] = mapped_column(
        String(30),
        default="IN_PERSON",
        comment="IN_PERSON, VIDEO, PHONE",
    )

    # Scheduling
    scheduled_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    scheduled_to: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    actual_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    actual_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Location/Link
    location: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
    )
    meeting_link: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Video call link",
    )

    # Interviewer
    interviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # Status
    status: Mapped[InterviewStatus] = mapped_column(
        Enum(InterviewStatus, name="interview_status"),
        default=InterviewStatus.SCHEDULED,
    )

    # Feedback (filled after interview)
    rating: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="1-5 rating",
    )
    recommendation: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="STRONG_YES, YES, MAYBE, NO, STRONG_NO",
    )
    feedback: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    strengths: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    weaknesses: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    applicant: Mapped["JobApplicant"] = relationship(
        "JobApplicant",
        back_populates="interviews",
    )
    interviewer: Mapped["Employee"] = relationship("Employee")

    def __repr__(self) -> str:
        return f"<Interview {self.round.value} for {self.applicant_id}>"
