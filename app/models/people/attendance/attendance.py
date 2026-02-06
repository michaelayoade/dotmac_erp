"""
Attendance Model - Attendance Schema.

Tracks daily employee attendance records.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
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
    from app.models.people.hr.employee import Employee
    from app.models.people.attendance.shift_type import ShiftType
    from app.models.people.leave.leave_application import LeaveApplication


class AttendanceStatus(str, enum.Enum):
    """Attendance marking status."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    HALF_DAY = "HALF_DAY"
    ON_LEAVE = "ON_LEAVE"
    HOLIDAY = "HOLIDAY"
    WORK_FROM_HOME = "WORK_FROM_HOME"


class Attendance(Base, AuditMixin, ERPNextSyncMixin):
    """
    Attendance - daily attendance record for an employee.

    Tracks check-in/out times, working hours, and attendance status.
    """

    __tablename__ = "attendance"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "attendance_date", name="uq_attendance_emp_date"
        ),
        Index("idx_attendance_date", "organization_id", "attendance_date"),
        Index("idx_attendance_employee", "employee_id", "attendance_date"),
        Index("idx_attendance_status", "organization_id", "status", "attendance_date"),
        {"schema": "attendance"},
    )

    attendance_id: Mapped[uuid.UUID] = mapped_column(
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

    # Links
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    shift_type_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attendance.shift_type.shift_type_id"),
        nullable=True,
    )

    # Date
    attendance_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # Check-in/out times
    check_in: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    check_out: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Calculated fields
    working_hours: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Actual hours worked",
    )
    overtime_hours: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0"),
    )

    # Status
    status: Mapped[AttendanceStatus] = mapped_column(
        Enum(AttendanceStatus, name="attendance_status"),
        nullable=False,
    )

    # Late/early flags
    late_entry: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    late_entry_minutes: Mapped[int] = mapped_column(
        default=0,
    )
    early_exit: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    early_exit_minutes: Mapped[int] = mapped_column(
        default=0,
    )

    # Leave reference (if status is ON_LEAVE)
    leave_application_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leave.leave_application.application_id"),
        nullable=True,
    )

    # Notes
    remarks: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Source
    marked_by: Mapped[str] = mapped_column(
        String(20),
        default="MANUAL",
        comment="How attendance was marked: MANUAL, BIOMETRIC, GEOFENCE, etc.",
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
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )
    shift_type: Mapped[Optional["ShiftType"]] = relationship(
        "ShiftType",
    )
    leave_application: Mapped[Optional["LeaveApplication"]] = relationship(
        "LeaveApplication",
    )

    def __repr__(self) -> str:
        return f"<Attendance {self.employee_id} {self.attendance_date}: {self.status.value}>"
