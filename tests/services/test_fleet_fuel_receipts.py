from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from starlette.datastructures import UploadFile

from app.models.finance.common.attachment import AttachmentCategory
from app.services.fleet.web.fleet_web import FleetWebService


TEST_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000002")


class _FakeDb:
    def __init__(self):
        self.added = []
        self.flushed = False

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flushed = True


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
