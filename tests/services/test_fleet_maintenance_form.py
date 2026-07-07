from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.models.fleet.enums import MaintenanceType
from app.schemas.fleet.maintenance import MaintenanceCreate


def test_maintenance_create_accepts_technician_name():
    data = MaintenanceCreate(
        vehicle_id=uuid4(),
        maintenance_type=MaintenanceType.PREVENTIVE,
        description="Routine service",
        scheduled_date=date(2026, 7, 10),
        technician_name="Mr IDOWU (Idowu Orile Enterprise)",
    )

    assert data.technician_name == "Mr IDOWU (Idowu Orile Enterprise)"
