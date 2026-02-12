from __future__ import annotations

from uuid import uuid4

from app.models.fleet.vehicle import Vehicle
from app.services.fleet.import_export import VehicleImporter


def test_vehicle_importer_create_entity(import_config, mock_db):
    importer = VehicleImporter(mock_db, import_config)
    row = {
        "vehicle_code": "FLT-001",
        "registration_number": "ABC-123",
        "make": "Toyota",
        "model": "Camry",
        "year": 2020,
    }

    vehicle = importer.create_entity(row)

    assert vehicle.vehicle_code == "FLT-001"
    assert vehicle.registration_number == "ABC-123"
    assert vehicle.make == "Toyota"
    assert vehicle.model == "Camry"
    assert vehicle.year == 2020


def test_vehicle_importer_duplicate_by_code(import_config, mock_db):
    existing = Vehicle(
        vehicle_id=uuid4(),
        organization_id=import_config.organization_id,
        vehicle_code="FLT-001",
        registration_number="ABC-123",
        make="Toyota",
        model="Camry",
        year=2020,
        seating_capacity=5,
        current_odometer=0,
    )
    mock_db.scalar.return_value = existing

    importer = VehicleImporter(mock_db, import_config)
    result = importer.check_duplicate({"Vehicle Code": "FLT-001"})

    assert result == existing
