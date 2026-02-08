"""
Shift Assignment Model - Attendance Schema.

Tracks employee shift assignments over time.
"""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.people.attendance.shift_type import ShiftType
    from app.models.people.hr.employee import Employee


class ShiftAssignment(Base, AuditMixin, ERPNextSyncMixin):
    """
    Shift Assignment - employee shift assignment for a period.
    """

    __tablename__ = "shift_assignment"
    __table_args__ = (
        Index("idx_shift_assignment_org", "organization_id"),
        Index("idx_shift_assignment_employee", "employee_id", "start_date"),
        Index("idx_shift_assignment_shift_type", "shift_type_id"),
        {"schema": "attendance"},
    )

    shift_assignment_id: Mapped[uuid.UUID] = mapped_column(
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
    shift_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attendance.shift_type.shift_type_id"),
        nullable=False,
    )

    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    end_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
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
    shift_type: Mapped["ShiftType"] = relationship(
        "ShiftType",
    )

    def __repr__(self) -> str:
        return (
            f"<ShiftAssignment {self.employee_id} {self.shift_type_id} "
            f"{self.start_date} to {self.end_date}>"
        )
