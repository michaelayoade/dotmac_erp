from uuid import uuid4

import pytest

from app.models.fleet.enums import AssignmentType, FuelType, OwnershipType, VehicleType
from app.schemas.fleet.vehicle import VehicleUpdate
from app.services.fleet.web.fleet_web import FleetWebService


def test_vehicle_update_schema_accepts_visible_edit_form_fields():
    data = VehicleUpdate(
        registration_number="ABC-123",
        make="Toyota",
        model="Hilux",
        year=2024,
        vehicle_type=VehicleType.PICKUP,
        fuel_type=FuelType.DIESEL,
        ownership_type=OwnershipType.OWNED,
        current_odometer=12345,
    )

    assert data.make == "Toyota"
    assert data.model == "Hilux"
    assert data.year == 2024
    assert data.current_odometer == 12345


@pytest.mark.asyncio
async def test_vehicle_edit_redirects_back_to_form_with_success(monkeypatch):
    import app.services.fleet.vehicle_service as vehicle_service_module

    org_id = uuid4()
    vehicle_id = uuid4()
    captured: dict[str, object] = {}

    class FakeRequest:
        async def form(self):
            return {
                "registration_number": "ABC-123",
                "make": "Toyota",
                "model": "Hilux",
                "year": "2024",
                "vehicle_type": VehicleType.PICKUP.value,
                "fuel_type": FuelType.DIESEL.value,
                "ownership_type": OwnershipType.OWNED.value,
                "color": "White",
                "seating_capacity": "5",
                "current_odometer_km": "12345",
                "is_pool_vehicle": "true",
                "notes": "Updated vehicle",
            }

    class FakeDb:
        def commit(self):
            captured["committed"] = True

    class FakeVehicleService:
        def __init__(self, db, organization_id):
            captured["organization_id"] = organization_id

        def update(self, target_vehicle_id, data):
            captured["vehicle_id"] = target_vehicle_id
            captured["data"] = data

    monkeypatch.setattr(
        vehicle_service_module,
        "VehicleService",
        FakeVehicleService,
    )

    response = await FleetWebService(FakeDb()).update_vehicle_response(
        FakeRequest(),
        org_id,
        vehicle_id,
        FakeDb(),
    )

    data = captured["data"]
    assert isinstance(data, VehicleUpdate)
    assert data.make == "Toyota"
    assert data.model == "Hilux"
    assert data.year == 2024
    assert data.current_odometer == 12345
    assert data.assignment_type == AssignmentType.POOL
    assert captured["committed"] is True
    assert response.status_code == 303
    assert (
        response.headers["location"]
        == f"/fleet/vehicles/{vehicle_id}/edit?success=Vehicle%20saved."
    )
