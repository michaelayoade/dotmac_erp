"""
Job Description and Competency Models - HR Schema.

Detailed job descriptions and competency frameworks for positions.
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
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin, SoftDeleteMixin

if TYPE_CHECKING:
    from app.models.finance.core_org.organization import Organization
    from app.models.people.hr.designation import Designation
    from app.models.people.hr.department import Department


class CompetencyCategory(str, enum.Enum):
    """Categories of competencies."""

    CORE = "core"  # Organization-wide values/behaviors
    FUNCTIONAL = "functional"  # Role-specific technical competencies
    LEADERSHIP = "leadership"  # Management and leadership competencies
    BEHAVIORAL = "behavioral"  # Soft skills and interpersonal competencies


class JobDescriptionStatus(str, enum.Enum):
    """Job description lifecycle status."""

    DRAFT = "draft"
    ACTIVE = "active"
    UNDER_REVIEW = "under_review"
    ARCHIVED = "archived"


class Competency(Base, AuditMixin, SoftDeleteMixin):
    """
    Competency definition.

    Competencies are measurable skills, behaviors, or knowledge areas
    that can be required for job descriptions and assessed during performance reviews.
    """

    __tablename__ = "competency"
    __table_args__ = {"schema": "hr"}

    competency_id: Mapped[uuid.UUID] = mapped_column(
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

    # Competency identification
    competency_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )
    competency_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Classification
    category: Mapped[CompetencyCategory] = mapped_column(
        Enum(
            CompetencyCategory,
            name="competency_category",
            schema="hr",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=CompetencyCategory.FUNCTIONAL,
    )

    # Proficiency level definitions (JSON or separate table could be used for more detail)
    # Using simple 1-5 scale with descriptions
    level_1_description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )  # Awareness
    level_2_description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )  # Basic
    level_3_description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )  # Intermediate
    level_4_description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )  # Advanced
    level_5_description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )  # Expert

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
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
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    job_description_competencies: Mapped[list["JobDescriptionCompetency"]] = (
        relationship(
            "JobDescriptionCompetency",
            back_populates="competency",
            cascade="all, delete-orphan",
        )
    )


class JobDescription(Base, AuditMixin, SoftDeleteMixin, ERPNextSyncMixin):
    """
    Detailed job description for a position.

    Links to Designation (job title) and contains comprehensive role information
    including responsibilities, requirements, and required competencies.
    """

    __tablename__ = "job_description"
    __table_args__ = {"schema": "hr"}

    job_description_id: Mapped[uuid.UUID] = mapped_column(
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

    # Link to designation (job title)
    designation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.designation.designation_id"),
        nullable=False,
        index=True,
    )

    # Optional department scope (same designation may have different JDs per department)
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
        index=True,
    )

    # Job description identification
    jd_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )
    job_title: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    # Version control
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    effective_from: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    effective_to: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    # Status
    status: Mapped[JobDescriptionStatus] = mapped_column(
        Enum(
            JobDescriptionStatus,
            name="job_description_status",
            schema="hr",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=JobDescriptionStatus.DRAFT,
    )

    # Job summary
    summary: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    purpose: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )  # Why this role exists

    # Key responsibilities (stored as text, could be JSON for structured list)
    key_responsibilities: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Qualifications & requirements
    education_requirements: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    experience_requirements: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    min_years_experience: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    max_years_experience: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    # Technical requirements
    technical_skills: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    certifications_required: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    certifications_preferred: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Working conditions
    work_location: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    travel_requirements: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    physical_requirements: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Compensation range (optional, for internal reference)
    salary_min: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2),
        nullable=True,
    )
    salary_max: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2),
        nullable=True,
    )
    salary_currency: Mapped[Optional[str]] = mapped_column(
        String(3),
        nullable=True,
    )

    # Reporting structure
    reports_to: Mapped[Optional[str]] = mapped_column(
        String(150),
        nullable=True,
    )  # Job title this role reports to
    direct_reports: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )  # Description of who reports to this role

    # Additional information
    additional_notes: Mapped[Optional[str]] = mapped_column(
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
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    designation: Mapped["Designation"] = relationship(
        "Designation",
        foreign_keys=[designation_id],
    )
    department: Mapped[Optional["Department"]] = relationship(
        "Department",
        foreign_keys=[department_id],
    )
    competencies: Mapped[list["JobDescriptionCompetency"]] = relationship(
        "JobDescriptionCompetency",
        back_populates="job_description",
        cascade="all, delete-orphan",
    )


class JobDescriptionCompetency(Base, AuditMixin):
    """
    Links competencies to job descriptions with required proficiency levels.

    This many-to-many relationship allows specifying which competencies
    are required for a job and at what proficiency level.
    """

    __tablename__ = "job_description_competency"
    __table_args__ = {"schema": "hr"}

    jd_competency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    job_description_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.job_description.job_description_id"),
        nullable=False,
        index=True,
    )
    competency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.competency.competency_id"),
        nullable=False,
        index=True,
    )

    # Required proficiency level (1-5 scale)
    required_level: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
    )

    # Weight/importance for this role (for weighted scoring)
    weight: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        default=Decimal("1.00"),
    )

    # Is this competency mandatory or preferred?
    is_mandatory: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Notes specific to this competency for this job
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    job_description: Mapped["JobDescription"] = relationship(
        "JobDescription",
        back_populates="competencies",
    )
    competency: Mapped["Competency"] = relationship(
        "Competency",
        back_populates="job_description_competencies",
    )
