from collections import deque
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.fleet.vehicle_tracker import VehicleTracker
from app.schemas.fleet.tracking import TrackerMappingCreate, TrackerMappingUpdate
from app.services.common import ConflictError, NotFoundError
from app.services.fleet.tracking.traccar_service import FleetTrackingService


class FakeSession:
    def __init__(self, vehicle, scalar_results=()):
        self.vehicle = vehicle
        self.scalar_results = deque(scalar_results)
        self.added = []
        self.flush_count = 0

    def get(self, model, record_id):
        if record_id == self.vehicle.vehicle_id:
            return self.vehicle
        return None

    def scalar(self, statement):
        assert "vehicle_tracker" in str(statement)
        return self.scalar_results.popleft() if self.scalar_results else None

    def add(self, record):
        self.added.append(record)

    def flush(self):
        self.flush_count += 1
        for record in self.added:
            if isinstance(record, VehicleTracker):
                record.tracker_id = record.tracker_id or uuid4()
                record.assigned_at = record.assigned_at or datetime.now(UTC)
                record.created_at = record.created_at or datetime.now(UTC)


def _vehicle(organization_id, *, gps_device_id=None):
    return SimpleNamespace(
        vehicle_id=uuid4(),
        organization_id=organization_id,
        has_gps_tracker=bool(gps_device_id),
        gps_device_id=gps_device_id,
    )


def _tracker(organization_id, vehicle_id, *, unique_id="123456789012345"):
    now = datetime.now(UTC)
    return VehicleTracker(
        tracker_id=uuid4(),
        organization_id=organization_id,
        vehicle_id=vehicle_id,
        provider="TRACCAR",
        unique_id=unique_id,
        external_device_id="42",
        tracker_manufacturer="Coban",
        tracker_model="GPS303F",
        protocol="gt06",
        is_active=True,
        assigned_at=now,
        created_at=now,
    )


def test_tracker_creation_sets_mapping_and_safe_legacy_compatibility():
    org_id = uuid4()
    vehicle = _vehicle(org_id)
    db = FakeSession(vehicle, [None, None, None])
    service = FleetTrackingService(db, org_id)

    tracker = service.create_mapping(
        vehicle.vehicle_id,
        TrackerMappingCreate(
            unique_id=" 123456789012345 ",
            external_device_id="42",
            tracker_manufacturer="Coban",
            tracker_model="GPS303F",
            protocol="gt06",
        ),
    )

    assert tracker.unique_id == "123456789012345"
    assert tracker.external_device_id == "42"
    assert vehicle.has_gps_tracker is True
    assert vehicle.gps_device_id == tracker.unique_id
    assert db.flush_count == 1


def test_tracker_creation_preserves_ambiguous_legacy_device_id():
    org_id = uuid4()
    vehicle = _vehicle(org_id, gps_device_id="legacy-unclassified-value")
    db = FakeSession(vehicle, [None, None])

    FleetTrackingService(db, org_id).create_mapping(
        vehicle.vehicle_id,
        TrackerMappingCreate(unique_id="123456789012345"),
    )

    assert vehicle.gps_device_id == "legacy-unclassified-value"
    assert vehicle.has_gps_tracker is True


def test_tracker_update_changes_mapping_and_known_legacy_mirror():
    org_id = uuid4()
    vehicle = _vehicle(org_id, gps_device_id="111111111111111")
    tracker = _tracker(org_id, vehicle.vehicle_id, unique_id=vehicle.gps_device_id)
    db = FakeSession(vehicle, [tracker, None, None])

    updated = FleetTrackingService(db, org_id).update_mapping(
        vehicle.vehicle_id,
        TrackerMappingUpdate(
            unique_id="222222222222222",
            external_device_id="84",
            tracker_model="GPS305",
        ),
    )

    assert updated.unique_id == "222222222222222"
    assert updated.external_device_id == "84"
    assert vehicle.gps_device_id == updated.unique_id
    assert db.flush_count == 1


def test_tracker_unlink_retains_history_and_ends_active_assignment():
    org_id = uuid4()
    vehicle = _vehicle(org_id, gps_device_id="123456789012345")
    tracker = _tracker(org_id, vehicle.vehicle_id)
    db = FakeSession(vehicle, [tracker])

    unlinked = FleetTrackingService(db, org_id).unlink_mapping(vehicle.vehicle_id)

    assert unlinked.is_active is False
    assert unlinked.unassigned_at is not None
    assert vehicle.has_gps_tracker is False
    assert vehicle.gps_device_id == "123456789012345"


def test_duplicate_imei_is_rejected():
    org_id = uuid4()
    vehicle = _vehicle(org_id)
    duplicate = _tracker(org_id, uuid4())
    db = FakeSession(vehicle, [None, duplicate])

    with pytest.raises(ConflictError, match="already assigned"):
        FleetTrackingService(db, org_id).create_mapping(
            vehicle.vehicle_id,
            TrackerMappingCreate(unique_id=duplicate.unique_id),
        )


def test_second_active_tracker_for_vehicle_is_rejected():
    org_id = uuid4()
    vehicle = _vehicle(org_id)
    db = FakeSession(vehicle, [_tracker(org_id, vehicle.vehicle_id)])

    with pytest.raises(ConflictError, match="already has an active tracker"):
        FleetTrackingService(db, org_id).create_mapping(
            vehicle.vehicle_id,
            TrackerMappingCreate(unique_id="999999999999999"),
        )


def test_vehicle_from_another_organization_is_not_visible():
    requested_org = uuid4()
    vehicle = _vehicle(uuid4())
    db = FakeSession(vehicle)

    with pytest.raises(NotFoundError):
        FleetTrackingService(db, requested_org).create_mapping(
            vehicle.vehicle_id,
            TrackerMappingCreate(unique_id="123456789012345"),
        )
