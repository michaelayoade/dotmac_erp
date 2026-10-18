"""Project/task attachments on the dotmac-files tenant plane."""

from io import BytesIO
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.finance.common.attachment import AttachmentCategory
from app.services.file_upload import FileUploadError
from app.services.pm.attachment import (
    ProjectAttachmentService,
    staged_download_response,
)

_STAGE = "app.services.finance.common.attachment._stage_attachment_file"


def _prepared(org_id, size=14):
    prepared = MagicMock()
    prepared.id = uuid4()
    prepared.storage_key = f"tenants/{org_id}/files/{prepared.id}"
    prepared.size_bytes = size
    prepared.checksum_sha256 = "sha256:cafe"
    return prepared


class TestSaveFile:
    def test_stages_with_pm_policy_config(self):
        mock_db = MagicMock()
        org_id, project_id = uuid4(), uuid4()
        service = ProjectAttachmentService()
        prepared = _prepared(org_id)

        with patch(_STAGE, return_value=prepared) as stage:
            attachment, error = service.save_file(
                mock_db,
                org_id,
                "PROJECT",
                project_id,
                "plan.zip",
                BytesIO(b"PK\x03\x04archive!"),
                "application/zip",
                uuid4(),
            )

        assert error is None
        assert attachment is not None
        assert attachment.file_path == prepared.storage_key
        assert attachment.file_size == prepared.size_bytes
        assert attachment.checksum == "cafe"
        assert attachment.category == AttachmentCategory.PROJECT
        assert stage.call_args.kwargs["config"] is service.upload_service.config
        mock_db.add.assert_called_once_with(attachment)

    def test_staging_refusal_is_returned(self):
        service = ProjectAttachmentService()
        with patch(_STAGE, side_effect=FileUploadError("File type not allowed")):
            attachment, error = service.save_file(
                MagicMock(),
                uuid4(),
                "TASK",
                uuid4(),
                "malware.exe",
                BytesIO(b"binary"),
                "application/x-msdownload",
                uuid4(),
            )
        assert attachment is None
        assert error == "File type not allowed"


class TestDeleteAndPaths:
    def test_hard_delete_retires_staged_file(self):
        mock_db = MagicMock()
        org_id, file_id = uuid4(), uuid4()
        attachment = MagicMock()
        attachment.file_path = f"tenants/{org_id}/files/{file_id}"
        service = ProjectAttachmentService()

        with (
            patch.object(service, "get_attachment", return_value=attachment),
            patch("dotmac_files.request_deletion") as request_deletion,
        ):
            ok, error = service.delete_attachment(mock_db, org_id, uuid4())

        assert (ok, error) == (True, None)
        assert request_deletion.call_args.kwargs["file_id"] == file_id
        mock_db.delete.assert_called_once_with(attachment)

    def test_staged_attachment_has_no_local_path(self):
        org_id = uuid4()
        attachment = MagicMock()
        attachment.file_path = f"tenants/{org_id}/files/{uuid4()}"
        service = ProjectAttachmentService()
        with patch.object(service, "get_attachment", return_value=attachment):
            assert service.get_file_path(MagicMock(), org_id, uuid4()) is None


class TestStagedDownloadResponse:
    def test_streams_authorized_target(self):
        org_id = uuid4()
        attachment = MagicMock()
        attachment.file_path = f"tenants/{org_id}/files/{uuid4()}"
        attachment.file_name = "plan.zip"
        attachment.content_type = "application/zip"
        target = MagicMock()
        target.detected_media_type = "application/zip"
        target.size_bytes = 4
        readable = MagicMock()
        readable.__enter__.return_value = readable
        readable.__exit__.return_value = False
        readable.read.side_effect = [b"PK\x03\x04", b""]

        with (
            patch(
                "app.services.storage.get_dotmac_files_provider",
                return_value=MagicMock(),
            ),
            patch("dotmac_files.download_target", return_value=target),
            patch("dotmac_files.open_object", return_value=readable),
        ):
            response = staged_download_response(MagicMock(), org_id, attachment)

        assert response is not None
        assert response.status_code == 200
        assert response.headers["content-length"] == "4"

    def test_legacy_attachment_returns_none(self):
        attachment = MagicMock()
        attachment.file_path = "project/uuid/plan.zip"
        assert staged_download_response(MagicMock(), uuid4(), attachment) is None
