"""Default HR invite attachments on the dotmac-files tenant plane."""

import asyncio
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.people.hr.invite_attachment import (
    delete_invite_attachment_object,
    load_default_invite_attachment,
    read_invite_attachment_bytes,
    staged_file_id,
)

_STAGE = "app.services.people.settings_web.stage_default_invite_attachment"


def _prepared(org_id, size=9):
    prepared = MagicMock()
    prepared.id = uuid4()
    prepared.storage_key = f"tenants/{org_id}/files/{prepared.id}"
    prepared.size_bytes = size
    return prepared


class TestStagedFileId:
    def test_parses_tenant_key(self):
        file_id = uuid4()
        metadata = {"s3_key": f"tenants/{uuid4()}/files/{file_id}"}
        assert staged_file_id(metadata) == file_id

    def test_legacy_key_is_not_staged(self):
        assert staged_file_id({"s3_key": "hr_invite_attachments/org/pack.pdf"}) is None
        assert staged_file_id(None) is None
        assert staged_file_id({}) is None


class TestReadBytes:
    def test_staged_reads_through_authorized_target(self):
        org_id = uuid4()
        metadata = {"s3_key": f"tenants/{org_id}/files/{uuid4()}"}
        target = MagicMock()
        readable = MagicMock()
        readable.__enter__.return_value = readable
        readable.__exit__.return_value = False
        readable.read.return_value = b"welcome pack"

        with (
            patch(
                "app.services.storage.get_dotmac_files_provider",
                return_value=MagicMock(),
            ),
            patch("dotmac_files.download_target", return_value=target) as resolve,
            patch("dotmac_files.open_object", return_value=readable),
        ):
            data = read_invite_attachment_bytes(MagicMock(), org_id, metadata)

        assert data == b"welcome pack"
        assert resolve.call_args.kwargs["file_id"] is not None

    def test_legacy_read_is_not_found(self):
        with pytest.raises(FileNotFoundError):
            read_invite_attachment_bytes(
                MagicMock(),
                uuid4(),
                {"s3_key": "hr_invite_attachments/org/pack.pdf"},
            )


class TestDeleteObject:
    def test_staged_requests_deletion(self):
        org_id, file_id = uuid4(), uuid4()
        with patch("dotmac_files.request_deletion") as request_deletion:
            delete_invite_attachment_object(
                MagicMock(), org_id, {"s3_key": f"tenants/{org_id}/files/{file_id}"}
            )
        assert request_deletion.call_args.kwargs["file_id"] == file_id

    def test_legacy_deletes_object(self):
        storage = MagicMock()
        with patch(
            "app.services.people.hr.invite_attachment.get_storage",
            return_value=storage,
        ):
            delete_invite_attachment_object(
                MagicMock(), uuid4(), {"s3_key": "hr_invite_attachments/org/p.pdf"}
            )
        storage.delete.assert_called_once_with("hr_invite_attachments/org/p.pdf")

    def test_load_uses_staged_reader(self):
        org_id = uuid4()
        metadata = {
            "s3_key": f"tenants/{org_id}/files/{uuid4()}",
            "filename": "pack.pdf",
            "content_type": "application/pdf",
        }
        with (
            patch(
                "app.services.people.hr.invite_attachment.get_default_invite_attachment_metadata",
                return_value=metadata,
            ),
            patch(
                "app.services.people.hr.invite_attachment.read_invite_attachment_bytes",
                return_value=b"data",
            ),
        ):
            result = load_default_invite_attachment(MagicMock(), org_id)
        assert result == ("pack.pdf", b"data", "application/pdf")


class TestSettingsUpload:
    def test_upload_stages_and_replaces_previous(self):
        from app.services.people.settings_web import PeopleSettingsWebService

        mock_db = MagicMock()
        org_id = uuid4()
        previous = {"s3_key": f"tenants/{org_id}/files/{uuid4()}"}
        prepared = _prepared(org_id)
        service = PeopleSettingsWebService.__new__(PeopleSettingsWebService)

        from io import BytesIO

        from fastapi import UploadFile

        upload = UploadFile(
            filename="welcome.pdf",
            file=BytesIO(b"%PDF-1.4 pack"),
            headers={"content-type": "application/pdf"},
        )

        with (
            patch(
                "app.services.people.settings_web.get_default_invite_attachment_metadata",
                return_value=previous,
            ),
            patch(_STAGE, return_value=prepared) as stage,
            patch(
                "app.services.people.settings_web.set_default_invite_attachment_metadata"
            ) as set_metadata,
            patch(
                "app.services.people.settings_web.delete_invite_attachment_object"
            ) as delete_object,
        ):
            ok, error = asyncio.run(
                service.update_default_invite_attachment(
                    mock_db, org_id, file=upload, remove_existing=False
                )
            )

        assert (ok, error) == (True, None)
        stage.assert_called_once()
        assert set_metadata.call_args.args[2]["s3_key"] == prepared.storage_key
        delete_object.assert_called_once_with(mock_db, org_id, previous)
        mock_db.commit.assert_called_once()
