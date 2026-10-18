"""Support ticket attachments on the dotmac-files tenant plane."""

from io import BytesIO
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.file_upload import FileUploadError
from app.services.support.attachment import (
    AttachmentService,
    staged_file_id,
)


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def ticket_id():
    return uuid4()


def _ticket(org_id, ticket_id):
    ticket = MagicMock()
    ticket.ticket_id = ticket_id
    ticket.organization_id = org_id
    return ticket


def _prepared(org_id, size=21):
    prepared = MagicMock()
    prepared.storage_key = f"tenants/{org_id}/files/{uuid4()}"
    prepared.size_bytes = size
    prepared.checksum_sha256 = "sha256:abc123"
    return prepared


class TestStagedFileId:
    def test_parses_tenant_storage_key(self, org_id):
        file_id = uuid4()
        attachment = MagicMock()
        attachment.storage_path = f"tenants/{org_id}/files/{file_id}"
        assert staged_file_id(attachment) == file_id

    def test_legacy_relative_path_is_not_staged(self):
        attachment = MagicMock()
        attachment.storage_path = "550e8400/scan.pdf"
        assert staged_file_id(attachment) is None

    def test_malformed_tenant_key_is_not_staged(self):
        attachment = MagicMock()
        attachment.storage_path = "tenants/not-a-uuid/files/not-a-uuid"
        assert staged_file_id(attachment) is None


class TestSaveFile:
    def test_stages_on_the_tickets_tenant_plane(self, mock_db, org_id, ticket_id):
        mock_db.get.return_value = _ticket(org_id, ticket_id)
        prepared = _prepared(org_id)

        with patch(
            "app.services.support.attachment._stage_support_file",
            return_value=prepared,
        ) as stage:
            attachment, error = AttachmentService().save_file(
                mock_db,
                ticket_id=ticket_id,
                filename="log.txt",
                file_data=BytesIO(b"diagnostic log bytes"),
                content_type="text/plain",
                uploaded_by_id=uuid4(),
            )

        assert error is None
        assert attachment is not None
        assert attachment.storage_path == prepared.storage_key
        assert attachment.file_size == prepared.size_bytes
        stage.assert_called_once()
        assert stage.call_args.args[1] == org_id
        mock_db.add.assert_called_once_with(attachment)
        mock_db.flush.assert_called()

    def test_missing_ticket_is_named_without_staging(self, mock_db, ticket_id):
        mock_db.get.return_value = None

        with patch("app.services.support.attachment._stage_support_file") as stage:
            attachment, error = AttachmentService().save_file(
                mock_db,
                ticket_id=ticket_id,
                filename="log.txt",
                file_data=BytesIO(b"x"),
                content_type="text/plain",
            )

        assert attachment is None
        assert error == "Ticket not found"
        stage.assert_not_called()

    def test_staging_refusal_is_returned(self, mock_db, org_id, ticket_id):
        mock_db.get.return_value = _ticket(org_id, ticket_id)

        with patch(
            "app.services.support.attachment._stage_support_file",
            side_effect=FileUploadError("File type not allowed"),
        ):
            attachment, error = AttachmentService().save_file(
                mock_db,
                ticket_id=ticket_id,
                filename="malware.exe",
                file_data=BytesIO(b"binary"),
                content_type="application/x-msdownload",
            )

        assert attachment is None
        assert error == "File type not allowed"
        mock_db.add.assert_not_called()


class TestDeleteAndPaths:
    def test_hard_delete_retires_staged_file(self, mock_db, org_id):
        file_id = uuid4()
        attachment = MagicMock()
        attachment.storage_path = f"tenants/{org_id}/files/{file_id}"
        service = AttachmentService()

        with (
            patch.object(service, "get_attachment", return_value=attachment),
            patch("dotmac_files.request_deletion") as request_deletion,
        ):
            ok, error = service.delete_attachment(
                mock_db, org_id, uuid4(), hard_delete=True
            )

        assert (ok, error) == (True, None)
        request_deletion.assert_called_once()
        assert request_deletion.call_args.kwargs["file_id"] == file_id
        mock_db.delete.assert_called_once_with(attachment)

    def test_staged_attachment_has_no_local_path(self, mock_db, org_id):
        attachment = MagicMock()
        attachment.is_active = True
        attachment.storage_path = f"tenants/{org_id}/files/{uuid4()}"
        service = AttachmentService()

        with patch.object(service, "get_attachment", return_value=attachment):
            assert service.get_file_path(mock_db, org_id, uuid4()) is None
