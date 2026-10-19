"""Vehicle documents on the dotmac-files tenant plane."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.fleet.document_service import (
    DocumentService,
    retire_vehicle_document_file,
    vehicle_document_staged_file_id,
)


class TestStagedFileId:
    def test_canonical_key_parses(self):
        file_id = uuid4()
        assert (
            vehicle_document_staged_file_id(f"tenants/{uuid4()}/files/{file_id}")
            == file_id
        )

    def test_legacy_and_empty_paths_return_none(self):
        assert vehicle_document_staged_file_id("org/fleet_documents/doc.pdf") is None
        assert vehicle_document_staged_file_id("") is None
        assert vehicle_document_staged_file_id(None) is None
        assert vehicle_document_staged_file_id("tenants/x/files/not-a-uuid") is None


class TestRetire:
    def test_staged_file_is_marked_for_deletion(self):
        org_id, file_id = uuid4(), uuid4()
        with patch("dotmac_files.request_deletion") as request_deletion:
            retire_vehicle_document_file(
                MagicMock(), org_id, f"tenants/{org_id}/files/{file_id}"
            )
        assert request_deletion.call_args.kwargs["file_id"] == file_id

    def test_legacy_file_is_left_alone(self):
        with patch("dotmac_files.request_deletion") as request_deletion:
            retire_vehicle_document_file(
                MagicMock(), uuid4(), "org/fleet_documents/doc.pdf"
            )
        request_deletion.assert_not_called()


class TestDelete:
    def test_delete_retires_staged_file_before_row_delete(self):
        org_id, file_id = uuid4(), uuid4()
        document = MagicMock()
        document.organization_id = org_id
        document.file_path = f"tenants/{org_id}/files/{file_id}"
        db = MagicMock()
        db.get.return_value = document
        service = DocumentService(db, org_id)

        with patch("dotmac_files.request_deletion") as request_deletion:
            service.delete(uuid4())

        assert request_deletion.call_args.kwargs["file_id"] == file_id
        db.delete.assert_called_once_with(document)
