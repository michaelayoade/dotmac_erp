"""
Job Offer Model - Recruit Schema.

Tracks job offers extended to candidates.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
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
    from app.models.people.recruit.job_applicant import JobApplicant
    from app.models.people.recruit.job_opening import JobOpening
    from app.models.people.hr.designation import Designation
    from app.models.people.hr.department import Department


class OfferStatus(str, enum.Enum):
    """Job offer status."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    EXTENDED = "EXTENDED"  # Sent to candidate
    ACCEPTED = "ACCEPTED"
    CONVERTED = "CONVERTED"
    DECLINED = "DECLINED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"


class JobOffer(Base, AuditMixin, StatusTrackingMixin, ERPNextSyncMixin):
    """
    Job Offer - offer extended to a candidate.

    Tracks offer terms, status, and conversion to employee.
    """

    __tablename__ = "job_offer"
    __table_args__ = (
        Index("idx_job_offer_applicant", "applicant_id"),
        Index("idx_job_offer_status", "organization_id", "status"),
        {"schema": "recruit"},
    )

    offer_id: Mapped[uuid.UUID] = mapped_column(
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

    # Reference
    offer_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        unique=True,
    )

    # Applicant & Job
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recruit.job_applicant.applicant_id"),
        nullable=False,
    )
    job_opening_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recruit.job_opening.job_opening_id"),
        nullable=False,
    )

    # Position details
    designation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.designation.designation_id"),
        nullable=False,
    )
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
    )

    # Dates
    offer_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    valid_until: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Offer expiry date",
    )
    expected_joining_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # Compensation
    base_salary: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )
    pay_frequency: Mapped[str] = mapped_column(
        String(20),
        default="MONTHLY",
    )

    # Additional compensation
    signing_bonus: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    relocation_allowance: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    other_benefits: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Employment terms
    employment_type: Mapped[str] = mapped_column(
        String(30),
        default="FULL_TIME",
    )
    probation_months: Mapped[int] = mapped_column(
        default=3,
    )
    notice_period_days: Mapped[int] = mapped_column(
        default=30,
    )

    # Status
    status: Mapped[OfferStatus] = mapped_column(
        Enum(OfferStatus, name="offer_status"),
        default=OfferStatus.DRAFT,
    )

    # Response tracking
    extended_on: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment="Date offer was sent to candidate",
    )
    responded_on: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    decline_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Candidate portal access
    candidate_access_token: Mapped[Optional[str]] = mapped_column(
        String(120),
        nullable=True,
        index=True,
        comment="Token for candidate offer portal access",
    )
    candidate_access_expires: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Expiration time for candidate portal access token",
    )

    # Conversion tracking
    converted_to_employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Employee created from this offer",
    )

    # Notes
    terms_and_conditions: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    notes: Mapped[Optional[str]] = mapped_column(
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
        back_populates="offers",
    )
    job_opening: Mapped["JobOpening"] = relationship("JobOpening")
    designation: Mapped["Designation"] = relationship("Designation")
    department: Mapped[Optional["Department"]] = relationship("Department")

    def __repr__(self) -> str:
        return f"<JobOffer {self.offer_number}: {self.status.value}>"
