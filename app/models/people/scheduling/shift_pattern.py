"""
Shift Pattern Model - Scheduling Schema.

Defines weekly shift patterns for rotation schedules.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.finance.core_org.organization import Organization
    from app.models.people.attendance.shift_type import ShiftType
    from app.models.people.scheduling.pattern_assignment import ShiftPatternAssignment


class RotationType(str, enum.Enum):
    """Types of shift rotation patterns."""

    DAY_ONLY = "DAY_ONLY"  # Fixed day shifts only
    NIGHT_ONLY = "NIGHT_ONLY"  # Fixed night shifts only
    ROTATING = "ROTATING"  # Rotates between day and night


class ShiftPattern(Base, AuditMixin):
    """
    Shift Pattern - defines weekly shift patterns for rotation schedules.

    Used to configure how employees rotate between shifts (day/night)
    and which days of the week they work.
    """

    __tablename__ = "shift_pattern"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "pattern_code", name="uq_shift_pattern_org_code"
        ),
        Index("idx_shift_pattern_org_active", "organization_id", "is_active"),
        {"schema": "scheduling"},
    )

    shift_pattern_id: Mapped[uuid.UUID] = mapped_column(
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

    # Pattern identification
    pattern_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="Unique pattern code, e.g. DAY-STD, NIGHT-STD, ROT-2WK",
    )
    pattern_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Display name, e.g. 'Standard Day Shift', 'Bi-Weekly Rotation'",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Rotation configuration
    rotation_type: Mapped[RotationType] = mapped_column(
        Enum(RotationType, name="rotation_type", schema="scheduling"),
        nullable=False,
        default=RotationType.DAY_ONLY,
        comment="Whether this pattern is day-only, night-only, or rotating",
    )
    cycle_weeks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment="Rotation cycle in weeks. 1=weekly same shift, 2=bi-weekly rotation",
    )

    # Work days configuration (stored as JSON array)
    work_days: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=["MON", "TUE", "WED", "THU", "FRI"],
        comment='Days of the week this pattern applies: ["MON","TUE","WED","THU","FRI"]',
    )
    day_work_days: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment='For ROTATING patterns: day-shift days, e.g. ["MON","WED","FRI"]',
    )
    night_work_days: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment='For ROTATING patterns: night-shift days, e.g. ["SAT","SUN"]',
    )

    # Shift type references
    day_shift_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attendance.shift_type.shift_type_id"),
        nullable=False,
        comment="Shift type to use for day shifts",
    )
    night_shift_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attendance.shift_type.shift_type_id"),
        nullable=True,
        comment="Shift type for night shifts (required for ROTATING type)",
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

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    day_shift_type: Mapped["ShiftType"] = relationship(
        "ShiftType",
        foreign_keys=[day_shift_type_id],
    )
    night_shift_type: Mapped[Optional["ShiftType"]] = relationship(
        "ShiftType",
        foreign_keys=[night_shift_type_id],
    )
    assignments: Mapped[list["ShiftPatternAssignment"]] = relationship(
        "ShiftPatternAssignment",
        back_populates="shift_pattern",
    )

    def __repr__(self) -> str:
        return f"<ShiftPattern {self.pattern_code}: {self.rotation_type.value}>"
