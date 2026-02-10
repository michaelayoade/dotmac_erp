"""
Incident Service - Vehicle incident management.

Handles incident reporting, investigation, and resolution.
"""

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.finance.audit.audit_log import AuditAction
from app.models.fleet.enums import IncidentSeverity, IncidentStatus, IncidentType
from app.models.fleet.vehicle import Vehicle
from app.models.fleet.vehicle_incident import VehicleIncident
from app.schemas.fleet.incident import IncidentCreate, IncidentResolve, IncidentUpdate
from app.services.audit_dispatcher import fire_audit_event
from app.services.common import (
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
    paginate,
)
from app.services.state_machine import StateMachine

logger = logging.getLogger(__name__)


# Valid incident status transitions
INCIDENT_STATUS_TRANSITIONS: dict[IncidentStatus, set] = {
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
_STATE_MACHINE = StateMachine(INCIDENT_STATUS_TRANSITIONS)


class IncidentService:
    """Service for vehicle incident operations."""

    def __init__(self, db: Session, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    def get_by_id(self, incident_id: UUID) -> VehicleIncident | None:
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
        vehicle_id: UUID | None = None,
        driver_id: UUID | None = None,
        status: IncidentStatus | None = None,
        incident_type: IncidentType | None = None,
        severity: IncidentSeverity | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        params: PaginationParams | None = None,
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

    def get_open_incidents(self, *, limit: int | None = None) -> list[VehicleIncident]:
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
        if limit is not None:
            stmt = stmt.limit(limit)
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

        fire_audit_event(
            self.db,
            self.organization_id,
            "fleet",
            "vehicle_incident",
            str(incident.incident_id),
            AuditAction.INSERT,
            new_values={
                "vehicle_id": str(data.vehicle_id),
                "incident_type": data.incident_type.value,
                "severity": data.severity.value,
            },
        )

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

        _STATE_MACHINE.validate(current, new_status)

        incident.status = new_status

        fire_audit_event(
            self.db,
            self.organization_id,
            "fleet",
            "vehicle_incident",
            str(incident.incident_id),
            AuditAction.UPDATE,
            old_values={"status": current.value},
            new_values={"status": new_status.value},
        )

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

        _STATE_MACHINE.validate(incident.status, IncidentStatus.RESOLVED)

        incident.status = IncidentStatus.RESOLVED
        incident.resolution_date = data.resolution_date or date.today()
        incident.resolution_notes = data.resolution_notes

        if data.actual_repair_cost is not None:
            incident.actual_repair_cost = data.actual_repair_cost

        if data.other_costs is not None:
            incident.other_costs = data.other_costs

        if data.insurance_payout is not None:
            incident.insurance_payout = data.insurance_payout

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=self.db,
                organization_id=self.organization_id,
                entity_type="FLEET_INCIDENT",
                entity_id=incident.incident_id,
                event="ON_STATUS_CHANGE",
                old_values={},
                new_values={"status": "RESOLVED"},
            )
        except Exception:
            logger.exception("Ignored exception")

        fire_audit_event(
            self.db,
            self.organization_id,
            "fleet",
            "vehicle_incident",
            str(incident.incident_id),
            AuditAction.UPDATE,
            new_values={
                "status": "RESOLVED",
                "resolution_date": str(incident.resolution_date),
            },
            reason="Incident resolved",
        )

        logger.info("Resolved incident %s", incident_id)
        return incident

    def close(self, incident_id: UUID) -> VehicleIncident:
        """Close an incident."""
        incident = self.get_or_raise(incident_id)

        _STATE_MACHINE.validate(incident.status, IncidentStatus.CLOSED)

        incident.status = IncidentStatus.CLOSED
        if not incident.resolution_date:
            incident.resolution_date = date.today()

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=self.db,
                organization_id=self.organization_id,
                entity_type="FLEET_INCIDENT",
                entity_id=incident.incident_id,
                event="ON_STATUS_CHANGE",
                old_values={},
                new_values={"status": "CLOSED"},
            )
        except Exception:
            logger.exception("Ignored exception")

        fire_audit_event(
            self.db,
            self.organization_id,
            "fleet",
            "vehicle_incident",
            str(incident.incident_id),
            AuditAction.UPDATE,
            new_values={"status": "CLOSED"},
            reason="Incident closed",
        )

        logger.info("Closed incident %s", incident_id)
        return incident

    def soft_delete(self, incident_id: UUID) -> VehicleIncident:
        """Soft delete an incident."""
        incident = self.get_or_raise(incident_id)
        incident.is_deleted = True
        incident.deleted_at = datetime.now(UTC)

        fire_audit_event(
            self.db,
            self.organization_id,
            "fleet",
            "vehicle_incident",
            str(incident.incident_id),
            AuditAction.DELETE,
            reason="Incident soft deleted",
        )

        logger.info("Soft deleted incident %s", incident_id)
        return incident

    def get_cost_summary(self, vehicle_id: UUID | None = None) -> dict:
        """Get incident cost summary using SQL aggregation."""
        base_filter = (
            VehicleIncident.organization_id == self.organization_id,
            VehicleIncident.is_deleted == False,  # noqa: E712
        )

        stmt = select(
            func.count(VehicleIncident.incident_id).label("total_incidents"),
            func.coalesce(
                func.sum(VehicleIncident.actual_repair_cost), Decimal(0)
            ).label("total_repair_cost"),
            func.coalesce(func.sum(VehicleIncident.other_costs), Decimal(0)).label(
                "total_other_costs"
            ),
            func.coalesce(func.sum(VehicleIncident.insurance_payout), Decimal(0)).label(
                "total_insurance_payout"
            ),
        ).where(*base_filter)

        if vehicle_id:
            stmt = stmt.where(VehicleIncident.vehicle_id == vehicle_id)

        row = self.db.execute(stmt).one()

        return {
            "total_incidents": row.total_incidents,
            "total_repair_cost": row.total_repair_cost,
            "total_other_costs": row.total_other_costs,
            "total_insurance_payout": row.total_insurance_payout,
            "net_cost": row.total_repair_cost
            + row.total_other_costs
            - row.total_insurance_payout,
        }
