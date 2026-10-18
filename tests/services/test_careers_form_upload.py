"""Dynamic form attachment uploads on the dotmac-files tenant plane."""

import asyncio
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.careers.web import CareersWebService
from app.services.file_upload import FileUploadError


def _service(mock_db):
    with (
        patch("app.services.careers.web.CareersService"),
        patch("app.services.careers.web.ResumeService"),
    ):
        return CareersWebService(mock_db)


def test_upload_form_attachment_stages_and_commits() -> None:
    mock_db = MagicMock()
    org_id = uuid4()
    file_id = uuid4()
    prepared = MagicMock()
    prepared.id = file_id
    prepared.storage_key = f"tenants/{org_id}/files/{file_id}"
    prepared.size_bytes = 12

    service = _service(mock_db)
    with patch(
        "app.services.careers.web._stage_form_file", return_value=prepared
    ) as stage:
        token, error = asyncio.run(
            service.upload_form_attachment(
                org_id, "certificate.pdf", b"%PDF-1.4data", "application/pdf"
            )
        )

    assert error is None
    assert token == {
        "file_url": f"/files/form_attachments/{org_id}/staged/{file_id}",
        "file_name": "certificate.pdf",
        "s3_key": prepared.storage_key,
        "file_size": 12,
    }
    stage.assert_called_once()
    mock_db.commit.assert_called_once()
    mock_db.rollback.assert_not_called()


def test_upload_form_attachment_refusal_rolls_back() -> None:
    mock_db = MagicMock()
    service = _service(mock_db)

    with patch(
        "app.services.careers.web._stage_form_file",
        side_effect=FileUploadError("File type not allowed"),
    ):
        token, error = asyncio.run(
            service.upload_form_attachment(
                uuid4(), "malware.exe", b"binary", "application/x-msdownload"
            )
        )

    assert token is None
    assert error == "File type not allowed"
    mock_db.rollback.assert_called_once()
    mock_db.commit.assert_not_called()
