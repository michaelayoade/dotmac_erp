from __future__ import annotations

import inspect
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from starlette.datastructures import Headers, UploadFile

from app.models.people.hr import DocumentType
from app.services.file_upload import (
    InvalidExtensionError,
    UploadResult,
    get_employee_document_upload,
)
from app.services.people.hr.employee_extended import (
    EmployeeDocumentService,
    EmployeeExtendedDataError,
)
from app.web.deps import require_hr_access
from app.web.people.hr import employee_extended as employee_document_routes


def test_upload_document_stores_generated_path_and_file_metadata(monkeypatch):
    organization_id = uuid4()
    employee_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(employee_id=employee_id)

    upload_service = MagicMock()
    upload_service.save.return_value = UploadResult(
        s3_key=f"employee_documents/{organization_id}/{employee_id}/generated.pdf",
        relative_path=f"{organization_id}/{employee_id}/generated.pdf",
        filename="generated.pdf",
        file_size=18,
        checksum="checksum",
    )
    monkeypatch.setattr(
        "app.services.people.hr.employee_extended.get_employee_document_upload",
        lambda: upload_service,
    )

    service = EmployeeDocumentService(db, organization_id)
    document = service.upload_document(
        employee_id=employee_id,
        document_type=DocumentType.CONTRACT,
        document_name="Employment contract",
        file_content=BytesIO(b"%PDF test content"),
        file_name="contract.pdf",
        content_type="application/pdf",
    )

    upload_service.save.assert_called_once_with(
        file_data=b"%PDF test content",
        content_type="application/pdf",
        subdirs=(str(organization_id), str(employee_id)),
        original_filename="contract.pdf",
    )
    assert document.organization_id == organization_id
    assert document.employee_id == employee_id
    assert document.file_path.endswith("/generated.pdf")
    assert document.file_name == "contract.pdf"
    assert document.file_size == 18
    assert document.mime_type == "application/pdf"
    db.add.assert_called_once_with(document)
    db.flush.assert_called_once()


def test_upload_document_rejects_file_validation_error(monkeypatch):
    organization_id = uuid4()
    employee_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(employee_id=employee_id)

    upload_service = MagicMock()
    upload_service.save.side_effect = InvalidExtensionError(
        "File extension '.exe' not allowed"
    )
    monkeypatch.setattr(
        "app.services.people.hr.employee_extended.get_employee_document_upload",
        lambda: upload_service,
    )

    service = EmployeeDocumentService(db, organization_id)
    with pytest.raises(EmployeeExtendedDataError, match="not allowed"):
        service.upload_document(
            employee_id=employee_id,
            document_type=DocumentType.OTHER,
            document_name="Unsafe file",
            file_content=BytesIO(b"MZ"),
            file_name="malware.exe",
            content_type="application/octet-stream",
        )

    db.add.assert_not_called()
    db.flush.assert_not_called()


def test_upload_document_rejects_employee_outside_organization(monkeypatch):
    organization_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = None

    get_upload = MagicMock()
    monkeypatch.setattr(
        "app.services.people.hr.employee_extended.get_employee_document_upload",
        get_upload,
    )

    service = EmployeeDocumentService(db, organization_id)
    with pytest.raises(EmployeeExtendedDataError, match="not found"):
        service.upload_document(
            employee_id=uuid4(),
            document_type=DocumentType.ID_PROOF,
            document_name="Identity document",
            file_content=BytesIO(b"%PDF test content"),
            file_name="identity.pdf",
            content_type="application/pdf",
        )

    get_upload.assert_not_called()
    db.add.assert_not_called()


def test_upload_document_rejects_empty_file(monkeypatch):
    organization_id = uuid4()
    employee_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(employee_id=employee_id)

    get_upload = MagicMock()
    monkeypatch.setattr(
        "app.services.people.hr.employee_extended.get_employee_document_upload",
        get_upload,
    )

    service = EmployeeDocumentService(db, organization_id)
    with pytest.raises(EmployeeExtendedDataError, match="empty"):
        service.upload_document(
            employee_id=employee_id,
            document_type=DocumentType.OTHER,
            document_name="Empty document",
            file_content=BytesIO(b""),
            file_name="empty.pdf",
            content_type="application/pdf",
        )

    get_upload.assert_not_called()


def test_employee_document_upload_policy_is_restricted():
    config = get_employee_document_upload().config

    assert config.s3_prefix == "employee_documents"
    assert config.max_size_bytes == 10 * 1024 * 1024
    assert config.require_magic_bytes is True
    assert config.compute_checksum is True
    assert config.allowed_extensions == frozenset(
        {".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png"}
    )


def test_employee_document_form_uses_multipart_file_upload():
    template = Path("templates/people/hr/employee/document_form.html").read_text()

    assert 'enctype="multipart/form-data"' in template
    assert 'name="file"' in template
    assert "required=true" in template
    assert "request.state.csrf_form | safe" in template
    assert 'name="file_path"' not in template
    assert 'name="file_name"' not in template


def test_create_document_route_passes_uploaded_file_to_service(monkeypatch):
    organization_id = uuid4()
    employee_id = uuid4()
    captured: dict[str, object] = {}

    class _DocumentService:
        def __init__(self, db, org_id):
            captured["db"] = db
            captured["organization_id"] = org_id

        def upload_document(self, **kwargs):
            captured.update(kwargs)
            captured["file_bytes"] = kwargs["file_content"].read()

    monkeypatch.setattr(
        employee_document_routes, "EmployeeDocumentService", _DocumentService
    )

    db = MagicMock()
    response = employee_document_routes.create_document(
        request=MagicMock(),
        employee_id=str(employee_id),
        document_type=DocumentType.CONTRACT.value,
        document_name="Employment contract",
        file=UploadFile(
            BytesIO(b"%PDF contract"),
            filename="contract.pdf",
            headers=Headers({"content-type": "application/pdf"}),
        ),
        description="Signed contract",
        issue_date="2026-07-20",
        expiry_date=None,
        auth=SimpleNamespace(organization_id=organization_id),
        db=db,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/documents?success=Document+uploaded")
    assert captured["organization_id"] == organization_id
    assert captured["employee_id"] == employee_id
    assert captured["file_name"] == "contract.pdf"
    assert captured["content_type"] == "application/pdf"
    assert captured["file_bytes"] == b"%PDF contract"


def test_create_document_route_requires_hr_access():
    auth_dependency = (
        inspect.signature(employee_document_routes.create_document)
        .parameters["auth"]
        .default
    )

    assert auth_dependency.dependency is require_hr_access


def test_download_document_route_streams_resolved_document():
    organization_id = uuid4()
    employee_id = uuid4()
    document_id = uuid4()

    class _DocumentService:
        def __init__(self, db, org_id):
            assert org_id == organization_id

        def resolve_owned_document_download(self, employee_id_arg, document_id_arg):
            assert employee_id_arg == employee_id
            assert document_id_arg == document_id
            return SimpleNamespace(
                chunks=iter([b"pdf-bytes"]),
                content_type="application/pdf",
                content_length=9,
                filename="safe.pdf",
            )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            employee_document_routes,
            "EmployeeDocumentService",
            _DocumentService,
        )
        response = employee_document_routes.download_document(
            employee_id=str(employee_id),
            document_id=str(document_id),
            auth=SimpleNamespace(organization_id=organization_id),
            db=MagicMock(),
        )

    assert response.status_code == 200
    assert "attachment;" in response.headers["Content-Disposition"]
    assert "safe.pdf" in response.headers["Content-Disposition"]
