"""Tests for dynamic form attachment downloads."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.files import download_staged_form_attachment


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
