"""
Attendance Request Model - Attendance Schema.

Tracks attendance correction/regularization requests.
"""

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin, StatusTrackingMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee


class AttendanceRequestStatus(str, enum.Enum):
    """Attendance request workflow status."""

    DRAFT = "DRAFT"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AttendanceRequest(Base, AuditMixin, StatusTrackingMixin, ERPNextSyncMixin):
    """
    Attendance Request - employee attendance correction request.
    """

    __tablename__ = "attendance_request"
    __table_args__ = (
        Index("idx_attendance_request_org", "organization_id"),
        Index("idx_attendance_request_employee", "employee_id", "from_date"),
        Index("idx_attendance_request_status", "organization_id", "status"),
        Index(
            "idx_attendance_request_dates", "organization_id", "from_date", "to_date"
        ),
        {"schema": "attendance"},
    )

    attendance_request_id: Mapped[uuid.UUID] = mapped_column(
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

    from_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    to_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    half_day: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    half_day_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Which date is half-day (if half_day=True)",
    )

    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    explanation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    status: Mapped[AttendanceRequestStatus] = mapped_column(
        Enum(AttendanceRequestStatus, name="attendance_request_status"),
        default=AttendanceRequestStatus.DRAFT,
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )

    def __repr__(self) -> str:
        return (
            f"<AttendanceRequest {self.employee_id} "
            f"{self.from_date} to {self.to_date}: {self.status.value}>"
        )
