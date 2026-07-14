from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from starlette.datastructures import FormData, UploadFile

from app.models.finance.common.attachment import AttachmentCategory
from app.services.fleet.web.fleet_web import FleetWebService


TEST_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000002")


class _FakeDb:
    def __init__(self):
        self.added = []
        self.flushed = False
        self.committed = False
        self.rolled_back = False

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flushed = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _FakeFuelReceiptUpload:
    config = SimpleNamespace(max_size_bytes=20 * 1024 * 1024)

    def save(self, file_data, content_type=None, subdirs=None, original_filename=None):
        return SimpleNamespace(
            relative_path="/".join([*(subdirs or []), "receipt_123.jpg"]),
            file_size=len(file_data),
            checksum="abc123",
        )


class _FakeIncidentAttachmentUpload:
    config = SimpleNamespace(max_size_bytes=20 * 1024 * 1024)

    def save(self, file_data, content_type=None, subdirs=None, original_filename=None):
        return SimpleNamespace(
            relative_path="/".join([*(subdirs or []), "incident_123.jpg"]),
            file_size=len(file_data),
            checksum="def456",
        )


class _FakeMaintenanceAttachmentUpload:
    config = SimpleNamespace(max_size_bytes=20 * 1024 * 1024)

    def save(self, file_data, content_type=None, subdirs=None, original_filename=None):
        return SimpleNamespace(
            relative_path="/".join([*(subdirs or []), "maintenance_123.pdf"]),
            file_size=len(file_data),
            checksum="maint123",
        )


class _FakeRequest:
    def __init__(self, form_data):
        self._form_data = form_data

    async def form(self):
        return self._form_data


class _FakeDocumentUpload:
    config = SimpleNamespace(max_size_bytes=10 * 1024 * 1024)

    def save(self, file_data, content_type=None, subdirs=None, original_filename=None):
        return SimpleNamespace(
            relative_path="/".join([*(subdirs or []), "document_123.pdf"]),
            file_size=len(file_data),
            checksum="ghi789",
        )


@pytest.mark.asyncio
async def test_save_fuel_receipt_attachment_creates_common_attachment(monkeypatch):
    db = _FakeDb()
    fuel_log_id = uuid4()
    upload = UploadFile(
        BytesIO(b"\xff\xd8\xffphone receipt"),
        filename="receipt.jpg",
        headers={"content-type": "image/jpeg"},
    )
    monkeypatch.setattr(
        "app.services.file_upload.get_fleet_fuel_receipt_upload",
        lambda: _FakeFuelReceiptUpload(),
    )

    await FleetWebService(db)._save_fuel_receipt_attachment(
        db=db,
        organization_id=TEST_ORG_ID,
        fuel_log_id=fuel_log_id,
        upload=upload,
        uploaded_by=TEST_USER_ID,
    )

    assert db.flushed is True
    assert len(db.added) == 1
    attachment = db.added[0]
    assert attachment.organization_id == TEST_ORG_ID
    assert attachment.entity_type == FleetWebService.FUEL_LOG_ATTACHMENT_ENTITY_TYPE
    assert attachment.entity_id == fuel_log_id
    assert attachment.file_name == "receipt.jpg"
    assert attachment.file_size == len(b"\xff\xd8\xffphone receipt")
    assert attachment.content_type == "image/jpeg"
    assert attachment.category == AttachmentCategory.RECEIPT
    assert attachment.uploaded_by == TEST_USER_ID


@pytest.mark.asyncio
async def test_save_incident_attachment_creates_common_attachment(monkeypatch):
    db = _FakeDb()
    incident_id = uuid4()
    upload = UploadFile(
        BytesIO(b"\xff\xd8\xffincident photo"),
        filename="damage.jpg",
        headers={"content-type": "image/jpeg"},
    )
    monkeypatch.setattr(
        "app.services.file_upload.get_fleet_incident_attachment_upload",
        lambda: _FakeIncidentAttachmentUpload(),
    )

    await FleetWebService(db)._save_incident_attachment(
        db=db,
        organization_id=TEST_ORG_ID,
        incident_id=incident_id,
        upload=upload,
        uploaded_by=TEST_USER_ID,
    )

    assert db.flushed is True
    assert len(db.added) == 1
    attachment = db.added[0]
    assert attachment.organization_id == TEST_ORG_ID
    assert attachment.entity_type == FleetWebService.INCIDENT_ATTACHMENT_ENTITY_TYPE
    assert attachment.entity_id == incident_id
    assert attachment.file_name == "damage.jpg"
    assert attachment.file_size == len(b"\xff\xd8\xffincident photo")
    assert attachment.content_type == "image/jpeg"
    assert attachment.category == AttachmentCategory.OTHER
    assert attachment.uploaded_by == TEST_USER_ID


@pytest.mark.asyncio
async def test_save_maintenance_attachment_creates_common_attachment(monkeypatch):
    db = _FakeDb()
    maintenance_id = uuid4()
    upload = UploadFile(
        BytesIO(b"%PDF maintenance estimate"),
        filename="estimate.pdf",
        headers={"content-type": "application/pdf"},
    )
    monkeypatch.setattr(
        "app.services.file_upload.get_fleet_maintenance_attachment_upload",
        lambda: _FakeMaintenanceAttachmentUpload(),
    )

    await FleetWebService(db)._save_maintenance_attachment(
        db=db,
        organization_id=TEST_ORG_ID,
        maintenance_id=maintenance_id,
        upload=upload,
        uploaded_by=TEST_USER_ID,
    )

    assert db.flushed is True
    assert len(db.added) == 1
    attachment = db.added[0]
    assert attachment.organization_id == TEST_ORG_ID
    assert attachment.entity_type == FleetWebService.MAINTENANCE_ATTACHMENT_ENTITY_TYPE
    assert attachment.entity_id == maintenance_id
    assert attachment.file_name == "estimate.pdf"
    assert attachment.file_size == len(b"%PDF maintenance estimate")
    assert attachment.content_type == "application/pdf"
    assert attachment.category == AttachmentCategory.OTHER
    assert attachment.uploaded_by == TEST_USER_ID


@pytest.mark.asyncio
async def test_update_entity_response_updates_fuel_log(monkeypatch):
    db = _FakeDb()
    fuel_log_id = uuid4()
    vehicle_id = uuid4()
    captured = {}

    class _FuelService:
        def __init__(self, db_arg, org_id):
            captured["db"] = db_arg
            captured["org_id"] = org_id

        def update(self, record_id, schema):
            captured["record_id"] = record_id
            captured["schema"] = schema

    monkeypatch.setattr("app.services.fleet.web.fleet_web.FuelService", _FuelService)

    response = await FleetWebService(db).update_entity_response(
        _FakeRequest(
            FormData(
                [
                    ("vehicle_id", str(vehicle_id)),
                    ("log_date", "2026-07-07"),
                    ("fuel_type", "DIESEL"),
                    ("quantity_liters", "40.5"),
                    ("price_per_liter", "900"),
                    ("total_cost", "36450"),
                    ("odometer_reading", "12345"),
                    ("is_full_tank", "false"),
                    ("is_full_tank", "true"),
                    ("station_name", "Updated Station"),
                    ("expense_claim_name", "ignored display text"),
                    ("expense_claim_id", ""),
                    ("notes", "Corrected entry"),
                ]
            )
        ),
        SimpleNamespace(organization_id=TEST_ORG_ID, user_id=TEST_USER_ID),
        db,
        "fuel",
        fuel_log_id,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/fleet/fuel"
    assert db.committed is True
    assert captured["record_id"] == fuel_log_id
    assert captured["schema"].vehicle_id == vehicle_id
    assert captured["schema"].is_full_tank is True
    assert captured["schema"].station_name == "Updated Station"
    assert captured["schema"].notes == "Corrected entry"


@pytest.mark.asyncio
async def test_update_entity_response_updates_incident(monkeypatch):
    db = _FakeDb()
    incident_id = uuid4()
    vehicle_id = uuid4()
    expense_claim_id = uuid4()
    captured = {}

    class _IncidentService:
        def __init__(self, db_arg, org_id):
            captured["db"] = db_arg
            captured["org_id"] = org_id

        def update(self, record_id, schema):
            captured["record_id"] = record_id
            captured["schema"] = schema

    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.IncidentService", _IncidentService
    )

    response = await FleetWebService(db).update_entity_response(
        _FakeRequest(
            FormData(
                [
                    ("vehicle_id", str(vehicle_id)),
                    ("incident_type", "ACCIDENT"),
                    ("severity", "MODERATE"),
                    ("incident_date", "2026-07-07"),
                    ("description", "Corrected description"),
                    ("third_party_involved", "false"),
                    ("estimated_repair_cost", "250000"),
                    ("expense_claim_name", "ignored display text"),
                    ("expense_claim_id", str(expense_claim_id)),
                    ("notes", "Updated notes"),
                ]
            )
        ),
        SimpleNamespace(organization_id=TEST_ORG_ID, user_id=TEST_USER_ID),
        db,
        "incident",
        incident_id,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/fleet/incidents/{incident_id}"
    assert db.committed is True
    assert captured["record_id"] == incident_id
    assert captured["schema"].vehicle_id == vehicle_id
    assert captured["schema"].third_party_involved is False
    assert captured["schema"].expense_claim_id == expense_claim_id
    assert captured["schema"].description == "Corrected description"


@pytest.mark.asyncio
async def test_save_document_file_updates_vehicle_document(monkeypatch):
    db = _FakeDb()
    document = SimpleNamespace(file_name=None, file_path=None)
    upload = UploadFile(
        BytesIO(b"%PDF fleet document"),
        filename="registration.pdf",
        headers={"content-type": "application/pdf"},
    )
    monkeypatch.setattr(
        "app.services.file_upload.get_finance_attachment_upload",
        lambda: _FakeDocumentUpload(),
    )

    await FleetWebService(db)._save_document_file(
        organization_id=TEST_ORG_ID,
        document=document,
        upload=upload,
    )

    assert db.flushed is True
    assert document.file_name == "registration.pdf"
    assert document.file_path == (f"{TEST_ORG_ID}/fleet_documents/document_123.pdf")
