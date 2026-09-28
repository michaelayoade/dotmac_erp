from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4
import sys
from types import ModuleType

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# The focused API test does not exercise the separately packaged kernel cache;
# provide only the tenant-scope value object required while importing API deps.
kernel_cache = ModuleType("dotmac_kernel.cache")
kernel_cache.TenantScope = lambda tenant_id: tenant_id
sys.modules.setdefault("dotmac_kernel", ModuleType("dotmac_kernel"))
sys.modules.setdefault("dotmac_kernel.cache", kernel_cache)

from app.api.fleet import tracking


ORG_ID = uuid4()
VEHICLE_ID = uuid4()


def _tracker():
    now = datetime.now(UTC)
    return SimpleNamespace(
        tracker_id=uuid4(),
        organization_id=ORG_ID,
        vehicle_id=VEHICLE_ID,
        provider="TRACCAR",
        unique_id="123456789012345",
        external_device_id="42",
        tracker_manufacturer="Coban",
        tracker_model="GPS303F",
        protocol="gt06",
        is_active=True,
        assigned_at=now,
        unassigned_at=None,
        created_at=now,
        updated_at=None,
    )


class FakeTrackingService:
    def __init__(self, db, organization_id):
        assert organization_id == ORG_ID

    def get_active_tracker_or_raise(self, vehicle_id):
        assert vehicle_id == VEHICLE_ID
        return _tracker()

    def create_mapping(self, vehicle_id, data):
        assert vehicle_id == VEHICLE_ID
        assert data.unique_id == "123456789012345"
        return _tracker()

    def update_mapping(self, vehicle_id, data):
        assert vehicle_id == VEHICLE_ID
        return _tracker()

    def unlink_mapping(self, vehicle_id):
        assert vehicle_id == VEHICLE_ID
        return _tracker()


def _client(monkeypatch, *, read_allowed=True, manage_allowed=True):
    api = FastAPI()
    api.include_router(tracking.router, prefix="/api/v1/fleet")
    api.dependency_overrides[tracking.require_organization_id] = lambda: ORG_ID
    api.dependency_overrides[tracking.get_db_with_org] = lambda: object()

    def require_read():
        if not read_allowed:
            raise HTTPException(status_code=403, detail="tracking read required")

    def require_manage():
        if not manage_allowed:
            raise HTTPException(status_code=403, detail="tracking manage required")

    api.dependency_overrides[tracking.require_tracking_read] = require_read
    api.dependency_overrides[tracking.require_tracking_manage] = require_manage
    monkeypatch.setattr(tracking, "FleetTrackingService", FakeTrackingService)
    return TestClient(api)


def test_read_permission_is_required(monkeypatch):
    client = _client(monkeypatch, read_allowed=False)
    response = client.get(f"/api/v1/fleet/tracking/vehicles/{VEHICLE_ID}/tracker")
    assert response.status_code == 403


def test_manage_permission_is_required(monkeypatch):
    client = _client(monkeypatch, manage_allowed=False)
    response = client.post(
        f"/api/v1/fleet/tracking/vehicles/{VEHICLE_ID}/tracker",
        json={"unique_id": "123456789012345"},
    )
    assert response.status_code == 403


def test_tracker_mapping_response_never_exposes_traccar_credentials(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(f"/api/v1/fleet/tracking/vehicles/{VEHICLE_ID}/tracker")

    assert response.status_code == 200
    body = response.json()
    assert body["unique_id"] == "123456789012345"
    assert body["connection_status"] == "NOT_CONNECTED"
    assert {"password", "username", "base_url"}.isdisjoint(body)


def test_mapping_mutations_use_manage_permission(monkeypatch):
    client = _client(monkeypatch)
    base = f"/api/v1/fleet/tracking/vehicles/{VEHICLE_ID}/tracker"

    assert client.post(base, json={"unique_id": "123456789012345"}).status_code == 201
    assert client.patch(base, json={"tracker_model": "GPS305"}).status_code == 200
    assert client.delete(base).status_code == 204
