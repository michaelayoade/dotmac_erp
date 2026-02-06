"""
Shift Pattern Assignment Model - Scheduling Schema.

Links employees to shift patterns for their department.
"""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
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
    from app.models.people.hr.department import Department
    from app.models.people.scheduling.shift_pattern import ShiftPattern


class ShiftPatternAssignment(Base, AuditMixin):
    """
    Shift Pattern Assignment - links employees to shift patterns.

    Allows assigning employees to specific patterns within their department
    with optional rotation offset for staggered schedules.
    """

    __tablename__ = "shift_pattern_assignment"
    __table_args__ = (
        Index(
            "idx_pattern_assignment_org_dept",
            "organization_id",
            "department_id",
        ),
        Index(
            "idx_pattern_assignment_employee",
            "employee_id",
            "effective_from",
        ),
        Index(
            "idx_pattern_assignment_active",
            "organization_id",
            "is_active",
        ),
        {"schema": "scheduling"},
    )

    pattern_assignment_id: Mapped[uuid.UUID] = mapped_column(
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

    # Pattern reference
    shift_pattern_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scheduling.shift_pattern.shift_pattern_id"),
        nullable=False,
    )

    # Rotation offset for staggered schedules
    rotation_week_offset: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Week offset (0 or 1) for staggered rotations. "
        "Offset 0: Week 1=Day, Week 2=Night. "
        "Offset 1: Week 1=Night, Week 2=Day.",
    )

    # Effective dates
    effective_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Start date for this assignment",
    )
    effective_to: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment="End date (null = ongoing)",
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
    shift_pattern: Mapped["ShiftPattern"] = relationship(
        "ShiftPattern",
        foreign_keys=[shift_pattern_id],
        back_populates="assignments",
    )

    def __repr__(self) -> str:
        return (
            f"<ShiftPatternAssignment {self.employee_id} -> "
            f"{self.shift_pattern_id} from {self.effective_from}>"
        )
