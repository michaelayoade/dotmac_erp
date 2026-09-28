"""Application service for ERP vehicle-to-tracker mappings."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fleet.vehicle import Vehicle
from app.models.fleet.vehicle_tracker import VehicleTracker
from app.schemas.fleet.tracking import TrackerMappingCreate, TrackerMappingUpdate
from app.services.common import ConflictError, NotFoundError
from app.services.fleet.tracking.traccar_client import TraccarClient

logger = logging.getLogger(__name__)


class FleetTrackingService:
    """Own tenant-scoped mappings and the future Traccar synchronization seam."""

    def __init__(
        self,
        db: Session,
        organization_id: UUID,
        *,
        traccar_client: TraccarClient | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self._traccar_client = traccar_client

    def _get_vehicle(self, vehicle_id: UUID) -> Vehicle:
        vehicle = self.db.get(Vehicle, vehicle_id)
        if not vehicle or vehicle.organization_id != self.organization_id:
            raise NotFoundError(f"Vehicle {vehicle_id} not found")
        return vehicle

    def get_active_tracker(self, vehicle_id: UUID) -> VehicleTracker | None:
        """Return the active mapping for a tenant-owned vehicle."""
        self._get_vehicle(vehicle_id)
        stmt = select(VehicleTracker).where(
            VehicleTracker.organization_id == self.organization_id,
            VehicleTracker.vehicle_id == vehicle_id,
            VehicleTracker.is_active.is_(True),
        )
        return self.db.scalar(stmt)

    def get_active_tracker_or_raise(self, vehicle_id: UUID) -> VehicleTracker:
        tracker = self.get_active_tracker(vehicle_id)
        if not tracker:
            raise NotFoundError(f"Active tracker for vehicle {vehicle_id} not found")
        return tracker

    def _find_identifier_conflict(
        self,
        *,
        provider: str,
        unique_id: str,
        external_device_id: str | None,
        exclude_tracker_id: UUID | None = None,
    ) -> VehicleTracker | None:
        clauses = [
            VehicleTracker.organization_id == self.organization_id,
            VehicleTracker.provider == provider,
            VehicleTracker.is_active.is_(True),
        ]
        if exclude_tracker_id:
            clauses.append(VehicleTracker.tracker_id != exclude_tracker_id)

        tracker = self.db.scalar(
            select(VehicleTracker).where(
                *clauses,
                VehicleTracker.unique_id == unique_id,
            )
        )
        if tracker or not external_device_id:
            return tracker
        return self.db.scalar(
            select(VehicleTracker).where(
                *clauses,
                VehicleTracker.external_device_id == external_device_id,
            )
        )

    def create_mapping(
        self,
        vehicle_id: UUID,
        data: TrackerMappingCreate,
    ) -> VehicleTracker:
        """Assign one active tracker to a tenant-owned vehicle."""
        vehicle = self._get_vehicle(vehicle_id)
        if self.get_active_tracker(vehicle_id):
            raise ConflictError("Vehicle already has an active tracker")

        conflict = self._find_identifier_conflict(
            provider=data.provider,
            unique_id=data.unique_id,
            external_device_id=data.external_device_id,
        )
        if conflict:
            raise ConflictError("Tracker identifier is already assigned")

        tracker = VehicleTracker(
            organization_id=self.organization_id,
            vehicle_id=vehicle_id,
            **data.model_dump(),
        )
        self.db.add(tracker)

        # Known new mappings can safely populate empty legacy fields. Existing
        # non-empty values remain untouched because their historical semantics
        # are not established.
        vehicle.has_gps_tracker = True
        if not vehicle.gps_device_id:
            vehicle.gps_device_id = data.unique_id

        self.db.flush()
        logger.info("Assigned tracker to fleet vehicle %s", vehicle_id)
        return tracker

    def update_mapping(
        self,
        vehicle_id: UUID,
        data: TrackerMappingUpdate,
    ) -> VehicleTracker:
        """Update the active mapping without contacting Traccar."""
        vehicle = self._get_vehicle(vehicle_id)
        tracker = self.get_active_tracker_or_raise(vehicle_id)
        old_unique_id = tracker.unique_id
        changes = data.model_dump(exclude_unset=True)
        provider = changes.get("provider", tracker.provider)
        unique_id = changes.get("unique_id", tracker.unique_id)
        external_device_id = changes.get(
            "external_device_id", tracker.external_device_id
        )

        conflict = self._find_identifier_conflict(
            provider=provider,
            unique_id=unique_id,
            external_device_id=external_device_id,
            exclude_tracker_id=tracker.tracker_id,
        )
        if conflict:
            raise ConflictError("Tracker identifier is already assigned")

        for field, value in changes.items():
            setattr(tracker, field, value)

        if vehicle.gps_device_id in {None, old_unique_id}:
            vehicle.gps_device_id = tracker.unique_id
        vehicle.has_gps_tracker = True
        self.db.flush()
        logger.info("Updated tracker mapping for fleet vehicle %s", vehicle_id)
        return tracker

    def unlink_mapping(self, vehicle_id: UUID) -> VehicleTracker:
        """End the active assignment while retaining durable mapping history."""
        vehicle = self._get_vehicle(vehicle_id)
        tracker = self.get_active_tracker_or_raise(vehicle_id)
        tracker.is_active = False
        tracker.unassigned_at = datetime.now(timezone.utc)

        # A matching value was mirrored by this service and can be marked
        # inactive. A different legacy value is preserved as unresolved data.
        if vehicle.gps_device_id in {None, tracker.unique_id}:
            vehicle.has_gps_tracker = False

        self.db.flush()
        logger.info("Unlinked tracker from fleet vehicle %s", vehicle_id)
        return tracker
