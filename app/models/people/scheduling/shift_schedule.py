"""
Shift Schedule Model - Scheduling Schema.

Generated monthly schedules for employees showing their assigned shifts per day.
"""

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Date,
    DateTime,
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
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.finance.core_org.organization import Organization
    from app.models.people.attendance.shift_type import ShiftType
    from app.models.people.hr.department import Department
    from app.models.people.hr.employee import Employee


class ScheduleStatus(str, enum.Enum):
    """Schedule lifecycle status."""

    DRAFT = "DRAFT"  # Generated but not yet published
    PUBLISHED = "PUBLISHED"  # Published and visible to employees
    COMPLETED = "COMPLETED"  # Past schedule, marked complete


class ShiftSchedule(Base, AuditMixin):
    """
    Shift Schedule - individual shift entry per employee per day.

    Generated monthly from shift pattern assignments, represents the
    actual scheduled shift for an employee on a specific date.
    """

    __tablename__ = "shift_schedule"
    __table_args__ = (
        # Prevent duplicate schedules for same employee on same date
        UniqueConstraint(
            "organization_id",
            "employee_id",
            "shift_date",
            name="uq_shift_schedule_emp_date",
        ),
        Index(
            "idx_shift_schedule_org_month",
            "organization_id",
            "schedule_month",
        ),
        Index(
            "idx_shift_schedule_dept_date",
            "department_id",
            "shift_date",
        ),
        Index(
            "idx_shift_schedule_employee_date",
            "employee_id",
            "shift_date",
        ),
        Index(
            "idx_shift_schedule_employee",
            "employee_id",
        ),
        {"schema": "scheduling"},
    )

    shift_schedule_id: Mapped[uuid.UUID] = mapped_column(
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

    # Employee and department
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=False,
    )

    # Schedule details
    shift_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="The date this schedule entry is for",
    )
    shift_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attendance.shift_type.shift_type_id"),
        nullable=False,
        comment="The assigned shift type for this date",
    )

    # Grouping field for monthly views
    schedule_month: Mapped[str] = mapped_column(
        String(7),
        nullable=False,
        comment="Month grouping in YYYY-MM format",
    )

    # Status
    status: Mapped[ScheduleStatus] = mapped_column(
        Enum(ScheduleStatus, name="schedule_status", schema="scheduling"),
        nullable=False,
        default=ScheduleStatus.DRAFT,
    )

    # Audit trail
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Manager who generated/modified this schedule",
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the schedule was published",
    )
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )

    # Notes
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Optional notes about this schedule entry",
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
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )
    department: Mapped["Department"] = relationship(
        "Department",
        foreign_keys=[department_id],
    )
    shift_type: Mapped["ShiftType"] = relationship(
        "ShiftType",
        foreign_keys=[shift_type_id],
    )
    created_by: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[created_by_id],
    )
    published_by: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[published_by_id],
    )

    def __repr__(self) -> str:
        return (
            f"<ShiftSchedule {self.employee_id} on {self.shift_date}: "
            f"{self.status.value}>"
        )
