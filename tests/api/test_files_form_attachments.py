"""Tests for dynamic form attachment downloads."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.files import download_form_attachment, download_staged_form_attachment
from app.services.storage import StorageReadUnavailable


def test_download_form_attachment_streams_org_scoped_s3_key() -> None:
    org_id = uuid.uuid4()
    storage = MagicMock()
    storage.exists.return_value = True
    storage.stream.return_value = (iter([b"%PDF-1.4"]), "application/pdf", 8)

    with patch("app.api.files.get_storage", return_value=storage):
        response = download_form_attachment(
            org_id,
            "resume.pdf",
            organization_id=org_id,
        )

    storage.exists.assert_called_once_with(f"form_attachments/{org_id}/resume.pdf")
    storage.stream.assert_called_once_with(f"form_attachments/{org_id}/resume.pdf")
    assert response.status_code == 200
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")


def test_download_form_attachment_hides_other_org_files() -> None:
    with pytest.raises(HTTPException) as exc_info:
        download_form_attachment(
            uuid.uuid4(),
            "resume.pdf",
            organization_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 404


def test_download_form_attachment_rejects_path_filename() -> None:
    org_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc_info:
        download_form_attachment(
            org_id,
            "../resume.pdf",
            organization_id=org_id,
        )

    assert exc_info.value.status_code == 400


def test_download_form_attachment_reports_storage_outage_as_retryable() -> None:
    org_id = uuid.uuid4()
    storage = MagicMock()
    storage.exists.side_effect = StorageReadUnavailable(
        "Object storage is temporarily unavailable"
    )

    with (
        patch("app.api.files.get_storage", return_value=storage),
        pytest.raises(HTTPException) as exc_info,
    ):
        download_form_attachment(
            org_id,
            "resume.pdf",
            organization_id=org_id,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.headers == {"Retry-After": "5"}


def test_download_staged_form_attachment_streams_authorized_target() -> None:
    org_id = uuid.uuid4()
    file_id = uuid.uuid4()
    target = MagicMock()
    target.original_filename = "certificate.pdf"
    target.detected_media_type = "application/pdf"
    target.size_bytes = 8
    readable = MagicMock()
    readable.__enter__.return_value = readable
    readable.__exit__.return_value = False
    readable.read.side_effect = [b"%PDF-1.4", b""]

    with (
        patch("app.api.files.get_dotmac_files_provider", return_value=MagicMock()),
        patch("dotmac_files.download_target", return_value=target) as resolve,
        patch("dotmac_files.open_object", return_value=readable),
    ):
        response = download_staged_form_attachment(
            org_id,
            file_id,
            organization_id=org_id,
            db=MagicMock(),
        )

    assert resolve.call_args.kwargs["file_id"] == file_id
    assert response.status_code == 200
    assert response.media_type == "application/pdf"
    assert response.headers["content-length"] == "8"
    assert "certificate.pdf" in response.headers["content-disposition"]


def test_download_staged_form_attachment_hides_other_org_files() -> None:
    with pytest.raises(HTTPException) as exc_info:
        download_staged_form_attachment(
            uuid.uuid4(),
            uuid.uuid4(),
            organization_id=uuid.uuid4(),
            db=MagicMock(),
        )

    assert exc_info.value.status_code == 404
