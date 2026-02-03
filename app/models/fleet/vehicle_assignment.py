"""
Vehicle Assignment Model - Fleet Schema.

Tracks vehicle assignment history to employees and departments.
"""
import uuid
from datetime import date
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Date, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.fleet.base import FleetBaseMixin
from app.models.fleet.enums import AssignmentType
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.fleet.vehicle import Vehicle
    from app.models.people.hr.employee import Employee


class VehicleAssignment(Base, FleetBaseMixin, AuditMixin):
    """
    Vehicle assignment history record.

    Each record represents a period when a vehicle was assigned
    to an employee or department. Used for:
    - Tracking who had the vehicle and when
    - Recording odometer at start/end of assignment
    - Maintaining audit trail for asset management
    """

    __tablename__ = "vehicle_assignment"
    __table_args__ = (
        Index(
            "idx_fleet_assignment_vehicle_dates",
            "vehicle_id",
            "start_date",
            "end_date",
        ),
        Index("idx_fleet_assignment_employee", "employee_id"),
        Index("idx_fleet_assignment_active", "organization_id", "is_active"),
        {"schema": "fleet"},
    )

    # Primary key
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # References
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fleet.vehicle.vehicle_id"),
        nullable=False,
    )
    employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
    )

    # Assignment details
    assignment_type: Mapped[AssignmentType] = mapped_column(
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    end_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment="NULL means assignment is still active",
    )

    # Odometer tracking
    start_odometer: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Odometer reading at start of assignment",
    )
    end_odometer: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Odometer reading at end of assignment",
    )

    # Metadata
    reason: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="Reason for assignment/change",
    )
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        default=True,
        comment="Current active assignment flag",
    )

    # Relationships
    vehicle: Mapped["Vehicle"] = relationship(
        "Vehicle",
        back_populates="assignments",
    )
    employee: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[employee_id],
        lazy="joined",
    )

    @property
    def distance_traveled(self) -> Optional[int]:
        """Calculate distance traveled during assignment."""
        if self.start_odometer is not None and self.end_odometer is not None:
            return self.end_odometer - self.start_odometer
        return None

    @property
    def duration_days(self) -> Optional[int]:
        """Calculate duration of assignment in days."""
        end = self.end_date or date.today()
        return (end - self.start_date).days
