"""HR handbook documents on the dotmac-files tenant plane."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.people.hr.handbook_service import (
    HRDocumentFileNotFoundError,
    HRDocumentService,
    HRDocumentValidationError,
)


def _service_with_stage(monkeypatch, prepared):
    upload = MagicMock()
    upload.config.max_size_bytes = 10 * 1024 * 1024
    upload.config.allowed_extensions = frozenset(
        {".pdf", ".doc", ".docx", ".txt", ".rtf"}
    )
    monkeypatch.setattr(
        "app.services.people.hr.handbook_service.get_hr_handbook_upload",
        lambda: upload,
    )
    monkeypatch.setattr(
        "app.services.storage.get_dotmac_files_provider", lambda: MagicMock()
    )
    monkeypatch.setattr(
        "app.services.file_object_cleanup.stage_tenant_file_if_unreserved",
        lambda db, *, prepared: None,
    )
    return upload


class TestSaveDocumentFile:
    def test_rtf_declared_media_is_derived_from_extension(self, monkeypatch):
        org_id, file_id = uuid4(), uuid4()
        prepared = SimpleNamespace(
            storage_key=f"tenants/{org_id}/files/{file_id}",
            size_bytes=21,
            checksum_sha256="sha256:" + "b" * 64,
        )
        _service_with_stage(monkeypatch, prepared)
        captured = {}

        def _prepare(provider, **kwargs):
            captured.update(kwargs)
            return prepared

        monkeypatch.setattr("dotmac_files.prepare_upload", _prepare)

        reference, size, checksum = HRDocumentService(MagicMock()).save_document_file(
            org_id,
            "policy.rtf",
            b"{\\rtf1\\ansi policy}",
            content_type="application/octet-stream",
        )

        assert reference == prepared.storage_key
        assert (size, checksum) == (21, "b" * 64)
        assert captured["declared_media_type"] == "application/rtf"
        assert "application/rtf" in captured["policy"].allowed_media_types

    def test_unknown_extension_is_a_validation_error(self, monkeypatch):
        from dotmac_files import FileError

        _service_with_stage(monkeypatch, MagicMock())
        monkeypatch.setattr(
            "dotmac_files.prepare_upload",
            MagicMock(side_effect=FileError("extension '.exe' is not allowed")),
        )

        with pytest.raises(HRDocumentValidationError):
            HRDocumentService(MagicMock()).save_document_file(
                uuid4(), "malware.exe", b"binary"
            )


class TestStreamDocument:
    def test_staged_document_streams_authorized_target(self):
        org_id, file_id = uuid4(), uuid4()
        document = SimpleNamespace(
            organization_id=org_id,
            file_path=f"tenants/{org_id}/files/{file_id}",
        )
        target = MagicMock()
        target.detected_media_type = "application/rtf"
        target.size_bytes = 4
        readable = MagicMock()
        readable.__enter__.return_value = readable
        readable.__exit__.return_value = False
        readable.read.side_effect = [b"{\\rt", b"f1}", b""]

        with (
            patch(
                "app.services.storage.get_dotmac_files_provider",
                return_value=MagicMock(),
            ),
            patch("dotmac_files.download_target", return_value=target),
            patch("dotmac_files.open_object", return_value=readable),
        ):
            chunks, content_type, content_length = HRDocumentService(
                MagicMock()
            ).stream_document(document)

        assert b"".join(chunks) == b"{\\rtf1}"
        assert (content_type, content_length) == ("application/rtf", 4)

    def test_legacy_document_keeps_storage_branch(self):
        org_id = uuid4()
        document = SimpleNamespace(
            organization_id=org_id,
            file_path=f"hr_documents/{org_id}/policy.pdf",
        )
        storage = MagicMock()
        storage.exists.return_value = True
        storage.stream.return_value = (iter([b"%PDF"]), "application/pdf", 4)

        with patch(
            "app.services.people.hr.handbook_service.get_storage",
            return_value=storage,
        ):
            chunks, content_type, content_length = HRDocumentService(
                MagicMock()
            ).stream_document(document)

        assert b"".join(chunks) == b"%PDF"
        assert (content_type, content_length) == ("application/pdf", 4)

    def test_missing_staged_object_is_not_found(self):
        from dotmac_files import FileError

        org_id = uuid4()
        document = SimpleNamespace(
            organization_id=org_id,
            file_path=f"tenants/{org_id}/files/{uuid4()}",
        )
        with (
            patch(
                "app.services.storage.get_dotmac_files_provider",
                return_value=MagicMock(),
            ),
            patch("dotmac_files.download_target", side_effect=FileError("missing")),
            pytest.raises(HRDocumentFileNotFoundError),
        ):
            HRDocumentService(MagicMock()).stream_document(document)
