"""
Incident Service - Vehicle incident management.

Handles incident reporting, investigation, and resolution.
"""
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.fleet.enums import IncidentSeverity, IncidentStatus, IncidentType
from app.models.fleet.vehicle import Vehicle
from app.models.fleet.vehicle_incident import VehicleIncident
from app.schemas.fleet.incident import IncidentCreate, IncidentResolve, IncidentUpdate
from app.services.common import (
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
    paginate,
)

logger = logging.getLogger(__name__)


# Valid incident status transitions
INCIDENT_STATUS_TRANSITIONS: Dict[IncidentStatus, set] = {
    IncidentStatus.REPORTED: {
        IncidentStatus.INVESTIGATING,
        IncidentStatus.INSURANCE_FILED,
        IncidentStatus.RESOLVED,
        IncidentStatus.CLOSED,
    },
    IncidentStatus.INVESTIGATING: {
        IncidentStatus.INSURANCE_FILED,
        IncidentStatus.RESOLVED,
        IncidentStatus.CLOSED,
    },
    IncidentStatus.INSURANCE_FILED: {
        IncidentStatus.RESOLVED,
        IncidentStatus.CLOSED,
    },
    IncidentStatus.RESOLVED: {
        IncidentStatus.CLOSED,
    },
    IncidentStatus.CLOSED: set(),  # Terminal
}


class IncidentService:
    """Service for vehicle incident operations."""

    def __init__(self, db: Session, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    def get_by_id(self, incident_id: UUID) -> Optional[VehicleIncident]:
        """Get incident by ID."""
        return self.db.get(VehicleIncident, incident_id)

    def get_or_raise(self, incident_id: UUID) -> VehicleIncident:
        """Get incident or raise NotFoundError."""
        incident = self.get_by_id(incident_id)
        if not incident or incident.organization_id != self.organization_id:
            raise NotFoundError(f"Incident {incident_id} not found")
        if incident.is_deleted:
            raise NotFoundError(f"Incident {incident_id} has been deleted")
        return incident

    def list_incidents(
        self,
        *,
        vehicle_id: Optional[UUID] = None,
        driver_id: Optional[UUID] = None,
        status: Optional[IncidentStatus] = None,
        incident_type: Optional[IncidentType] = None,
        severity: Optional[IncidentSeverity] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        params: Optional[PaginationParams] = None,
    ) -> PaginatedResult[VehicleIncident]:
        """List incidents with filtering."""
        stmt = (
            select(VehicleIncident)
            .where(
                VehicleIncident.organization_id == self.organization_id,
                VehicleIncident.is_deleted == False,  # noqa: E712
            )
            .options(
                selectinload(VehicleIncident.vehicle),
                selectinload(VehicleIncident.reported_by),
                selectinload(VehicleIncident.driver),
            )
            .order_by(VehicleIncident.incident_date.desc())
        )

        if vehicle_id:
            stmt = stmt.where(VehicleIncident.vehicle_id == vehicle_id)

        if driver_id:
            stmt = stmt.where(VehicleIncident.driver_id == driver_id)

        if status:
            stmt = stmt.where(VehicleIncident.status == status)

        if incident_type:
            stmt = stmt.where(VehicleIncident.incident_type == incident_type)

        if severity:
            stmt = stmt.where(VehicleIncident.severity == severity)

        if from_date:
            stmt = stmt.where(VehicleIncident.incident_date >= from_date)

        if to_date:
            stmt = stmt.where(VehicleIncident.incident_date <= to_date)

        return paginate(self.db, stmt, params)

    def get_open_incidents(self) -> List[VehicleIncident]:
        """Get all open (non-closed) incidents."""
        stmt = (
            select(VehicleIncident)
            .where(
                VehicleIncident.organization_id == self.organization_id,
                VehicleIncident.is_deleted == False,  # noqa: E712
                VehicleIncident.status != IncidentStatus.CLOSED,
            )
            .options(selectinload(VehicleIncident.vehicle))
            .order_by(VehicleIncident.incident_date.desc())
        )
        return list(self.db.scalars(stmt).all())

    def create(self, data: IncidentCreate) -> VehicleIncident:
        """Report a new incident."""
        # Verify vehicle exists
        vehicle = self.db.get(Vehicle, data.vehicle_id)
        if not vehicle or vehicle.organization_id != self.organization_id:
            raise NotFoundError(f"Vehicle {data.vehicle_id} not found")

        incident = VehicleIncident(
            organization_id=self.organization_id,
            **data.model_dump(),
        )

        self.db.add(incident)
        self.db.flush()

        logger.info(
            "Reported incident for vehicle %s: %s (%s)",
            vehicle.vehicle_code,
            data.incident_type.value,
            data.severity.value,
        )
        return incident

    def update(self, incident_id: UUID, data: IncidentUpdate) -> VehicleIncident:
        """Update incident details."""
        incident = self.get_or_raise(incident_id)

        if incident.status == IncidentStatus.CLOSED:
            raise ValidationError("Cannot update closed incident")

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(incident, field, value)

        logger.info("Updated incident %s", incident_id)
        return incident

    def change_status(
        self,
        incident_id: UUID,
        new_status: IncidentStatus,
    ) -> VehicleIncident:
        """Change incident status with validation."""
        incident = self.get_or_raise(incident_id)
        current = incident.status

        allowed = INCIDENT_STATUS_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise ValidationError(
                f"Cannot transition from {current.value} to {new_status.value}"
            )

        incident.status = new_status

        logger.info(
            "Incident %s status changed: %s -> %s",
            incident_id,
            current.value,
            new_status.value,
        )
        return incident

    def resolve(self, incident_id: UUID, data: IncidentResolve) -> VehicleIncident:
        """Resolve an incident."""
        incident = self.get_or_raise(incident_id)

        if incident.status == IncidentStatus.CLOSED:
            raise ValidationError("Incident is already closed")

        incident.status = IncidentStatus.RESOLVED
        incident.resolution_date = data.resolution_date or date.today()
        incident.resolution_notes = data.resolution_notes

        if data.actual_repair_cost is not None:
            incident.actual_repair_cost = data.actual_repair_cost

        if data.other_costs is not None:
            incident.other_costs = data.other_costs

        if data.insurance_payout is not None:
            incident.insurance_payout = data.insurance_payout

        logger.info("Resolved incident %s", incident_id)
        return incident

    def close(self, incident_id: UUID) -> VehicleIncident:
        """Close an incident."""
        incident = self.get_or_raise(incident_id)

        if incident.status == IncidentStatus.CLOSED:
            raise ValidationError("Incident is already closed")

        incident.status = IncidentStatus.CLOSED
        if not incident.resolution_date:
            incident.resolution_date = date.today()

        logger.info("Closed incident %s", incident_id)
        return incident

    def soft_delete(self, incident_id: UUID) -> VehicleIncident:
        """Soft delete an incident."""
        incident = self.get_or_raise(incident_id)
        incident.is_deleted = True
        incident.deleted_at = datetime.now()

        logger.info("Soft deleted incident %s", incident_id)
        return incident

    def get_cost_summary(self, vehicle_id: Optional[UUID] = None) -> dict:
        """Get incident cost summary."""
        stmt = select(VehicleIncident).where(
            VehicleIncident.organization_id == self.organization_id,
            VehicleIncident.is_deleted == False,  # noqa: E712
        )

        if vehicle_id:
            stmt = stmt.where(VehicleIncident.vehicle_id == vehicle_id)

        incidents = list(self.db.scalars(stmt).all())

        total_repair = sum(i.actual_repair_cost or Decimal(0) for i in incidents)
        total_other = sum(i.other_costs or Decimal(0) for i in incidents)
        total_payout = sum(i.insurance_payout or Decimal(0) for i in incidents)

        return {
            "total_incidents": len(incidents),
            "total_repair_cost": total_repair,
            "total_other_costs": total_other,
            "total_insurance_payout": total_payout,
            "net_cost": total_repair + total_other - total_payout,
        }
