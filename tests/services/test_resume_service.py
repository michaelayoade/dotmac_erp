"""Tests for the Resume Service."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.careers.resume_service import (
    FileTooLargeError,
    InvalidFileTypeError,
    ResumeService,
)


class TestResumeService:
    """Test cases for ResumeService."""

    @pytest.fixture
    def _mock_storage(self, tmp_path: Path):
        """Mock S3 storage with key tracking for exists/delete behaviour."""
        mock = MagicMock()
        _keys: dict[str, bytes] = {}

        def _upload(key: str, data: bytes, content_type: str | None = None) -> None:
            _keys[key] = data
            # Also write to disk so save_resume disk assertions still work
            relative = "/".join(key.split("/")[1:])
            path = tmp_path / "resumes" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        def _exists(key: str) -> bool:
            return key in _keys

        def _delete(key: str) -> None:
            _keys.pop(key, None)

        mock.upload.side_effect = _upload
        mock.exists.side_effect = _exists
        mock.delete.side_effect = _delete

        with (
            patch(
                "app.services.file_upload.FileUploadService._get_storage",
                return_value=mock,
            ),
            patch(
                "app.services.careers.resume_service.get_storage",
                return_value=mock,
            ),
        ):
            yield mock

    @pytest.fixture
    def resume_service(self, tmp_path: Path, _mock_storage: MagicMock) -> ResumeService:
        """Create a ResumeService with a temp directory."""
        service = ResumeService()
        service.upload_dir = tmp_path / "resumes"
        service.upload_dir.mkdir(parents=True, exist_ok=True)
        return service

    @pytest.fixture
    def staged_store(self, monkeypatch):
        """In-memory staged-file store behind the service's own seams."""
        store: dict[uuid.UUID, uuid.UUID] = {}

        def _stage(db, org_id, *, file_data, original_filename, config):
            prepared = MagicMock()
            prepared.id = uuid.uuid4()
            prepared.storage_key = f"tenants/{org_id}/files/{prepared.id}"
            prepared.size_bytes = len(file_data)
            prepared.checksum_sha256 = "sha256:cafe"
            store[prepared.id] = org_id
            return prepared

        def _target(db, org_id, file_id):
            if store.get(file_id) != org_id:
                return None
            target = MagicMock()
            target.size_bytes = 1
            return target

        def _request_deletion(db, *, scope, file_id, now):
            store.pop(file_id, None)

        monkeypatch.setattr(
            "app.services.careers.resume_service._stage_resume_file", _stage
        )
        monkeypatch.setattr(ResumeService, "_staged_target", staticmethod(_target))
        monkeypatch.setattr("dotmac_files.request_deletion", _request_deletion)
        return store

    @pytest.fixture
    def valid_pdf_content(self) -> bytes:
        """Create valid PDF-like content."""
        return b"%PDF-1.4\n%test content\n%%EOF"

    @pytest.fixture
    def valid_docx_content(self) -> bytes:
        """Create valid DOCX-like content (ZIP header)."""
        return b"PK\x03\x04" + b"\x00" * 100

    def test_validate_file_valid_pdf(self, resume_service: ResumeService):
        """Test validation of valid PDF file."""
        is_valid, error = resume_service.validate_file(
            "resume.pdf",
            1024,
            b"%PDF-1.4\ntest content",
        )
        assert is_valid is True
        assert error is None

    def test_validate_file_valid_docx(self, resume_service: ResumeService):
        """Test validation of valid DOCX file."""
        is_valid, error = resume_service.validate_file(
            "resume.docx",
            1024,
            b"PK\x03\x04test",
        )
        assert is_valid is True
        assert error is None

    def test_validate_file_invalid_extension(self, resume_service: ResumeService):
        """Test validation rejects invalid extensions."""
        is_valid, error = resume_service.validate_file(
            "resume.exe",
            1024,
            None,
        )
        assert is_valid is False
        assert "not allowed" in error

    def test_validate_file_too_large(self, resume_service: ResumeService):
        """Test validation rejects files that are too large."""
        resume_service.max_size = 1024

        is_valid, error = resume_service.validate_file(
            "resume.pdf",
            2048,
            None,
        )
        assert is_valid is False
        assert "too large" in error.lower()

    def test_validate_file_magic_bytes_mismatch(self, resume_service: ResumeService):
        """Test validation rejects files with mismatched magic bytes."""
        is_valid, error = resume_service.validate_file(
            "resume.pdf",
            1024,
            b"NOT A PDF FILE",
        )
        assert is_valid is False
        assert "content does not match" in error

    def test_save_resume_success(
        self, resume_service: ResumeService, valid_pdf_content: bytes, staged_store
    ):
        """Test successful resume save stages a tenant file and commits."""
        org_id = uuid.uuid4()
        db = MagicMock()

        file_id, storage_key = resume_service.save_resume(
            db,
            org_id,
            "my_resume.pdf",
            valid_pdf_content,
        )

        assert file_id is not None
        assert storage_key == f"tenants/{org_id}/files/{file_id}"
        assert uuid.UUID(file_id) in staged_store
        db.commit.assert_called_once()

    def test_save_resume_invalid_type(self, resume_service: ResumeService):
        """Test saving resume with invalid type raises error."""
        org_id = uuid.uuid4()

        with pytest.raises(InvalidFileTypeError):
            resume_service.save_resume(
                MagicMock(),
                org_id,
                "malware.exe",
                b"malicious content",
            )

    def test_save_resume_too_large(self, resume_service: ResumeService):
        """Test saving resume that's too large raises error."""
        resume_service.max_size = 100
        org_id = uuid.uuid4()

        with pytest.raises(FileTooLargeError):
            resume_service.save_resume(
                MagicMock(),
                org_id,
                "resume.pdf",
                b"%PDF" + b"x" * 200,
            )

    def test_get_resume_path(
        self, resume_service: ResumeService, valid_pdf_content: bytes, _mock_storage
    ):
        """Legacy S3-key lookup still resolves pre-cutover resumes."""
        org_id = uuid.uuid4()
        file_id = str(uuid.uuid4())
        _mock_storage.upload(
            f"resumes/{org_id}/{file_id}.pdf", valid_pdf_content, "application/pdf"
        )

        path = resume_service.get_resume_path(org_id, file_id)
        assert path is not None
        assert path.name.endswith(".pdf")

        # Non-existent file
        path = resume_service.get_resume_path(org_id, "nonexistent")
        assert path is None

        # Wrong org
        path = resume_service.get_resume_path(uuid.uuid4(), file_id)
        assert path is None

    def test_delete_resume(
        self, resume_service: ResumeService, valid_pdf_content: bytes, staged_store
    ):
        """Test deleting a staged resume retires its tenant record."""
        org_id = uuid.uuid4()
        db = MagicMock()

        file_id, _ = resume_service.save_resume(
            db,
            org_id,
            "resume.pdf",
            valid_pdf_content,
        )
        assert uuid.UUID(file_id) in staged_store

        result = resume_service.delete_resume(db, org_id, file_id)
        assert result is True
        assert uuid.UUID(file_id) not in staged_store

        # Delete non-existent
        result = resume_service.delete_resume(db, org_id, "nonexistent")
        assert result is False

    def test_get_resume_url(
        self, resume_service: ResumeService, valid_pdf_content: bytes, staged_store
    ):
        """Staged resumes resolve through the by-id download route."""
        org_id = uuid.uuid4()
        db = MagicMock()

        file_id, _ = resume_service.save_resume(
            db,
            org_id,
            "resume.pdf",
            valid_pdf_content,
        )

        url = resume_service.get_resume_url(db, org_id, file_id)
        assert url == f"/files/resumes/{file_id}"

        # Non-existent
        url = resume_service.get_resume_url(db, org_id, "nonexistent")
        assert url is None

    def test_org_isolation(
        self, resume_service: ResumeService, valid_pdf_content: bytes, staged_store
    ):
        """Test that resumes are isolated by organization."""
        org1 = uuid.uuid4()
        org2 = uuid.uuid4()
        db = MagicMock()

        file_id1, key1 = resume_service.save_resume(
            db,
            org1,
            "resume.pdf",
            valid_pdf_content,
        )

        file_id2, key2 = resume_service.save_resume(
            db,
            org2,
            "resume.pdf",
            valid_pdf_content,
        )

        assert key1.startswith(f"tenants/{org1}/")
        assert key2.startswith(f"tenants/{org2}/")
        # Cross-org URL resolution must not find the other org's file
        assert resume_service.get_resume_url(db, org2, file_id1) is None

        # Each org can only see their own files
        assert resume_service.get_resume_url(db, org1, file_id1) is not None
        assert resume_service.get_resume_url(db, org1, file_id2) is None
        assert resume_service.get_resume_url(db, org2, file_id2) is not None
