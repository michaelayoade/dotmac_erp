"""Tests for dynamic form attachment downloads."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.files import download_form_attachment
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
