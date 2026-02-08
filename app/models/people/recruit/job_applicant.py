"""
Job Applicant Model - Recruit Schema.

Tracks candidates applying for job openings.
"""

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
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
from app.models.people.base import AuditMixin, ERPNextSyncMixin, StatusTrackingMixin

if TYPE_CHECKING:
    from app.models.people.recruit.interview import Interview
    from app.models.people.recruit.job_offer import JobOffer
    from app.models.people.recruit.job_opening import JobOpening


class ApplicantStatus(str, enum.Enum):
    """Applicant pipeline status."""

    NEW = "NEW"
    SCREENING = "SCREENING"
    SHORTLISTED = "SHORTLISTED"
    INTERVIEW_SCHEDULED = "INTERVIEW_SCHEDULED"
    INTERVIEW_COMPLETED = "INTERVIEW_COMPLETED"
    SELECTED = "SELECTED"
    OFFER_EXTENDED = "OFFER_EXTENDED"
    OFFER_ACCEPTED = "OFFER_ACCEPTED"
    OFFER_DECLINED = "OFFER_DECLINED"
    HIRED = "HIRED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class JobApplicant(Base, AuditMixin, StatusTrackingMixin, ERPNextSyncMixin):
    """
    Job Applicant - candidate for a job opening.

    Tracks applicant details, source, and pipeline progress.
    """

    __tablename__ = "job_applicant"
    __table_args__ = (
        Index("idx_job_applicant_status", "organization_id", "status"),
        Index("idx_job_applicant_job", "job_opening_id", "status"),
        Index("idx_job_applicant_email", "organization_id", "email"),
        {"schema": "recruit"},
    )

    applicant_id: Mapped[uuid.UUID] = mapped_column(
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

    # Application reference
    application_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        unique=True,
    )

    # Job opening
    job_opening_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recruit.job_opening.job_opening_id"),
        nullable=False,
    )

    # Personal details
    first_name: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )
    last_name: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    phone: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )
    date_of_birth: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    gender: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    # Address
    city: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
    )
    country_code: Mapped[str | None] = mapped_column(
        String(2),
        nullable=True,
    )

    # Professional details
    current_employer: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    current_job_title: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    years_of_experience: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    highest_qualification: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    skills: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Comma-separated list of skills",
    )

    # Application details
    applied_on: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        server_default=func.current_date(),
    )
    source: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="WEBSITE, LINKEDIN, REFERRAL, AGENCY, etc.",
    )
    referral_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    cover_letter: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    resume_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # Pipeline status
    status: Mapped[ApplicantStatus] = mapped_column(
        Enum(ApplicantStatus, name="applicant_status"),
        default=ApplicantStatus.NEW,
    )

    # Rating
    overall_rating: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="1-5 overall rating",
    )

    # Notes
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Email verification for status tracking (public careers portal)
    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        comment="Whether applicant email has been verified for status tracking",
    )
    verification_token: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Token for verifying email to check application status",
    )
    verification_token_expires: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Expiration time for verification token",
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
    job_opening: Mapped["JobOpening"] = relationship(
        "JobOpening",
        back_populates="applicants",
    )
    interviews: Mapped[list["Interview"]] = relationship(
        "Interview",
        back_populates="applicant",
    )
    offers: Mapped[list["JobOffer"]] = relationship(
        "JobOffer",
        back_populates="applicant",
    )

    @property
    def full_name(self) -> str:
        """Get applicant's full name."""
        return f"{self.first_name} {self.last_name}"

    def __repr__(self) -> str:
        return f"<JobApplicant {self.application_number}: {self.full_name}>"
