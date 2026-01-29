"""
Shift Swap Request Model - Scheduling Schema.

Handles employee requests to swap shifts with each other.
"""
import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.finance.core_org.organization import Organization
    from app.models.people.hr.employee import Employee
    from app.models.people.scheduling.shift_schedule import ShiftSchedule


class SwapRequestStatus(str, enum.Enum):
    """Swap request workflow status."""

    PENDING = "PENDING"  # Awaiting target employee acceptance
    TARGET_ACCEPTED = "TARGET_ACCEPTED"  # Target accepted, awaiting manager approval
    APPROVED = "APPROVED"  # Manager approved, swap executed
    REJECTED = "REJECTED"  # Manager rejected
    CANCELLED = "CANCELLED"  # Requester cancelled


class ShiftSwapRequest(Base, AuditMixin):
    """
    Shift Swap Request - employee-initiated shift swap requests.

    Workflow:
    1. Requester creates swap request (PENDING)
    2. Target employee accepts (TARGET_ACCEPTED)
    3. Manager approves/rejects (APPROVED/REJECTED)
    4. On approval, shifts are swapped between the two schedules
    """

    __tablename__ = "shift_swap_request"
    __table_args__ = (
        Index(
            "idx_swap_request_org_status",
            "organization_id",
            "status",
        ),
        Index(
            "idx_swap_request_requester",
            "requester_id",
            "status",
        ),
        Index(
            "idx_swap_request_target",
            "target_employee_id",
            "status",
        ),
        {"schema": "scheduling"},
    )

    swap_request_id: Mapped[uuid.UUID] = mapped_column(
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

    # The two schedule entries being swapped
    requester_schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scheduling.shift_schedule.shift_schedule_id"),
        nullable=False,
        comment="Requester's original schedule entry",
    )
    target_schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scheduling.shift_schedule.shift_schedule_id"),
        nullable=False,
        comment="Target employee's schedule entry to swap with",
    )

    # Participants
    requester_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
        comment="Employee requesting the swap",
    )
    target_employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
        comment="Employee to swap with",
    )

    # Status
    status: Mapped[SwapRequestStatus] = mapped_column(
        Enum(SwapRequestStatus, name="swap_request_status", schema="scheduling"),
        nullable=False,
        default=SwapRequestStatus.PENDING,
    )

    # Request details
    reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Reason for the swap request",
    )

    # Target acceptance
    target_accepted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When target employee accepted the swap",
    )

    # Manager review
    reviewed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Manager who approved/rejected",
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the swap was reviewed",
    )
    review_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Manager's notes on approval/rejection",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    requester_schedule: Mapped["ShiftSchedule"] = relationship(
        "ShiftSchedule",
        foreign_keys=[requester_schedule_id],
    )
    target_schedule: Mapped["ShiftSchedule"] = relationship(
        "ShiftSchedule",
        foreign_keys=[target_schedule_id],
    )
    requester: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[requester_id],
    )
    target_employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[target_employee_id],
    )
    reviewed_by: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[reviewed_by_id],
    )

    def __repr__(self) -> str:
        return (
            f"<ShiftSwapRequest {self.requester_id} <-> {self.target_employee_id}: "
            f"{self.status.value}>"
        )
