"""Discipline case documents on the dotmac-files tenant plane."""

from io import BytesIO
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.errors import NotFoundError, ValidationError
from app.models.people.discipline import DocumentType
from app.services.file_upload import FileUploadError
from app.services.people.discipline.attachment_service import (
    DisciplineAttachmentService,
    staged_file_id,
)

_STAGE = "app.services.people.discipline.attachment_service._stage_discipline_file"


def _prepared(org_id, size=15):
    prepared = MagicMock()
    prepared.id = uuid4()
    prepared.storage_key = f"tenants/{org_id}/files/{prepared.id}"
    prepared.size_bytes = size
    return prepared


def _service(mock_db, org_id, case_id):
    case = MagicMock()
    case.organization_id = org_id
    case.case_number = "DSC-2026-0001"
    mock_db.get.return_value = case
    return DisciplineAttachmentService(mock_db)


class TestStagedFileId:
    def test_parses_tenant_key(self):
        file_id = uuid4()
        document = MagicMock()
        document.file_path = f"tenants/{uuid4()}/files/{file_id}"
        assert staged_file_id(document) == file_id

    def test_legacy_path_is_not_staged(self):
        document = MagicMock()
        document.file_path = "org/case/warning.pdf"
        assert staged_file_id(document) is None


class TestSaveFile:
    def test_stages_under_case_organization(self):
        mock_db = MagicMock()
        org_id, case_id = uuid4(), uuid4()
        service = _service(mock_db, org_id, case_id)
        prepared = _prepared(org_id)

        with patch(_STAGE, return_value=prepared) as stage:
            document = service.save_file(
                org_id,
                case_id,
                BytesIO(b"warning letter!"),
                "warning.pdf",
                "application/pdf",
                DocumentType.WARNING_LETTER,
                uuid4(),
            )

        assert document.file_path == prepared.storage_key
        assert document.file_size == prepared.size_bytes
        assert stage.call_args.args[1] == org_id
        mock_db.add.assert_called_once_with(document)

    def test_missing_case_is_named(self):
        mock_db = MagicMock()
        mock_db.get.return_value = None
        service = DisciplineAttachmentService(mock_db)

        with pytest.raises(NotFoundError):
            service.save_file(
                uuid4(),
                uuid4(),
                BytesIO(b"x"),
                "x.pdf",
                "application/pdf",
                DocumentType.WARNING_LETTER,
                uuid4(),
            )

    def test_staging_refusal_is_validation_error(self):
        mock_db = MagicMock()
        org_id, case_id = uuid4(), uuid4()
        service = _service(mock_db, org_id, case_id)

        with (
            patch(_STAGE, side_effect=FileUploadError("File type not allowed")),
            pytest.raises(ValidationError, match="not allowed"),
        ):
            service.save_file(
                org_id,
                case_id,
                BytesIO(b"binary"),
                "malware.exe",
                "application/x-msdownload",
                DocumentType.WARNING_LETTER,
                uuid4(),
            )
        mock_db.add.assert_not_called()


class TestDeleteAndDownload:
    def test_delete_retires_staged_file(self):
        mock_db = MagicMock()
        org_id, file_id = uuid4(), uuid4()
        document = MagicMock()
        document.file_path = f"tenants/{org_id}/files/{file_id}"
        service = DisciplineAttachmentService(mock_db)

        with (
            patch.object(service, "get_document", return_value=document),
            patch("dotmac_files.request_deletion") as request_deletion,
        ):
            assert service.delete(org_id, uuid4()) is True

        assert request_deletion.call_args.kwargs["file_id"] == file_id
        mock_db.delete.assert_called_once_with(document)

    def test_staged_download_streams_authorized_target(self):
        org_id = uuid4()
        document = MagicMock()
        document.file_path = f"tenants/{org_id}/files/{uuid4()}"
        document.file_name = "warning.pdf"
        document.mime_type = "application/pdf"
        target = MagicMock()
        target.detected_media_type = "application/pdf"
        target.size_bytes = 8
        readable = MagicMock()
        readable.__enter__.return_value = readable
        readable.__exit__.return_value = False
        readable.read.side_effect = [b"%PDF-1.4", b""]
        service = DisciplineAttachmentService(MagicMock())

        with (
            patch(
                "app.services.storage.get_dotmac_files_provider",
                return_value=MagicMock(),
            ),
            patch("dotmac_files.download_target", return_value=target),
            patch("dotmac_files.open_object", return_value=readable),
        ):
            response = service.staged_download_response(org_id, document)

        assert response is not None
        assert response.status_code == 200
        assert response.headers["content-length"] == "8"

    def test_legacy_document_download_returns_none(self):
        document = MagicMock()
        document.file_path = "org/case/warning.pdf"
        service = DisciplineAttachmentService(MagicMock())
        assert service.staged_download_response(uuid4(), document) is None
