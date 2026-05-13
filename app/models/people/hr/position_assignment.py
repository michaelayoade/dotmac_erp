"""
Position Assignment Model - HR Schema.

Tracks employee occupancy of organizational positions.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.finance.core_org.organization import Organization
    from app.models.people.hr.employee import Employee
    from app.models.people.hr.position import Position


class PositionAssignmentType(str, enum.Enum):
    """How an employee occupies a position."""

    PRIMARY = "PRIMARY"
    ACTING = "ACTING"
    INTERIM = "INTERIM"


class PositionAssignment(Base, AuditMixin):
    """
    Employee assignment to a position.

    Historical rows are retained by closing the assignment with end_date.
    """

    __tablename__ = "position_assignment"
    __table_args__ = (
        Index(
            "idx_hr_position_assignment_employee_active",
            "organization_id",
            "employee_id",
            "end_date",
        ),
        Index(
            "idx_hr_position_assignment_position_active",
            "organization_id",
            "position_id",
            "end_date",
        ),
        Index(
            "uq_hr_position_assignment_active_primary_employee",
            "organization_id",
            "employee_id",
            unique=True,
            postgresql_where=text("assignment_type = 'PRIMARY' AND end_date IS NULL"),
            sqlite_where=text("assignment_type = 'PRIMARY' AND end_date IS NULL"),
        ),
        Index(
            "uq_hr_position_assignment_active_primary_position",
            "organization_id",
            "position_id",
            unique=True,
            postgresql_where=text("assignment_type = 'PRIMARY' AND end_date IS NULL"),
            sqlite_where=text("assignment_type = 'PRIMARY' AND end_date IS NULL"),
        ),
        {"schema": "hr"},
    )

    position_assignment_id: Mapped[uuid.UUID] = mapped_column(
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
    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.position.position_id"),
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
    assignment_type: Mapped[PositionAssignmentType] = mapped_column(
        Enum(
            PositionAssignmentType,
            name="position_assignment_type",
            schema="hr",
        ),
        nullable=False,
        default=PositionAssignmentType.PRIMARY,
        server_default=PositionAssignmentType.PRIMARY.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    organization: Mapped[Organization] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    employee: Mapped[Employee] = relationship(
        "Employee",
        foreign_keys=[employee_id],
        back_populates="position_assignments",
    )
    position: Mapped[Position] = relationship(
        "Position",
        foreign_keys=[position_id],
        back_populates="assignments",
    )
