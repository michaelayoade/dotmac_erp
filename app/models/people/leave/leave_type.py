"""
Leave Type Model - Leave Schema.

Defines types of leave available in the organization.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
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
    from app.models.people.leave.leave_allocation import LeaveAllocation


class LeaveTypePolicy(str, enum.Enum):
    """How leave is allocated."""

    ANNUAL = "ANNUAL"  # Allocated once per year
    MONTHLY = "MONTHLY"  # Accrued monthly
    EARNED = "EARNED"  # Earned based on attendance
    UNLIMITED = "UNLIMITED"  # No limit (e.g., WFH)


class LeaveType(Base, AuditMixin, ERPNextSyncMixin):
    """
    Leave Type - defines categories of leave.

    Examples: Annual Leave, Sick Leave, Maternity Leave, etc.
    """

    __tablename__ = "leave_type"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "leave_type_code", name="uq_leave_type_org_code"
        ),
        Index("idx_leave_type_active", "organization_id", "is_active"),
        {"schema": "leave"},
    )

    leave_type_id: Mapped[uuid.UUID] = mapped_column(
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
    leave_type_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="Unique code, e.g. ANNUAL, SICK, MAT",
    )
    leave_type_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Allocation policy
    allocation_policy: Mapped[LeaveTypePolicy] = mapped_column(
        Enum(LeaveTypePolicy, name="leave_type_policy"),
        default=LeaveTypePolicy.ANNUAL,
    )
    max_days_per_year: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 1),
        nullable=True,
        comment="Maximum days allowed per year (null = unlimited)",
    )
    max_continuous_days: Mapped[Optional[int]] = mapped_column(
        nullable=True,
        comment="Max consecutive days allowed",
    )

    # Carry forward settings
    allow_carry_forward: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    max_carry_forward_days: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 1),
        nullable=True,
    )
    carry_forward_expiry_months: Mapped[Optional[int]] = mapped_column(
        nullable=True,
        comment="Months after which carried forward leave expires",
    )

    # Encashment settings
    allow_encashment: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Can unused leave be converted to cash",
    )
    encashment_threshold_days: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 1),
        nullable=True,
        comment="Min days required before encashment allowed",
    )

    # Payroll impact
    is_lwp: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Leave Without Pay - affects salary",
    )
    is_compensatory: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Compensatory off (earned by working holidays)",
    )
    include_holidays: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Count holidays within leave period",
    )

    # Restrictions
    applicable_after_days: Mapped[int] = mapped_column(
        default=0,
        comment="Days of service required before eligible",
    )
    is_optional: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Optional leave (e.g., religious holidays)",
    )
    max_optional_leaves: Mapped[Optional[int]] = mapped_column(
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
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    allocations: Mapped[list["LeaveAllocation"]] = relationship(
        "LeaveAllocation",
        back_populates="leave_type",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<LeaveType {self.leave_type_code}: {self.leave_type_name}>"
