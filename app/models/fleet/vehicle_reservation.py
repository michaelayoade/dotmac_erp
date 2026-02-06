"""
Vehicle Reservation Model - Fleet Schema.

Tracks pool vehicle reservation requests.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.fleet.base import FleetBaseMixin
from app.models.fleet.enums import ReservationStatus
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.fleet.vehicle import Vehicle
    from app.models.people.hr.employee import Employee


class VehicleReservation(Base, FleetBaseMixin, AuditMixin):
    """
    Pool vehicle reservation request.

    Allows employees to reserve pool vehicles for business trips.
    Supports:
    - Approval workflow
    - Date/time range booking
    - Trip details and purpose
    - Odometer tracking at pickup/return
    """

    __tablename__ = "vehicle_reservation"
    __table_args__ = (
        Index(
            "idx_fleet_reservation_vehicle_dates",
            "vehicle_id",
            "start_datetime",
            "end_datetime",
        ),
        Index("idx_fleet_reservation_employee", "employee_id"),
        Index("idx_fleet_reservation_status", "organization_id", "status"),
        Index(
            "idx_fleet_reservation_pending",
            "organization_id",
            "status",
            "start_datetime",
        ),
        {"schema": "fleet"},
    )

    # Primary key
    reservation_id: Mapped[uuid.UUID] = mapped_column(
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
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
        comment="Employee requesting the vehicle",
    )

    # Reservation period
    start_datetime: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    end_datetime: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    actual_start_datetime: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When vehicle was actually picked up",
    )
    actual_end_datetime: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When vehicle was actually returned",
    )

    # Trip details
    purpose: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    destination: Mapped[Optional[str]] = mapped_column(
        String(300),
        nullable=True,
    )
    estimated_distance_km: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    # Status
    status: Mapped[ReservationStatus] = mapped_column(
        default=ReservationStatus.PENDING,
    )

    # Approval
    approved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(
        String(300),
        nullable=True,
    )

    # Odometer tracking
    start_odometer: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Odometer at pickup",
    )
    end_odometer: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Odometer at return",
    )

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    vehicle: Mapped["Vehicle"] = relationship(
        "Vehicle",
        back_populates="reservations",
    )
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
        lazy="joined",
    )
    approved_by: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[approved_by_id],
        lazy="joined",
    )

    @property
    def duration_hours(self) -> float:
        """Calculate planned reservation duration in hours."""
        delta = self.end_datetime - self.start_datetime
        return delta.total_seconds() / 3600

    @property
    def actual_duration_hours(self) -> Optional[float]:
        """Calculate actual usage duration in hours."""
        if self.actual_start_datetime and self.actual_end_datetime:
            delta = self.actual_end_datetime - self.actual_start_datetime
            return delta.total_seconds() / 3600
        return None

    @property
    def actual_distance_km(self) -> Optional[int]:
        """Calculate actual distance traveled."""
        if self.start_odometer is not None and self.end_odometer is not None:
            return self.end_odometer - self.start_odometer
        return None

    @property
    def is_active(self) -> bool:
        """Check if reservation is currently active."""
        return self.status == ReservationStatus.ACTIVE

    @property
    def can_be_cancelled(self) -> bool:
        """Check if reservation can be cancelled."""
        return self.status in (ReservationStatus.PENDING, ReservationStatus.APPROVED)
