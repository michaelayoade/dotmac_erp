"""
Assignment Service - Vehicle assignment management.

Handles assigning vehicles to employees and departments.
"""
import logging
from datetime import date
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.fleet.enums import AssignmentType, VehicleStatus
from app.models.fleet.vehicle import Vehicle
from app.models.fleet.vehicle_assignment import VehicleAssignment
from app.schemas.fleet.assignment import AssignmentCreate, AssignmentEnd, AssignmentUpdate
from app.services.common import (
    ConflictError,
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
    paginate,
)

logger = logging.getLogger(__name__)


class AssignmentService:
    """Service for vehicle assignment operations."""

    def __init__(self, db: Session, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    def get_by_id(self, assignment_id: UUID) -> Optional[VehicleAssignment]:
        """Get assignment by ID."""
        return self.db.get(VehicleAssignment, assignment_id)

    def get_or_raise(self, assignment_id: UUID) -> VehicleAssignment:
        """Get assignment or raise NotFoundError."""
        assignment = self.get_by_id(assignment_id)
        if not assignment or assignment.organization_id != self.organization_id:
            raise NotFoundError(f"Assignment {assignment_id} not found")
        return assignment

    def get_active_assignment(self, vehicle_id: UUID) -> Optional[VehicleAssignment]:
        """Get the current active assignment for a vehicle."""
        stmt = select(VehicleAssignment).where(
            VehicleAssignment.organization_id == self.organization_id,
            VehicleAssignment.vehicle_id == vehicle_id,
            VehicleAssignment.is_active == True,  # noqa: E712
        )
        return self.db.scalar(stmt)

    def list_assignments(
        self,
        *,
        vehicle_id: Optional[UUID] = None,
        employee_id: Optional[UUID] = None,
        department_id: Optional[UUID] = None,
        active_only: bool = False,
        params: Optional[PaginationParams] = None,
    ) -> PaginatedResult[VehicleAssignment]:
        """List assignments with filtering."""
        stmt = (
            select(VehicleAssignment)
            .where(VehicleAssignment.organization_id == self.organization_id)
            .options(
                selectinload(VehicleAssignment.vehicle),
                selectinload(VehicleAssignment.employee),
            )
            .order_by(VehicleAssignment.start_date.desc())
        )

        if vehicle_id:
            stmt = stmt.where(VehicleAssignment.vehicle_id == vehicle_id)

        if employee_id:
            stmt = stmt.where(VehicleAssignment.employee_id == employee_id)

        if department_id:
            stmt = stmt.where(VehicleAssignment.department_id == department_id)

        if active_only:
            stmt = stmt.where(VehicleAssignment.is_active == True)  # noqa: E712

        return paginate(self.db, stmt, params)

    def get_employee_vehicles(self, employee_id: UUID) -> List[Vehicle]:
        """Get all vehicles currently assigned to an employee."""
        stmt = (
            select(Vehicle)
            .where(
                Vehicle.organization_id == self.organization_id,
                Vehicle.assigned_employee_id == employee_id,
                Vehicle.is_deleted == False,  # noqa: E712
                Vehicle.status != VehicleStatus.DISPOSED,
            )
            .order_by(Vehicle.vehicle_code)
        )
        return list(self.db.scalars(stmt).all())

    def create(self, data: AssignmentCreate) -> VehicleAssignment:
        """Create a new assignment."""
        # Verify vehicle exists
        vehicle = self.db.get(Vehicle, data.vehicle_id)
        if not vehicle or vehicle.organization_id != self.organization_id:
            raise NotFoundError(f"Vehicle {data.vehicle_id} not found")

        if vehicle.status == VehicleStatus.DISPOSED:
            raise ValidationError("Cannot assign disposed vehicle")

        # Validate assignment type requirements
        if data.assignment_type == AssignmentType.PERSONAL and not data.employee_id:
            raise ValidationError("Personal assignment requires an employee")

        if data.assignment_type == AssignmentType.DEPARTMENT and not data.department_id:
            raise ValidationError("Department assignment requires a department")

        # End any active assignment for this vehicle
        active = self.get_active_assignment(data.vehicle_id)
        if active:
            active.is_active = False
            active.end_date = data.start_date
            if vehicle.current_odometer:
                active.end_odometer = vehicle.current_odometer

        # Create new assignment
        assignment = VehicleAssignment(
            organization_id=self.organization_id,
            **data.model_dump(),
        )

        self.db.add(assignment)
        self.db.flush()

        # Update vehicle assignment fields
        vehicle.assignment_type = data.assignment_type
        vehicle.assigned_employee_id = data.employee_id
        vehicle.assigned_department_id = data.department_id

        logger.info(
            "Created assignment for vehicle %s: %s",
            vehicle.vehicle_code,
            data.assignment_type.value,
        )
        return assignment

    def update(self, assignment_id: UUID, data: AssignmentUpdate) -> VehicleAssignment:
        """Update an assignment."""
        assignment = self.get_or_raise(assignment_id)

        if not assignment.is_active:
            raise ValidationError("Cannot update inactive assignment")

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(assignment, field, value)

        logger.info("Updated assignment %s", assignment_id)
        return assignment

    def end_assignment(
        self,
        assignment_id: UUID,
        data: AssignmentEnd,
    ) -> VehicleAssignment:
        """End an active assignment."""
        assignment = self.get_or_raise(assignment_id)

        if not assignment.is_active:
            raise ValidationError("Assignment is already ended")

        assignment.is_active = False
        assignment.end_date = data.end_date or date.today()
        assignment.end_odometer = data.end_odometer

        if data.reason:
            assignment.reason = f"{assignment.reason or ''}\nEnded: {data.reason}".strip()

        # Update vehicle - clear assignment if this was the active one
        vehicle = self.db.get(Vehicle, assignment.vehicle_id)
        if vehicle and vehicle.assigned_employee_id == assignment.employee_id:
            vehicle.assignment_type = AssignmentType.POOL
            vehicle.assigned_employee_id = None
            vehicle.assigned_department_id = None

        logger.info("Ended assignment %s", assignment_id)
        return assignment

    def transfer_vehicle(
        self,
        vehicle_id: UUID,
        to_employee_id: Optional[UUID] = None,
        to_department_id: Optional[UUID] = None,
        reason: Optional[str] = None,
    ) -> VehicleAssignment:
        """Transfer a vehicle to a new employee or department."""
        vehicle = self.db.get(Vehicle, vehicle_id)
        if not vehicle or vehicle.organization_id != self.organization_id:
            raise NotFoundError(f"Vehicle {vehicle_id} not found")

        # Determine assignment type
        if to_employee_id:
            assignment_type = AssignmentType.PERSONAL
        elif to_department_id:
            assignment_type = AssignmentType.DEPARTMENT
        else:
            assignment_type = AssignmentType.POOL

        # Create new assignment
        data = AssignmentCreate(
            vehicle_id=vehicle_id,
            assignment_type=assignment_type,
            start_date=date.today(),
            start_odometer=vehicle.current_odometer,
            employee_id=to_employee_id,
            department_id=to_department_id,
            reason=reason or "Vehicle transfer",
        )

        return self.create(data)
