"""
Shift Type Model - Attendance Schema.

Defines work shift schedules.
"""

import uuid
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin


class ShiftType(Base, AuditMixin, ERPNextSyncMixin):
    """
    Shift Type - defines work schedule parameters.

    Examples: Day Shift (9-5), Night Shift (10pm-6am), Flexible, etc.
    """

    __tablename__ = "shift_type"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "shift_code", name="uq_shift_type_org_code"
        ),
        Index("idx_shift_type_active", "organization_id", "is_active"),
        {"schema": "attendance"},
    )

    shift_type_id: Mapped[uuid.UUID] = mapped_column(
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
    shift_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    shift_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Timings
    start_time: Mapped[time] = mapped_column(
        Time,
        nullable=False,
    )
    end_time: Mapped[time] = mapped_column(
        Time,
        nullable=False,
    )
    working_hours: Mapped[Decimal] = mapped_column(
        Numeric(4, 2),
        nullable=False,
        comment="Total working hours per day",
    )

    # Grace periods
    late_entry_grace_period: Mapped[int] = mapped_column(
        default=0,
        comment="Minutes allowed for late check-in",
    )
    early_exit_grace_period: Mapped[int] = mapped_column(
        default=0,
        comment="Minutes allowed for early checkout",
    )

    # Half-day settings
    enable_half_day: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )
    half_day_threshold_hours: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2),
        nullable=True,
        comment="Hours after which half-day is marked",
    )

    # Overtime settings
    enable_overtime: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    overtime_threshold_hours: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2),
        nullable=True,
        comment="Hours after which overtime starts",
    )

    # Break settings
    break_duration_minutes: Mapped[int] = mapped_column(
        default=60,
        comment="Total break time in minutes",
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
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

    def __repr__(self) -> str:
        return f"<ShiftType {self.shift_code}: {self.start_time}-{self.end_time}>"
