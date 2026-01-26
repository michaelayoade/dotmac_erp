"""
KPI (Key Performance Indicator) Model - Performance Schema.

Tracks specific measurable indicators for employees.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Date,
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
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.perf.kra import KRA


class KPIStatus(str, enum.Enum):
    """KPI tracking status."""
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    ON_TRACK = "ON_TRACK"
    AT_RISK = "AT_RISK"
    ACHIEVED = "ACHIEVED"
    COMPLETED = "COMPLETED"
    MISSED = "MISSED"
    DEFERRED = "DEFERRED"
    CANCELLED = "CANCELLED"


class KPI(Base, AuditMixin, ERPNextSyncMixin):
    """
    KPI - Key Performance Indicator.

    Tracks specific measurable goals for an employee.
    """

    __tablename__ = "kpi"
    __table_args__ = (
        Index("idx_kpi_employee", "employee_id"),
        Index("idx_kpi_kra", "kra_id"),
        Index("idx_kpi_status", "organization_id", "status"),
        Index("idx_kpi_period", "organization_id", "period_start", "period_end"),
        {"schema": "perf"},
    )

    kpi_id: Mapped[uuid.UUID] = mapped_column(
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

    # Employee
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # KRA (optional, can be standalone KPI)
    kra_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.kra.kra_id"),
        nullable=True,
    )

    # KPI details
    kpi_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Period
    period_start: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    period_end: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # Target
    target_value: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    unit_of_measure: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
        comment="%, units, dollars, etc.",
    )
    threshold_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="Minimum acceptable value",
    )
    stretch_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="Exceptional performance value",
    )

    # Actual
    actual_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    achievement_percentage: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="(actual/target) * 100",
    )

    # Weighting
    weightage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0.00"),
        comment="Weight percentage for this KPI",
    )

    # Status
    status: Mapped[KPIStatus] = mapped_column(
        Enum(KPIStatus, name="kpi_status"),
        default=KPIStatus.DRAFT,
    )

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    evidence: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Supporting evidence or documentation",
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
    employee: Mapped["Employee"] = relationship("Employee")
    kra: Mapped[Optional["KRA"]] = relationship("KRA")

    @property
    def is_achieved(self) -> bool:
        """Check if KPI is achieved."""
        if self.actual_value is None or self.target_value is None:
            return False
        return self.actual_value >= self.target_value

    def calculate_achievement(self) -> Optional[Decimal]:
        """Calculate achievement percentage."""
        if self.actual_value is None or self.target_value is None or self.target_value == 0:
            return None
        return (self.actual_value / self.target_value) * 100

    def __repr__(self) -> str:
        return f"<KPI {self.kpi_name} for {self.employee_id}>"
