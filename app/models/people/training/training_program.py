"""
Training Program Model - Training Schema.

Defines training curricula and programs.
"""
import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
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
    from app.models.people.training.training_event import TrainingEvent


class TrainingProgramStatus(str, enum.Enum):
    """Training program status."""
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class TrainingProgram(Base, AuditMixin, ERPNextSyncMixin):
    """
    Training Program - curriculum or course definition.

    Tracks program details, duration, and costs.
    """

    __tablename__ = "training_program"
    __table_args__ = (
        UniqueConstraint("organization_id", "program_code", name="uq_training_program_code"),
        Index("idx_training_program_status", "organization_id", "status"),
        {"schema": "training"},
    )

    program_id: Mapped[uuid.UUID] = mapped_column(
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
    program_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    program_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Training details
    training_type: Mapped[str] = mapped_column(
        String(30),
        default="INTERNAL",
        comment="INTERNAL, EXTERNAL, ONLINE, CERTIFICATION",
    )
    category: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="TECHNICAL, SOFT_SKILLS, COMPLIANCE, LEADERSHIP, etc.",
    )

    # Duration
    duration_hours: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    duration_days: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    # Department (if department-specific)
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
    )

    # Cost
    cost_per_attendee: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )

    # Content
    objectives: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    prerequisites: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    syllabus: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Provider (for external training)
    provider_name: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
    )
    provider_contact: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
    )

    # Status
    status: Mapped[TrainingProgramStatus] = mapped_column(
        Enum(TrainingProgramStatus, name="training_program_status"),
        default=TrainingProgramStatus.DRAFT,
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
    events: Mapped[list["TrainingEvent"]] = relationship(
        "TrainingEvent",
        back_populates="program",
    )

    def __repr__(self) -> str:
        return f"<TrainingProgram {self.program_code}: {self.program_name}>"
