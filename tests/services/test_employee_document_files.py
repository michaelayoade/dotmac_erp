"""Employee documents and pending evidence on the dotmac-files tenant plane."""

from io import BytesIO
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.people.hr import DocumentType
from app.services.file_upload import FileUploadError
from app.services.people.hr.employee_extended import (
    EmployeeDocumentService,
    EmployeeExtendedDataError,
    delete_employee_file,
    staged_file_id_for_path,
)
from app.services.people.self_service_web import SelfServiceWebService

_MODULE = "app.services.people.hr.employee_extended"


def _prepared(org_id, size=17):
    prepared = MagicMock()
    prepared.id = uuid4()
    prepared.storage_key = f"tenants/{org_id}/files/{prepared.id}"
    prepared.size_bytes = size
    prepared.checksum_sha256 = "sha256:deadbeef"
    return prepared


class TestStagedFileIdForPath:
    def test_parses_tenant_key(self):
        file_id = uuid4()
        assert staged_file_id_for_path(f"tenants/{uuid4()}/files/{file_id}") == file_id

    def test_legacy_and_empty_paths(self):
        assert staged_file_id_for_path("550e8400/id-card.pdf") is None
        assert staged_file_id_for_path(None) is None
        assert staged_file_id_for_path("") is None


class TestUploadDocument:
    def test_stages_and_records_tenant_key(self):
        mock_db = MagicMock()
        org_id, employee_id = uuid4(), uuid4()
        service = EmployeeDocumentService(mock_db, org_id)
        prepared = _prepared(org_id)

        with (
            patch.object(service, "_get_employee", return_value=MagicMock()),
            patch(f"{_MODULE}._stage_employee_file", return_value=prepared) as stage,
        ):
            document = service.upload_document(
                employee_id,
                DocumentType.CONTRACT,
                "Employment contract",
                BytesIO(b"contract bytes!"),
                "contract.pdf",
                "application/pdf",
            )

        assert document.file_path == prepared.storage_key
        assert document.file_size == prepared.size_bytes
        assert document.content_checksum == "deadbeef"
        stage.assert_called_once()
        mock_db.add.assert_called_once_with(document)

    def test_staging_refusal_is_named(self):
        mock_db = MagicMock()
        service = EmployeeDocumentService(mock_db, uuid4())

        with (
            patch.object(service, "_get_employee", return_value=MagicMock()),
            patch(
                f"{_MODULE}._stage_employee_file",
                side_effect=FileUploadError("File type not allowed"),
            ),
            pytest.raises(EmployeeExtendedDataError, match="not allowed"),
        ):
            service.upload_document(
                uuid4(),
                DocumentType.CONTRACT,
                "Bad file",
                BytesIO(b"binary"),
                "malware.exe",
                "application/x-msdownload",
            )

        mock_db.add.assert_not_called()


class TestResolveDownload:
    def test_staged_document_streams_authorized_target(self):
        mock_db = MagicMock()
        org_id = uuid4()
        service = EmployeeDocumentService(mock_db, org_id)
        document = MagicMock()
        document.file_path = f"tenants/{org_id}/files/{uuid4()}"
        document.file_name = "id-card.pdf"
        document.mime_type = "application/pdf"
        target = MagicMock()
        target.detected_media_type = "application/pdf"
        target.size_bytes = 8
        readable = MagicMock()
        readable.__enter__.return_value = readable
        readable.__exit__.return_value = False
        readable.read.side_effect = [b"%PDF-1.4", b""]

        with (
            patch.object(service, "get_document", return_value=document),
            patch(
                "app.services.storage.get_dotmac_files_provider",
                return_value=MagicMock(),
            ),
            patch("dotmac_files.download_target", return_value=target) as resolve,
            patch("dotmac_files.open_object", return_value=readable),
        ):
            result = service.resolve_document_download(uuid4())

        assert resolve.call_args.kwargs["file_id"] is not None
        assert result.content_type == "application/pdf"
        assert result.content_length == 8
        assert result.filename == "id-card.pdf"
        assert b"".join(result.chunks) == b"%PDF-1.4"


class TestDeleteEmployeeFile:
    def test_staged_path_requests_deletion(self):
        org_id, file_id = uuid4(), uuid4()
        with patch("dotmac_files.request_deletion") as request_deletion:
            delete_employee_file(
                MagicMock(), org_id, f"tenants/{org_id}/files/{file_id}"
            )
        assert request_deletion.call_args.kwargs["file_id"] == file_id

    def test_legacy_path_uses_legacy_delete(self):
        upload = MagicMock()
        with patch(f"{_MODULE}.get_employee_document_upload", return_value=upload):
            delete_employee_file(MagicMock(), uuid4(), "550e8400/id-card.pdf")
        upload.delete.assert_called_once_with("550e8400/id-card.pdf")


class TestPendingEvidence:
    def test_upload_pending_evidence_stages_tenant_file(self):
        mock_db = MagicMock()
        org_id = uuid4()
        prepared = _prepared(org_id)
        service = SelfServiceWebService.__new__(SelfServiceWebService)
        upload = MagicMock()
        upload.filename = "evidence.pdf"
        upload.content_type = "application/pdf"
        upload.file.read.return_value = b"evidence bytes!"

        with patch(
            "app.services.people.self_service_web._stage_employee_file",
            return_value=prepared,
        ) as stage:
            evidence = service._upload_pending_evidence(
                mock_db, org_id=org_id, employee_id=uuid4(), upload=upload
            )

        assert evidence is not None
        assert evidence.path == prepared.storage_key
        assert evidence.file_size == prepared.size_bytes
        assert evidence.checksum == "deadbeef"
        stage.assert_called_once()
