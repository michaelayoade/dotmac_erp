"""
Leave Application Model - Leave Schema.

Handles leave requests and approval workflow.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
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
from app.models.people.base import AuditMixin, ERPNextSyncMixin, StatusTrackingMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.leave.leave_type import LeaveType


class LeaveApplicationStatus(str, enum.Enum):
    """Leave application workflow status."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class LeaveApplication(Base, AuditMixin, StatusTrackingMixin, ERPNextSyncMixin):
    """
    Leave Application - employee leave request.

    Follows approval workflow: DRAFT -> SUBMITTED -> APPROVED/REJECTED
    """

    __tablename__ = "leave_application"
    __table_args__ = (
        Index("idx_leave_app_employee", "employee_id", "from_date"),
        Index("idx_leave_app_status", "organization_id", "status"),
        Index("idx_leave_app_dates", "organization_id", "from_date", "to_date"),
        {"schema": "leave"},
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
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

    # Application number (auto-generated)
    application_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        unique=True,
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

    # Leave period
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
    half_day_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment="Which date is half-day (if half_day=True)",
    )

    # Calculated fields
    total_leave_days: Mapped[Decimal] = mapped_column(
        Numeric(5, 1),
        nullable=False,
        comment="Number of leave days (excluding holidays/weekends as configured)",
    )

    # Reason and details
    reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    contact_during_leave: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Contact number during leave",
    )
    address_during_leave: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Workflow
    status: Mapped[LeaveApplicationStatus] = mapped_column(
        Enum(LeaveApplicationStatus, name="leave_application_status"),
        default=LeaveApplicationStatus.DRAFT,
    )

    # Approval details
    leave_approver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Employee who should approve",
    )
    approved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Posting reference (for LWP payroll deduction)
    is_posted_to_payroll: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    salary_slip_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.salary_slip.slip_id"),
        nullable=True,
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
    leave_type: Mapped["LeaveType"] = relationship(
        "LeaveType",
    )
    leave_approver: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[leave_approver_id],
    )

    def __repr__(self) -> str:
        return f"<LeaveApplication {self.application_number}: {self.status.value}>"
