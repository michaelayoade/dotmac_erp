from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import UUID
from uuid import uuid4

import pytest
from pydantic import ValidationError
from starlette.datastructures import FormData

from app.models.fleet.enums import IncidentSeverity, IncidentStatus, IncidentType
from app.schemas.fleet.incident import IncidentCreate
from app.services.fleet.web.fleet_web import FleetWebService


TEST_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000002")
TEST_EMPLOYEE_ID = UUID("00000000-0000-0000-0000-000000000003")


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


def _incident_payload() -> dict[str, object]:
    return {
        "vehicle_id": uuid4(),
        "reported_by_id": uuid4(),
        "incident_type": IncidentType.ACCIDENT,
        "severity": IncidentSeverity.MINOR,
        "incident_date": date(2026, 7, 10),
        "location": "Head office gate",
        "description": "Minor bumper damage",
    }


def test_incident_create_accepts_status():
    data = IncidentCreate(
        **_incident_payload(),
        status=IncidentStatus.INVESTIGATING,
    )

    assert data.status == IncidentStatus.INVESTIGATING


def test_incident_create_requires_location():
    payload = _incident_payload()
    payload.pop("location")

    with pytest.raises(ValidationError):
        IncidentCreate(**payload)


@pytest.mark.asyncio
async def test_incident_create_response_uses_resolved_employee_id(monkeypatch):
    db = _FakeDb()
    vehicle_id = uuid4()
    captured = {}

    class _IncidentService:
        def __init__(self, db_arg, org_id):
            captured["db"] = db_arg
            captured["org_id"] = org_id

        def create(self, schema):
            captured["schema"] = schema
            return SimpleNamespace(incident_id=uuid4())

    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.IncidentService", _IncidentService
    )
    monkeypatch.setattr(
        FleetWebService,
        "_resolve_active_employee_id_for_auth",
        lambda self, organization_id, auth: TEST_EMPLOYEE_ID,
    )

    response = await FleetWebService(db).create_entity_response(
        _FakeRequest(
            FormData(
                [
                    ("vehicle_id", str(vehicle_id)),
                    ("incident_type", "ACCIDENT"),
                    ("severity", "MINOR"),
                    ("incident_date", "2026-07-10"),
                    ("location", "Head office gate"),
                    ("description", "Minor bumper damage"),
                    ("status", "INVESTIGATING"),
                ]
            )
        ),
        SimpleNamespace(organization_id=TEST_ORG_ID, user_id=TEST_USER_ID),
        db,
        "incident",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/fleet/incidents"
    assert db.committed is True
    assert captured["org_id"] == TEST_ORG_ID
    assert captured["schema"].vehicle_id == vehicle_id
    assert captured["schema"].reported_by_id == TEST_EMPLOYEE_ID
    assert captured["schema"].status == IncidentStatus.INVESTIGATING
