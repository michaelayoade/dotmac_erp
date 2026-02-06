"""
Maintenance Service - Vehicle maintenance management.

Handles maintenance scheduling, tracking, and completion.
"""

import logging
from datetime import date
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.fleet.enums import MaintenanceStatus, MaintenanceType, VehicleStatus
from app.models.fleet.maintenance import MaintenanceRecord
from app.models.fleet.vehicle import Vehicle
from app.schemas.fleet.maintenance import (
    MaintenanceComplete,
    MaintenanceCreate,
    MaintenanceUpdate,
)
from app.services.common import (
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
    paginate,
)
from app.models.finance.audit.audit_log import AuditAction
from app.services.audit_dispatcher import fire_audit_event
from app.services.state_machine import StateMachine

logger = logging.getLogger(__name__)


# Valid maintenance status transitions
MAINTENANCE_STATUS_TRANSITIONS: Dict[MaintenanceStatus, set] = {
    MaintenanceStatus.SCHEDULED: {
        MaintenanceStatus.IN_PROGRESS,
        MaintenanceStatus.COMPLETED,
        MaintenanceStatus.CANCELLED,
    },
    MaintenanceStatus.IN_PROGRESS: {
        MaintenanceStatus.COMPLETED,
        MaintenanceStatus.CANCELLED,
    },
    MaintenanceStatus.COMPLETED: set(),  # Terminal
    MaintenanceStatus.CANCELLED: set(),  # Terminal
}
_STATE_MACHINE = StateMachine(MAINTENANCE_STATUS_TRANSITIONS)


class MaintenanceService:
    """Service for vehicle maintenance operations."""

    def __init__(self, db: Session, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    def get_by_id(self, maintenance_id: UUID) -> Optional[MaintenanceRecord]:
        """Get maintenance record by ID."""
        return self.db.get(MaintenanceRecord, maintenance_id)

    def get_or_raise(self, maintenance_id: UUID) -> MaintenanceRecord:
        """Get maintenance record or raise NotFoundError."""
        record = self.get_by_id(maintenance_id)
        if not record or record.organization_id != self.organization_id:
            raise NotFoundError(f"Maintenance record {maintenance_id} not found")
        return record

    def list_records(
        self,
        *,
        vehicle_id: Optional[UUID] = None,
        status: Optional[MaintenanceStatus] = None,
        maintenance_type: Optional[MaintenanceType] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        params: Optional[PaginationParams] = None,
    ) -> PaginatedResult[MaintenanceRecord]:
        """List maintenance records with filtering."""
        stmt = (
            select(MaintenanceRecord)
            .where(MaintenanceRecord.organization_id == self.organization_id)
            .options(selectinload(MaintenanceRecord.vehicle))
            .order_by(MaintenanceRecord.scheduled_date.desc())
        )

        if vehicle_id:
            stmt = stmt.where(MaintenanceRecord.vehicle_id == vehicle_id)

        if status:
            stmt = stmt.where(MaintenanceRecord.status == status)

        if maintenance_type:
            stmt = stmt.where(MaintenanceRecord.maintenance_type == maintenance_type)

        if from_date:
            stmt = stmt.where(MaintenanceRecord.scheduled_date >= from_date)

        if to_date:
            stmt = stmt.where(MaintenanceRecord.scheduled_date <= to_date)

        return paginate(self.db, stmt, params)

    def get_due_maintenance(
        self, days_ahead: int = 7, *, limit: Optional[int] = None
    ) -> List[MaintenanceRecord]:
        """Get maintenance records due within the specified days."""
        from datetime import timedelta

        cutoff = date.today() + timedelta(days=days_ahead)
        stmt = (
            select(MaintenanceRecord)
            .where(
                MaintenanceRecord.organization_id == self.organization_id,
                MaintenanceRecord.status == MaintenanceStatus.SCHEDULED,
                MaintenanceRecord.scheduled_date <= cutoff,
            )
            .options(selectinload(MaintenanceRecord.vehicle))
            .order_by(MaintenanceRecord.scheduled_date.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def get_overdue_maintenance(
        self, *, limit: Optional[int] = None
    ) -> List[MaintenanceRecord]:
        """Get overdue maintenance records."""
        stmt = (
            select(MaintenanceRecord)
            .where(
                MaintenanceRecord.organization_id == self.organization_id,
                MaintenanceRecord.status == MaintenanceStatus.SCHEDULED,
                MaintenanceRecord.scheduled_date < date.today(),
            )
            .options(selectinload(MaintenanceRecord.vehicle))
            .order_by(MaintenanceRecord.scheduled_date.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def create(self, data: MaintenanceCreate) -> MaintenanceRecord:
        """Create a new maintenance record."""
        # Verify vehicle exists and belongs to org
        vehicle = self.db.get(Vehicle, data.vehicle_id)
        if not vehicle or vehicle.organization_id != self.organization_id:
            raise NotFoundError(f"Vehicle {data.vehicle_id} not found")

        record = MaintenanceRecord(
            organization_id=self.organization_id,
            **data.model_dump(),
        )

        self.db.add(record)
        self.db.flush()

        fire_audit_event(
            self.db,
            self.organization_id,
            "fleet",
            "maintenance_record",
            str(record.maintenance_id),
            AuditAction.INSERT,
            new_values={
                "vehicle_id": str(data.vehicle_id),
                "maintenance_type": data.maintenance_type.value
                if hasattr(data.maintenance_type, "value")
                else str(data.maintenance_type),
                "description": data.description,
                "scheduled_date": str(data.scheduled_date),
            },
        )

        logger.info(
            "Created maintenance record for vehicle %s: %s",
            vehicle.vehicle_code,
            data.description,
        )
        return record

    def update(
        self, maintenance_id: UUID, data: MaintenanceUpdate
    ) -> MaintenanceRecord:
        """Update a maintenance record."""
        record = self.get_or_raise(maintenance_id)

        if record.status in (MaintenanceStatus.COMPLETED, MaintenanceStatus.CANCELLED):
            raise ValidationError("Cannot update completed or cancelled maintenance")

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(record, field, value)

        logger.info("Updated maintenance record %s", maintenance_id)
        return record

    def start(self, maintenance_id: UUID) -> MaintenanceRecord:
        """Mark maintenance as in progress and update vehicle status."""
        record = self.get_or_raise(maintenance_id)

        _STATE_MACHINE.validate(record.status, MaintenanceStatus.IN_PROGRESS)

        record.status = MaintenanceStatus.IN_PROGRESS

        fire_audit_event(
            self.db,
            self.organization_id,
            "fleet",
            "maintenance_record",
            str(record.maintenance_id),
            AuditAction.UPDATE,
            old_values={"status": "SCHEDULED"},
            new_values={"status": "IN_PROGRESS"},
        )

        # Update vehicle status
        vehicle = self.db.get(Vehicle, record.vehicle_id)
        if vehicle and vehicle.status == VehicleStatus.ACTIVE:
            vehicle.status = VehicleStatus.MAINTENANCE

        logger.info("Started maintenance %s", maintenance_id)
        return record

    def complete(
        self,
        maintenance_id: UUID,
        data: MaintenanceComplete,
    ) -> MaintenanceRecord:
        """Complete a maintenance record."""
        record = self.get_or_raise(maintenance_id)

        _STATE_MACHINE.validate(record.status, MaintenanceStatus.COMPLETED)

        record.status = MaintenanceStatus.COMPLETED
        record.completed_date = data.completed_date or date.today()
        record.actual_cost = data.actual_cost
        record.odometer_at_service = data.odometer_at_service
        record.work_performed = data.work_performed
        record.parts_replaced = data.parts_replaced
        record.technician_name = data.technician_name
        record.invoice_number = data.invoice_number
        record.next_service_odometer = data.next_service_odometer
        record.next_service_date = data.next_service_date

        # Update vehicle odometer if provided
        vehicle = self.db.get(Vehicle, record.vehicle_id)
        if vehicle and data.odometer_at_service:
            if data.odometer_at_service > vehicle.current_odometer:
                vehicle.current_odometer = data.odometer_at_service
                vehicle.last_odometer_date = record.completed_date

        # Return vehicle to active status if it was in maintenance
        if vehicle and vehicle.status == VehicleStatus.MAINTENANCE:
            vehicle.status = VehicleStatus.ACTIVE

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=self.db,
                organization_id=self.organization_id,
                entity_type="FLEET_MAINTENANCE",
                entity_id=record.maintenance_id,
                event="ON_STATUS_CHANGE",
                old_values={},
                new_values={"status": "COMPLETED"},
            )
        except Exception:
            pass

        fire_audit_event(
            self.db,
            self.organization_id,
            "fleet",
            "maintenance_record",
            str(record.maintenance_id),
            AuditAction.UPDATE,
            new_values={
                "status": "COMPLETED",
                "completed_date": str(record.completed_date),
                "actual_cost": str(data.actual_cost) if data.actual_cost else None,
            },
            reason="Maintenance completed",
        )

        logger.info("Completed maintenance %s", maintenance_id)
        return record

    def cancel(
        self, maintenance_id: UUID, reason: Optional[str] = None
    ) -> MaintenanceRecord:
        """Cancel a maintenance record."""
        record = self.get_or_raise(maintenance_id)

        _STATE_MACHINE.validate(record.status, MaintenanceStatus.CANCELLED)

        record.status = MaintenanceStatus.CANCELLED
        if reason:
            record.notes = f"{record.notes or ''}\nCancelled: {reason}".strip()

        # Return vehicle to active if it was in maintenance
        vehicle = self.db.get(Vehicle, record.vehicle_id)
        if vehicle and vehicle.status == VehicleStatus.MAINTENANCE:
            vehicle.status = VehicleStatus.ACTIVE

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=self.db,
                organization_id=self.organization_id,
                entity_type="FLEET_MAINTENANCE",
                entity_id=record.maintenance_id,
                event="ON_STATUS_CHANGE",
                old_values={},
                new_values={"status": "CANCELLED"},
            )
        except Exception:
            pass

        fire_audit_event(
            self.db,
            self.organization_id,
            "fleet",
            "maintenance_record",
            str(record.maintenance_id),
            AuditAction.UPDATE,
            new_values={"status": "CANCELLED"},
            reason=reason,
        )

        logger.info("Cancelled maintenance %s: %s", maintenance_id, reason)
        return record
