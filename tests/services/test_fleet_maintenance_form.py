from __future__ import annotations

from datetime import date
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from starlette.datastructures import FormData, UploadFile

from app.models.fleet.enums import MaintenanceStatus, MaintenanceType
from app.schemas.fleet.maintenance import MaintenanceCreate
from app.services.fleet.web.fleet_web import FleetWebService


TEST_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000002")


class _FakeDb:
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _FakeRequest:
    def __init__(self, form_data):
        self._form_data = form_data

    async def form(self):
        return self._form_data


def test_maintenance_create_accepts_technician_name():
    data = MaintenanceCreate(
        vehicle_id=uuid4(),
        maintenance_type=MaintenanceType.PREVENTIVE,
        description="Routine service",
        scheduled_date=date(2026, 7, 10),
        technician_name="Mr IDOWU (Idowu Orile Enterprise)",
    )

    assert data.technician_name == "Mr IDOWU (Idowu Orile Enterprise)"


def test_maintenance_create_accepts_status():
    data = MaintenanceCreate(
        vehicle_id=uuid4(),
        maintenance_type=MaintenanceType.CORRECTIVE,
        description="Repair brake pads",
        scheduled_date=date(2026, 7, 10),
        status=MaintenanceStatus.IN_PROGRESS,
    )

    assert data.status == MaintenanceStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_maintenance_create_response_saves_uploaded_attachments(monkeypatch):
    db = _FakeDb()
    vehicle_id = uuid4()
    maintenance_id = uuid4()
    captured = {}
    upload = UploadFile(
        BytesIO(b"%PDF maintenance estimate"),
        filename="estimate.pdf",
        headers={"content-type": "application/pdf"},
    )

    class _MaintenanceService:
        def __init__(self, db_arg, org_id):
            captured["db"] = db_arg
            captured["org_id"] = org_id

        def create(self, schema):
            captured["schema"] = schema
            return SimpleNamespace(maintenance_id=maintenance_id)

    async def _fake_save_attachment(self, **kwargs):
        captured["attachment_kwargs"] = kwargs

    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.MaintenanceService", _MaintenanceService
    )
    monkeypatch.setattr(
        FleetWebService,
        "_save_maintenance_attachment",
        _fake_save_attachment,
    )

    response = await FleetWebService(db).create_entity_response(
        _FakeRequest(
            FormData(
                [
                    ("vehicle_id", str(vehicle_id)),
                    ("maintenance_type", "CORRECTIVE"),
                    ("status", "SCHEDULED"),
                    ("description", "Replace damaged tyre"),
                    ("scheduled_date", "2026-07-10"),
                    ("maintenance_files", upload),
                ]
            )
        ),
        SimpleNamespace(organization_id=TEST_ORG_ID, user_id=TEST_USER_ID),
        db,
        "maintenance",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/fleet/maintenance"
    assert db.committed is True
    assert captured["org_id"] == TEST_ORG_ID
    assert captured["schema"].vehicle_id == vehicle_id
    assert captured["schema"].status == MaintenanceStatus.SCHEDULED
    assert captured["attachment_kwargs"]["organization_id"] == TEST_ORG_ID
    assert captured["attachment_kwargs"]["maintenance_id"] == maintenance_id
    assert captured["attachment_kwargs"]["upload"] is upload
    assert captured["attachment_kwargs"]["uploaded_by"] == TEST_USER_ID
