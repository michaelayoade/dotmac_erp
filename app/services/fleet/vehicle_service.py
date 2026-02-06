"""
Vehicle Service - Core fleet management operations.

Handles vehicle CRUD, status transitions, odometer updates, and fleet statistics.
"""

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy import case, extract, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.fleet.enums import (
    AssignmentType,
    DisposalMethod,
    OwnershipType,
    VehicleStatus,
    VehicleType,
)
from app.models.fleet.vehicle import Vehicle
from app.schemas.fleet.vehicle import VehicleCreate, VehicleUpdate
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


# Valid status transitions
VEHICLE_STATUS_TRANSITIONS: Dict[VehicleStatus, set] = {
    VehicleStatus.ACTIVE: {
        VehicleStatus.MAINTENANCE,
        VehicleStatus.OUT_OF_SERVICE,
        VehicleStatus.RESERVED,
        VehicleStatus.DISPOSED,
    },
    VehicleStatus.MAINTENANCE: {
        VehicleStatus.ACTIVE,
        VehicleStatus.OUT_OF_SERVICE,
        VehicleStatus.DISPOSED,
    },
    VehicleStatus.OUT_OF_SERVICE: {
        VehicleStatus.ACTIVE,
        VehicleStatus.MAINTENANCE,
        VehicleStatus.DISPOSED,
    },
    VehicleStatus.RESERVED: {
        VehicleStatus.ACTIVE,
        VehicleStatus.MAINTENANCE,
    },
    VehicleStatus.DISPOSED: set(),  # Terminal state
}
_STATE_MACHINE = StateMachine(VEHICLE_STATUS_TRANSITIONS)


class VehicleService:
    """Service for vehicle management operations."""

    def __init__(self, db: Session, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    # ─────────────────────────────────────────────────────────────
    # Read Operations
    # ─────────────────────────────────────────────────────────────

    def get_by_id(self, vehicle_id: UUID) -> Optional[Vehicle]:
        """Get vehicle by ID."""
        return self.db.get(Vehicle, vehicle_id)

    def get_or_raise(self, vehicle_id: UUID) -> Vehicle:
        """Get vehicle or raise NotFoundError."""
        vehicle = self.get_by_id(vehicle_id)
        if not vehicle or vehicle.organization_id != self.organization_id:
            raise NotFoundError(f"Vehicle {vehicle_id} not found")
        if vehicle.is_deleted:
            raise NotFoundError(f"Vehicle {vehicle_id} has been deleted")
        return vehicle

    def get_by_registration(self, registration_number: str) -> Optional[Vehicle]:
        """Find vehicle by registration number."""
        stmt = select(Vehicle).where(
            Vehicle.organization_id == self.organization_id,
            Vehicle.registration_number == registration_number,
            Vehicle.is_deleted == False,  # noqa: E712
        )
        return self.db.scalar(stmt)

    def get_by_code(self, vehicle_code: str) -> Optional[Vehicle]:
        """Find vehicle by internal code."""
        stmt = select(Vehicle).where(
            Vehicle.organization_id == self.organization_id,
            Vehicle.vehicle_code == vehicle_code,
            Vehicle.is_deleted == False,  # noqa: E712
        )
        return self.db.scalar(stmt)

    def list_vehicles(
        self,
        *,
        status: Optional[VehicleStatus] = None,
        vehicle_type: Optional[VehicleType] = None,
        assignment_type: Optional[AssignmentType] = None,
        ownership_type: Optional[OwnershipType] = None,
        assigned_employee_id: Optional[UUID] = None,
        search: Optional[str] = None,
        include_disposed: bool = False,
        params: Optional[PaginationParams] = None,
    ) -> PaginatedResult[Vehicle]:
        """
        List vehicles with filtering and pagination.

        Args:
            status: Filter by vehicle status
            vehicle_type: Filter by type (SEDAN, SUV, etc.)
            assignment_type: Filter by assignment (PERSONAL, POOL, etc.)
            ownership_type: Filter by ownership (OWNED, LEASED)
            assigned_employee_id: Filter by assigned employee
            search: Search in registration, make, model, code
            include_disposed: Include disposed vehicles
            params: Pagination parameters
        """
        stmt = (
            select(Vehicle)
            .where(
                Vehicle.organization_id == self.organization_id,
                Vehicle.is_deleted == False,  # noqa: E712
            )
            .options(
                selectinload(Vehicle.documents),
                selectinload(Vehicle.assigned_employee),
            )
            .order_by(Vehicle.vehicle_code.asc())
        )

        if not include_disposed:
            stmt = stmt.where(Vehicle.status != VehicleStatus.DISPOSED)

        if status:
            stmt = stmt.where(Vehicle.status == status)

        if vehicle_type:
            stmt = stmt.where(Vehicle.vehicle_type == vehicle_type)

        if assignment_type:
            stmt = stmt.where(Vehicle.assignment_type == assignment_type)

        if ownership_type:
            stmt = stmt.where(Vehicle.ownership_type == ownership_type)

        if assigned_employee_id:
            stmt = stmt.where(Vehicle.assigned_employee_id == assigned_employee_id)

        if search:
            search_pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    Vehicle.registration_number.ilike(search_pattern),
                    Vehicle.make.ilike(search_pattern),
                    Vehicle.model.ilike(search_pattern),
                    Vehicle.vehicle_code.ilike(search_pattern),
                )
            )

        return paginate(self.db, stmt, params)

    def count_by_status(self) -> Dict[str, int]:
        """Get vehicle counts grouped by status."""
        stmt = (
            select(Vehicle.status, func.count(Vehicle.vehicle_id))
            .where(
                Vehicle.organization_id == self.organization_id,
                Vehicle.is_deleted == False,  # noqa: E712
            )
            .group_by(Vehicle.status)
        )
        results = self.db.execute(stmt).all()
        return {status.value: count for status, count in results}

    def get_available_pool_vehicles(
        self,
        start_datetime,
        end_datetime,
    ) -> List[Vehicle]:
        """Get pool vehicles available for reservation in a time period."""
        from app.models.fleet.enums import ReservationStatus
        from app.models.fleet.vehicle_reservation import VehicleReservation

        # Subquery for vehicles with conflicting reservations
        conflicting = (
            select(VehicleReservation.vehicle_id)
            .where(
                VehicleReservation.status.in_(
                    [
                        ReservationStatus.APPROVED,
                        ReservationStatus.ACTIVE,
                    ]
                ),
                VehicleReservation.start_datetime < end_datetime,
                VehicleReservation.end_datetime > start_datetime,
            )
            .subquery()
        )

        stmt = (
            select(Vehicle)
            .where(
                Vehicle.organization_id == self.organization_id,
                Vehicle.is_deleted == False,  # noqa: E712
                Vehicle.status == VehicleStatus.ACTIVE,
                Vehicle.assignment_type == AssignmentType.POOL,
                ~Vehicle.vehicle_id.in_(select(conflicting.c.vehicle_id)),
            )
            .order_by(Vehicle.vehicle_code)
        )

        return list(self.db.scalars(stmt).all())

    # ─────────────────────────────────────────────────────────────
    # Write Operations
    # ─────────────────────────────────────────────────────────────

    def create(self, data: VehicleCreate) -> Vehicle:
        """
        Create a new vehicle.

        Validates uniqueness of registration and vehicle code.
        """
        # Check registration uniqueness
        existing = self.get_by_registration(data.registration_number)
        if existing:
            raise ConflictError(
                f"Vehicle with registration {data.registration_number} already exists"
            )

        # Check code uniqueness
        existing_code = self.get_by_code(data.vehicle_code)
        if existing_code:
            raise ConflictError(f"Vehicle code {data.vehicle_code} already exists")

        vehicle = Vehicle(
            organization_id=self.organization_id,
            **data.model_dump(),
        )

        self.db.add(vehicle)
        self.db.flush()

        logger.info(
            "Created vehicle %s: %s",
            vehicle.vehicle_code,
            vehicle.registration_number,
        )
        return vehicle

    def update(self, vehicle_id: UUID, data: VehicleUpdate) -> Vehicle:
        """Update vehicle details."""
        vehicle = self.get_or_raise(vehicle_id)

        # Check registration uniqueness if changing
        if (
            data.registration_number
            and data.registration_number != vehicle.registration_number
        ):
            existing = self.get_by_registration(data.registration_number)
            if existing:
                raise ConflictError(
                    f"Registration {data.registration_number} already in use"
                )

        # Check code uniqueness if changing
        if data.vehicle_code and data.vehicle_code != vehicle.vehicle_code:
            existing = self.get_by_code(data.vehicle_code)
            if existing:
                raise ConflictError(f"Vehicle code {data.vehicle_code} already in use")

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(vehicle, field, value)

        logger.info("Updated vehicle %s", vehicle.vehicle_code)
        return vehicle

    def change_status(
        self,
        vehicle_id: UUID,
        new_status: VehicleStatus,
        reason: Optional[str] = None,
    ) -> Vehicle:
        """
        Change vehicle status with transition validation.

        Validates that the transition is allowed and handles
        status-specific logic (e.g., setting disposal date).
        """
        vehicle = self.get_or_raise(vehicle_id)
        current = vehicle.status

        # Validate transition
        _STATE_MACHINE.validate(current, new_status)

        # Handle disposal
        if new_status == VehicleStatus.DISPOSED:
            if not vehicle.disposal_date:
                vehicle.disposal_date = date.today()
            # Clear assignments
            vehicle.assigned_employee_id = None
            vehicle.assigned_department_id = None

        vehicle.status = new_status

        logger.info(
            "Vehicle %s status changed: %s -> %s (reason: %s)",
            vehicle.vehicle_code,
            current.value,
            new_status.value,
            reason,
        )
        return vehicle

    def update_odometer(
        self,
        vehicle_id: UUID,
        reading: int,
        reading_date: Optional[date] = None,
    ) -> Vehicle:
        """Update vehicle odometer reading."""
        vehicle = self.get_or_raise(vehicle_id)

        if reading < vehicle.current_odometer:
            raise ValidationError(
                f"New odometer reading ({reading}) cannot be less than "
                f"current ({vehicle.current_odometer})"
            )

        vehicle.current_odometer = reading
        vehicle.last_odometer_date = reading_date or date.today()

        logger.info(
            "Updated odometer for %s: %d km",
            vehicle.vehicle_code,
            reading,
        )
        return vehicle

    def dispose(
        self,
        vehicle_id: UUID,
        method: DisposalMethod,
        amount: Optional[Decimal] = None,
        notes: Optional[str] = None,
    ) -> Vehicle:
        """Dispose of a vehicle (sell, scrap, trade-in)."""
        vehicle = self.get_or_raise(vehicle_id)

        # Validate transition via the status map (same as change_status)
        _STATE_MACHINE.validate(vehicle.status, VehicleStatus.DISPOSED)

        vehicle.status = VehicleStatus.DISPOSED
        vehicle.disposal_date = date.today()
        vehicle.disposal_method = method
        vehicle.disposal_amount = amount
        vehicle.disposal_notes = notes
        vehicle.assigned_employee_id = None
        vehicle.assigned_department_id = None

        logger.info(
            "Disposed vehicle %s via %s",
            vehicle.vehicle_code,
            method.value,
        )
        return vehicle

    def soft_delete(self, vehicle_id: UUID) -> Vehicle:
        """Soft delete a vehicle."""
        vehicle = self.get_or_raise(vehicle_id)
        vehicle.is_deleted = True
        vehicle.deleted_at = datetime.now(timezone.utc)

        logger.info("Soft deleted vehicle %s", vehicle.vehicle_code)
        return vehicle

    # ─────────────────────────────────────────────────────────────
    # Fleet Statistics
    # ─────────────────────────────────────────────────────────────

    def get_fleet_summary(self) -> Dict:
        """Get overall fleet statistics using SQL aggregations."""
        not_disposed = Vehicle.status != VehicleStatus.DISPOSED
        base_filter = (
            Vehicle.organization_id == self.organization_id,
            Vehicle.is_deleted == False,  # noqa: E712
        )

        stmt = select(
            # Total non-disposed vehicles
            func.count(case((not_disposed, Vehicle.vehicle_id))).label(
                "total_vehicles"
            ),
            # Status counts
            func.count(
                case((Vehicle.status == VehicleStatus.ACTIVE, Vehicle.vehicle_id))
            ).label("active"),
            func.count(
                case((Vehicle.status == VehicleStatus.MAINTENANCE, Vehicle.vehicle_id))
            ).label("in_maintenance"),
            func.count(
                case(
                    (
                        Vehicle.status == VehicleStatus.OUT_OF_SERVICE,
                        Vehicle.vehicle_id,
                    )
                )
            ).label("out_of_service"),
            func.count(
                case((Vehicle.status == VehicleStatus.DISPOSED, Vehicle.vehicle_id))
            ).label("disposed"),
            # Ownership counts (non-disposed only)
            func.count(
                case(
                    (
                        (not_disposed)
                        & (Vehicle.ownership_type == OwnershipType.OWNED),
                        Vehicle.vehicle_id,
                    )
                )
            ).label("owned_count"),
            func.count(
                case(
                    (
                        (not_disposed)
                        & (Vehicle.ownership_type == OwnershipType.LEASED),
                        Vehicle.vehicle_id,
                    )
                )
            ).label("leased_count"),
            # Financial aggregations (non-disposed only)
            func.coalesce(
                func.sum(
                    case(
                        (
                            (not_disposed)
                            & (Vehicle.ownership_type == OwnershipType.OWNED),
                            Vehicle.purchase_price,
                        )
                    )
                ),
                Decimal(0),
            ).label("total_owned_value"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (not_disposed)
                            & (Vehicle.ownership_type == OwnershipType.LEASED),
                            Vehicle.lease_monthly_cost,
                        )
                    )
                ),
                Decimal(0),
            ).label("monthly_lease_cost"),
            # Average age (non-disposed only)
            func.coalesce(
                func.avg(
                    case(
                        (
                            not_disposed,
                            extract("year", func.current_date()) - Vehicle.year,
                        )
                    )
                ),
                0,
            ).label("avg_age_years"),
        ).where(*base_filter)

        row = self.db.execute(stmt).one()

        return {
            "total_vehicles": row.total_vehicles,
            "active": row.active,
            "in_maintenance": row.in_maintenance,
            "out_of_service": row.out_of_service,
            "disposed": row.disposed,
            "owned_count": row.owned_count,
            "leased_count": row.leased_count,
            "total_owned_value": row.total_owned_value,
            "monthly_lease_cost": row.monthly_lease_cost,
            "avg_age_years": float(row.avg_age_years),
        }
