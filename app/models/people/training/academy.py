"""Dotmac Academy requirement and learner progress models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.people.hr.designation import Designation
    from app.models.people.hr.employee import Employee
    from app.models.people.hr.employee_extended import EmployeeCertification
    from app.models.person import Person


class AcademyProgressStatus(str, enum.Enum):
    """Supported Academy learner progress states."""

    ASSIGNED = "assigned"
    STARTED = "started"
    IN_PROGRESS = "in_progress"
    ASSESSMENT_TAKEN = "assessment_taken"
    PASSED = "passed"
    FAILED = "failed"
    COMPLETED = "completed"
    CERTIFICATE_ISSUED = "certificate_issued"


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [item.value for item in enum_cls]


class AcademyLearningRequirement(Base):
    """Academy course or assessment required for an HR designation."""

    __tablename__ = "academy_learning_requirement"
    __table_args__ = (
        Index(
            "idx_academy_req_org_designation",
            "organization_id",
            "designation_id",
        ),
        Index("idx_academy_req_course", "organization_id", "academy_course_id"),
        Index("idx_academy_req_active", "organization_id", "is_active"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
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
    designation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.designation.designation_id"),
        nullable=False,
    )
    academy_course_id: Mapped[str] = mapped_column(String(120), nullable=False)
    academy_course_title: Mapped[str] = mapped_column(String(255), nullable=False)
    academy_assessment_id: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )
    academy_assessment_title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    designation: Mapped[Designation] = relationship("Designation")
    creator: Mapped[Person | None] = relationship("Person")
    progress_records: Mapped[list[AcademyLearningProgress]] = relationship(
        "AcademyLearningProgress",
        back_populates="requirement",
    )


class AcademyLearningProgress(Base):
    """Synced Academy progress for one employee and course/assessment pair."""

    __tablename__ = "academy_learning_progress"
    __table_args__ = (
        Index("idx_academy_progress_employee", "organization_id", "employee_id"),
        Index("idx_academy_progress_course", "organization_id", "academy_course_id"),
        Index("idx_academy_progress_status", "organization_id", "status"),
        Index("idx_academy_progress_requirement", "organization_id", "requirement_id"),
        Index("idx_academy_progress_synced", "organization_id", "last_synced_at"),
        {"schema": "training"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
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
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training.academy_learning_requirement.id"),
        nullable=True,
    )
    academy_course_id: Mapped[str] = mapped_column(String(120), nullable=False)
    academy_course_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    academy_assessment_id: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )
    academy_assessment_title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    status: Mapped[AcademyProgressStatus] = mapped_column(
        Enum(
            AcademyProgressStatus,
            name="academy_progress_status",
            schema="training",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=AcademyProgressStatus.IN_PROGRESS,
        server_default=AcademyProgressStatus.IN_PROGRESS.value,
    )
    progress_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    certificate_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    certification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee_certification.certification_id"),
        nullable=True,
    )
    raw_payload: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    employee: Mapped[Employee] = relationship("Employee")
    requirement: Mapped[AcademyLearningRequirement | None] = relationship(
        "AcademyLearningRequirement",
        back_populates="progress_records",
    )
    certification: Mapped[EmployeeCertification | None] = relationship(
        "EmployeeCertification",
    )
