"""
Job Opening Model - Recruit Schema.

Defines job vacancies/positions to fill.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.people.hr.department import Department
    from app.models.people.hr.designation import Designation
    from app.models.people.recruit.job_applicant import JobApplicant


class JobOpeningStatus(str, enum.Enum):
    """Job opening status."""

    DRAFT = "DRAFT"
    OPEN = "OPEN"
    ON_HOLD = "ON_HOLD"
    CLOSED = "CLOSED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


class JobOpening(Base, AuditMixin, ERPNextSyncMixin):
    """
    Job Opening - vacancy to be filled.

    Tracks job requirements, posting dates, and applicant count.
    """

    __tablename__ = "job_opening"
    __table_args__ = (
        UniqueConstraint("organization_id", "job_code", name="uq_job_opening_org_code"),
        Index("idx_job_opening_status", "organization_id", "status"),
        Index("idx_job_opening_dept", "organization_id", "department_id"),
        {"schema": "recruit"},
    )

    job_opening_id: Mapped[uuid.UUID] = mapped_column(
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

    # Identification
    job_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    job_title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Position details
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
    )
    designation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.designation.designation_id"),
        nullable=True,
    )
    reports_to_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Hiring manager",
    )

    # Headcount
    number_of_positions: Mapped[int] = mapped_column(
        Integer,
        default=1,
    )
    positions_filled: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    # Dates
    posted_on: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    closes_on: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    # Employment type
    employment_type: Mapped[str] = mapped_column(
        String(30),
        default="FULL_TIME",
        comment="FULL_TIME, PART_TIME, CONTRACT, INTERNSHIP",
    )
    location: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    is_remote: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    # Compensation
    min_salary: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    max_salary: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )

    # Requirements
    min_experience_years: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    required_skills: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Comma-separated list of required skills",
    )
    preferred_skills: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    education_requirements: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Status
    status: Mapped[JobOpeningStatus] = mapped_column(
        Enum(JobOpeningStatus, name="job_opening_status"),
        default=JobOpeningStatus.DRAFT,
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
    department: Mapped[Optional["Department"]] = relationship("Department")
    designation: Mapped[Optional["Designation"]] = relationship("Designation")
    applicants: Mapped[list["JobApplicant"]] = relationship(
        "JobApplicant",
        back_populates="job_opening",
    )

    @property
    def positions_remaining(self) -> int:
        """Calculate remaining positions."""
        return self.number_of_positions - self.positions_filled

    def __repr__(self) -> str:
        return f"<JobOpening {self.job_code}: {self.job_title}>"
