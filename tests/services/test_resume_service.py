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
        self, resume_service: ResumeService, valid_pdf_content: bytes
    ):
        """Test successful resume save."""
        org_id = uuid.uuid4()

        file_id, relative_path = resume_service.save_resume(
            org_id,
            "my_resume.pdf",
            valid_pdf_content,
        )

        assert file_id is not None
        assert str(org_id) in relative_path
        assert relative_path.endswith(".pdf")

        # Verify file was written via the bridge mock
        file_path = resume_service.upload_dir / relative_path
        assert file_path.exists()
        assert file_path.read_bytes() == valid_pdf_content

    def test_save_resume_invalid_type(self, resume_service: ResumeService):
        """Test saving resume with invalid type raises error."""
        org_id = uuid.uuid4()

        with pytest.raises(InvalidFileTypeError):
            resume_service.save_resume(
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
                org_id,
                "resume.pdf",
                b"%PDF" + b"x" * 200,
            )

    def test_get_resume_path(
        self, resume_service: ResumeService, valid_pdf_content: bytes
    ):
        """Test retrieving resume path via S3 lookup."""
        org_id = uuid.uuid4()

        file_id, _ = resume_service.save_resume(
            org_id,
            "resume.pdf",
            valid_pdf_content,
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
        self, resume_service: ResumeService, valid_pdf_content: bytes
    ):
        """Test deleting a resume from S3."""
        org_id = uuid.uuid4()

        file_id, _ = resume_service.save_resume(
            org_id,
            "resume.pdf",
            valid_pdf_content,
        )

        # Verify exists
        assert resume_service.get_resume_path(org_id, file_id) is not None

        # Delete
        result = resume_service.delete_resume(org_id, file_id)
        assert result is True

        # Verify deleted (S3 key no longer found)
        assert resume_service.get_resume_path(org_id, file_id) is None

        # Delete non-existent
        result = resume_service.delete_resume(org_id, "nonexistent")
        assert result is False

    def test_get_resume_url(
        self, resume_service: ResumeService, valid_pdf_content: bytes
    ):
        """Test getting resume URL."""
        org_id = uuid.uuid4()

        file_id, _ = resume_service.save_resume(
            org_id,
            "resume.pdf",
            valid_pdf_content,
        )

        url = resume_service.get_resume_url(org_id, file_id)
        assert url is not None
        assert "/files/resumes/" in url
        assert str(org_id) in url

        # Non-existent
        url = resume_service.get_resume_url(org_id, "nonexistent")
        assert url is None

    def test_org_isolation(
        self, resume_service: ResumeService, valid_pdf_content: bytes
    ):
        """Test that resumes are isolated by organization."""
        org1 = uuid.uuid4()
        org2 = uuid.uuid4()

        file_id1, _ = resume_service.save_resume(
            org1,
            "resume.pdf",
            valid_pdf_content,
        )

        file_id2, _ = resume_service.save_resume(
            org2,
            "resume.pdf",
            valid_pdf_content,
        )

        # Each org can only see their own files
        assert resume_service.get_resume_path(org1, file_id1) is not None
        assert resume_service.get_resume_path(org1, file_id2) is None
        assert resume_service.get_resume_path(org2, file_id1) is None
        assert resume_service.get_resume_path(org2, file_id2) is not None
