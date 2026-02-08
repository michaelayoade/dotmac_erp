"""
Appraisal Cycle Model - Performance Schema.

Defines organization-wide appraisal periods.
"""

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    Enum,
    ForeignKey,
    Index,
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
    from app.models.people.perf.appraisal import Appraisal


class AppraisalCycleStatus(str, enum.Enum):
    """Appraisal cycle status."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"  # Goal setting / self-assessment phase
    REVIEW = "REVIEW"  # Manager review phase
    CALIBRATION = "CALIBRATION"  # HR calibration phase
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AppraisalCycle(Base, AuditMixin, ERPNextSyncMixin):
    """
    Appraisal Cycle - organization-wide performance review period.

    Tracks cycle dates and phases.
    """

    __tablename__ = "appraisal_cycle"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "cycle_code", name="uq_appraisal_cycle_code"
        ),
        Index("idx_appraisal_cycle_status", "organization_id", "status"),
        Index("idx_appraisal_cycle_dates", "organization_id", "start_date", "end_date"),
        {"schema": "perf"},
    )

    cycle_id: Mapped[uuid.UUID] = mapped_column(
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
    cycle_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    cycle_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Review period (what period is being reviewed)
    review_period_start: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Start of period being reviewed (e.g., Jan 1)",
    )
    review_period_end: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="End of period being reviewed (e.g., Dec 31)",
    )

    # Cycle dates (when the appraisal process runs)
    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="When appraisal process begins",
    )
    end_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="When appraisal process ends",
    )

    # Phase deadlines
    self_assessment_deadline: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    manager_review_deadline: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    calibration_deadline: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # Status
    status: Mapped[AppraisalCycleStatus] = mapped_column(
        Enum(AppraisalCycleStatus, name="appraisal_cycle_status"),
        default=AppraisalCycleStatus.DRAFT,
    )

    # Settings
    include_probation_employees: Mapped[bool] = mapped_column(
        default=False,
    )
    min_tenure_months: Mapped[int] = mapped_column(
        default=3,
        comment="Minimum months employed to participate",
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
    appraisals: Mapped[list["Appraisal"]] = relationship(
        "Appraisal",
        back_populates="cycle",
    )

    def __repr__(self) -> str:
        return f"<AppraisalCycle {self.cycle_code}: {self.cycle_name}>"
