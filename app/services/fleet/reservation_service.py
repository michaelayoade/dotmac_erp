"""
Reservation Service - Pool vehicle reservation management.

Handles reservation requests, approvals, and vehicle checkouts.
"""

import logging
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.fleet.enums import AssignmentType, ReservationStatus, VehicleStatus
from app.models.fleet.vehicle import Vehicle
from app.models.fleet.vehicle_reservation import VehicleReservation
from app.schemas.fleet.reservation import (
    ReservationCheckin,
    ReservationCheckout,
    ReservationCreate,
    ReservationUpdate,
)
from app.services.common import (
    ConflictError,
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
    paginate,
)
from app.services.state_machine import StateMachine

logger = logging.getLogger(__name__)


# Valid reservation status transitions
RESERVATION_STATUS_TRANSITIONS: dict[ReservationStatus, set] = {
    ReservationStatus.PENDING: {
        ReservationStatus.APPROVED,
        ReservationStatus.REJECTED,
        ReservationStatus.CANCELLED,
    },
    ReservationStatus.APPROVED: {
        ReservationStatus.ACTIVE,
        ReservationStatus.CANCELLED,
        ReservationStatus.NO_SHOW,
    },
    ReservationStatus.REJECTED: set(),  # Terminal
    ReservationStatus.ACTIVE: {
        ReservationStatus.COMPLETED,
    },
    ReservationStatus.COMPLETED: set(),  # Terminal
    ReservationStatus.CANCELLED: set(),  # Terminal
    ReservationStatus.NO_SHOW: set(),  # Terminal
}
_STATE_MACHINE = StateMachine(RESERVATION_STATUS_TRANSITIONS)


class ReservationService:
    """Service for vehicle reservation operations."""

    def __init__(self, db: Session, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    def get_by_id(self, reservation_id: UUID) -> VehicleReservation | None:
        """Get reservation by ID."""
        return self.db.get(VehicleReservation, reservation_id)

    def get_or_raise(self, reservation_id: UUID) -> VehicleReservation:
        """Get reservation or raise NotFoundError."""
        reservation = self.get_by_id(reservation_id)
        if not reservation or reservation.organization_id != self.organization_id:
            raise NotFoundError(f"Reservation {reservation_id} not found")
        return reservation

    def list_reservations(
        self,
        *,
        vehicle_id: UUID | None = None,
        employee_id: UUID | None = None,
        status: ReservationStatus | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        params: PaginationParams | None = None,
    ) -> PaginatedResult[VehicleReservation]:
        """List reservations with filtering."""
        stmt = (
            select(VehicleReservation)
            .where(VehicleReservation.organization_id == self.organization_id)
            .options(
                selectinload(VehicleReservation.vehicle),
                selectinload(VehicleReservation.employee),
            )
            .order_by(VehicleReservation.start_datetime.desc())
        )

        if vehicle_id:
            stmt = stmt.where(VehicleReservation.vehicle_id == vehicle_id)

        if employee_id:
            stmt = stmt.where(VehicleReservation.employee_id == employee_id)

        if status:
            stmt = stmt.where(VehicleReservation.status == status)

        if from_date:
            stmt = stmt.where(VehicleReservation.start_datetime >= from_date)

        if to_date:
            stmt = stmt.where(VehicleReservation.end_datetime <= to_date)

        return paginate(self.db, stmt, params)

    def get_pending_reservations(
        self, *, limit: int | None = None
    ) -> list[VehicleReservation]:
        """Get all pending reservations awaiting approval."""
        stmt = (
            select(VehicleReservation)
            .where(
                VehicleReservation.organization_id == self.organization_id,
                VehicleReservation.status == ReservationStatus.PENDING,
            )
            .options(
                selectinload(VehicleReservation.vehicle),
                selectinload(VehicleReservation.employee),
            )
            .order_by(VehicleReservation.start_datetime.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def get_active_reservations(
        self, *, limit: int | None = None
    ) -> list[VehicleReservation]:
        """Get all currently active reservations."""
        stmt = (
            select(VehicleReservation)
            .where(
                VehicleReservation.organization_id == self.organization_id,
                VehicleReservation.status == ReservationStatus.ACTIVE,
            )
            .options(selectinload(VehicleReservation.vehicle))
            .order_by(VehicleReservation.actual_end_datetime.asc().nullslast())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def check_availability(
        self,
        vehicle_id: UUID,
        start_datetime: datetime,
        end_datetime: datetime,
        exclude_reservation_id: UUID | None = None,
    ) -> bool:
        """Check if vehicle is available for the given time period."""
        stmt = select(VehicleReservation).where(
            VehicleReservation.vehicle_id == vehicle_id,
            VehicleReservation.status.in_(
                [ReservationStatus.APPROVED, ReservationStatus.ACTIVE]
            ),
            VehicleReservation.start_datetime < end_datetime,
            VehicleReservation.end_datetime > start_datetime,
        )

        if exclude_reservation_id:
            stmt = stmt.where(
                VehicleReservation.reservation_id != exclude_reservation_id
            )

        conflict = self.db.scalar(stmt)
        return conflict is None

    def create(self, data: ReservationCreate) -> VehicleReservation:
        """Create a new reservation request."""
        # Verify vehicle exists and is a pool vehicle
        vehicle = self.db.get(Vehicle, data.vehicle_id)
        if not vehicle or vehicle.organization_id != self.organization_id:
            raise NotFoundError(f"Vehicle {data.vehicle_id} not found")

        if vehicle.assignment_type != AssignmentType.POOL:
            raise ValidationError("Only pool vehicles can be reserved")

        if vehicle.status != VehicleStatus.ACTIVE:
            raise ValidationError("Vehicle is not available for reservation")

        # Validate dates
        if data.start_datetime >= data.end_datetime:
            raise ValidationError("End time must be after start time")

        if data.start_datetime < datetime.now(UTC):
            raise ValidationError("Cannot create reservation in the past")

        # Check availability
        if not self.check_availability(
            data.vehicle_id, data.start_datetime, data.end_datetime
        ):
            raise ConflictError(
                "Vehicle is not available for the requested time period"
            )

        reservation = VehicleReservation(
            organization_id=self.organization_id,
            **data.model_dump(),
        )

        self.db.add(reservation)
        self.db.flush()

        logger.info(
            "Created reservation for vehicle %s by employee %s",
            vehicle.vehicle_code,
            data.employee_id,
        )
        return reservation

    def update(
        self, reservation_id: UUID, data: ReservationUpdate
    ) -> VehicleReservation:
        """Update a reservation."""
        reservation = self.get_or_raise(reservation_id)

        if reservation.status not in (
            ReservationStatus.PENDING,
            ReservationStatus.APPROVED,
        ):
            raise ValidationError("Can only update pending or approved reservations")

        # Check availability if dates are changing
        new_start = data.start_datetime or reservation.start_datetime
        new_end = data.end_datetime or reservation.end_datetime
        new_vehicle = data.vehicle_id or reservation.vehicle_id

        if data.start_datetime or data.end_datetime or data.vehicle_id:
            if not self.check_availability(
                new_vehicle, new_start, new_end, exclude_reservation_id=reservation_id
            ):
                raise ConflictError(
                    "Vehicle is not available for the requested time period"
                )

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(reservation, field, value)

        logger.info("Updated reservation %s", reservation_id)
        return reservation

    def _validate_transition(
        self, current: ReservationStatus, target: ReservationStatus
    ) -> None:
        """Validate a status transition against the transition map."""
        _STATE_MACHINE.validate(current, target)

    def approve(
        self,
        reservation_id: UUID,
        approved_by_id: UUID,
    ) -> VehicleReservation:
        """Approve a reservation request."""
        reservation = self.get_or_raise(reservation_id)
        self._validate_transition(reservation.status, ReservationStatus.APPROVED)

        # Verify availability again
        if not self.check_availability(
            reservation.vehicle_id,
            reservation.start_datetime,
            reservation.end_datetime,
            exclude_reservation_id=reservation_id,
        ):
            raise ConflictError("Vehicle is no longer available for this time period")

        reservation.status = ReservationStatus.APPROVED
        reservation.approved_by_id = approved_by_id
        reservation.approved_at = datetime.now(UTC)

        logger.info("Approved reservation %s", reservation_id)
        return reservation

    def reject(
        self,
        reservation_id: UUID,
        reason: str,
    ) -> VehicleReservation:
        """Reject a reservation request."""
        reservation = self.get_or_raise(reservation_id)
        self._validate_transition(reservation.status, ReservationStatus.REJECTED)

        reservation.status = ReservationStatus.REJECTED
        reservation.rejection_reason = reason

        logger.info("Rejected reservation %s: %s", reservation_id, reason)
        return reservation

    def checkout(
        self,
        reservation_id: UUID,
        data: ReservationCheckout,
    ) -> VehicleReservation:
        """Check out vehicle (start the reservation)."""
        reservation = self.get_or_raise(reservation_id)
        self._validate_transition(reservation.status, ReservationStatus.ACTIVE)

        reservation.status = ReservationStatus.ACTIVE
        reservation.actual_start_datetime = data.actual_start_datetime or datetime.now(
            UTC
        )
        reservation.start_odometer = data.start_odometer

        # Update vehicle status
        vehicle = self.db.get(Vehicle, reservation.vehicle_id)
        if vehicle:
            vehicle.status = VehicleStatus.RESERVED

        logger.info("Checked out reservation %s", reservation_id)
        return reservation

    def checkin(
        self,
        reservation_id: UUID,
        data: ReservationCheckin,
    ) -> VehicleReservation:
        """Check in vehicle (complete the reservation)."""
        reservation = self.get_or_raise(reservation_id)
        self._validate_transition(reservation.status, ReservationStatus.COMPLETED)

        reservation.status = ReservationStatus.COMPLETED
        reservation.actual_end_datetime = data.actual_end_datetime or datetime.now(UTC)
        reservation.end_odometer = data.end_odometer

        if data.notes:
            reservation.notes = f"{reservation.notes or ''}\n{data.notes}".strip()

        # Update vehicle
        vehicle = self.db.get(Vehicle, reservation.vehicle_id)
        if vehicle:
            vehicle.status = VehicleStatus.ACTIVE
            if (
                data.end_odometer is not None
                and data.end_odometer > vehicle.current_odometer
            ):
                vehicle.current_odometer = data.end_odometer
                vehicle.last_odometer_date = date.today()

        logger.info("Checked in reservation %s", reservation_id)
        return reservation

    def cancel(self, reservation_id: UUID) -> VehicleReservation:
        """Cancel a reservation."""
        reservation = self.get_or_raise(reservation_id)

        if not reservation.can_be_cancelled:
            raise ValidationError("Cannot cancel this reservation")

        reservation.status = ReservationStatus.CANCELLED

        logger.info("Cancelled reservation %s", reservation_id)
        return reservation

    def mark_no_show(self, reservation_id: UUID) -> VehicleReservation:
        """Mark reservation as no-show (employee didn't pick up)."""
        reservation = self.get_or_raise(reservation_id)
        self._validate_transition(reservation.status, ReservationStatus.NO_SHOW)

        reservation.status = ReservationStatus.NO_SHOW

        logger.info("Marked reservation %s as no-show", reservation_id)
        return reservation
