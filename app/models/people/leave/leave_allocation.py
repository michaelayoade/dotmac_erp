"""
Leave Allocation Model - Leave Schema.

Tracks leave balance allocation per employee per leave type.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Numeric,
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
    from app.models.people.leave.leave_type import LeaveType


class LeaveAllocation(Base, AuditMixin, ERPNextSyncMixin):
    """
    Leave Allocation - employee's leave balance for a period.

    Tracks allocated, used, and remaining leave days.
    """

    __tablename__ = "leave_allocation"
    __table_args__ = (
        UniqueConstraint(
            "employee_id",
            "leave_type_id",
            "from_date",
            name="uq_leave_allocation_emp_type_period",
        ),
        Index("idx_leave_allocation_employee", "employee_id", "from_date"),
        Index("idx_leave_allocation_type", "leave_type_id", "from_date"),
        {"schema": "leave"},
    )

    allocation_id: Mapped[uuid.UUID] = mapped_column(
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
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leave.leave_type.leave_type_id"),
        nullable=False,
    )

    # Period
    from_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    to_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # Allocation details
    new_leaves_allocated: Mapped[Decimal] = mapped_column(
        Numeric(5, 1),
        nullable=False,
        default=Decimal("0"),
        comment="Fresh allocation for this period",
    )
    carry_forward_leaves: Mapped[Decimal] = mapped_column(
        Numeric(5, 1),
        default=Decimal("0"),
        comment="Carried forward from previous period",
    )
    total_leaves_allocated: Mapped[Decimal] = mapped_column(
        Numeric(5, 1),
        nullable=False,
        comment="new_leaves + carry_forward",
    )

    # Usage tracking
    leaves_used: Mapped[Decimal] = mapped_column(
        Numeric(5, 1),
        default=Decimal("0"),
    )
    leaves_encashed: Mapped[Decimal] = mapped_column(
        Numeric(5, 1),
        default=Decimal("0"),
    )
    leaves_expired: Mapped[Decimal] = mapped_column(
        Numeric(5, 1),
        default=Decimal("0"),
        comment="Expired unused leave",
    )

    # Notes
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
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
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )
    leave_type: Mapped["LeaveType"] = relationship(
        "LeaveType",
        back_populates="allocations",
    )

    @property
    def leaves_remaining(self) -> Decimal:
        """Calculate remaining leave balance."""
        return (
            self.total_leaves_allocated
            - self.leaves_used
            - self.leaves_encashed
            - self.leaves_expired
        )

    def __repr__(self) -> str:
        return f"<LeaveAllocation {self.employee_id} - {self.leave_type_id}: {self.leaves_remaining} remaining>"
