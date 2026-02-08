"""
Tests for the unified FileUploadService.
"""

import pytest

from app.services.file_upload import (
    FileTooLargeError,
    FileUploadConfig,
    FileUploadService,
    InvalidContentTypeError,
    InvalidExtensionError,
    InvalidMagicBytesError,
    PathTraversalError,
    UploadResult,
)


@pytest.fixture
def tmp_upload_dir(tmp_path):
    """Provide a temporary upload directory."""
    return str(tmp_path / "uploads")


@pytest.fixture
def image_config(tmp_upload_dir):
    """Config for image uploads."""
    return FileUploadConfig(
        base_dir=tmp_upload_dir,
        allowed_content_types=frozenset({"image/jpeg", "image/png"}),
        max_size_bytes=1024 * 1024,  # 1MB
    )


@pytest.fixture
def doc_config(tmp_upload_dir):
    """Config for document uploads with extensions + magic bytes."""
    return FileUploadConfig(
        base_dir=tmp_upload_dir,
        allowed_content_types=frozenset({"application/pdf"}),
        allowed_extensions=frozenset({".pdf", ".doc"}),
        max_size_bytes=5 * 1024 * 1024,
        require_magic_bytes=True,
        compute_checksum=True,
    )


@pytest.fixture
def image_service(image_config):
    return FileUploadService(image_config)


@pytest.fixture
def doc_service(doc_config):
    return FileUploadService(doc_config)


class TestValidate:
    """Tests for the validate() method."""

    def test_valid_content_type(self, image_service):
        """Should pass with allowed content type."""
        image_service.validate("image/jpeg", "photo.jpg", 100)

    def test_invalid_content_type(self, image_service):
        """Should reject disallowed content type."""
        with pytest.raises(InvalidContentTypeError, match="not allowed"):
            image_service.validate("application/pdf", "doc.pdf", 100)

    def test_valid_extension(self, doc_service):
        """Should pass with allowed extension."""
        doc_service.validate("application/pdf", "report.pdf", 100, b"%PDF-1.4")

    def test_invalid_extension(self, doc_service):
        """Should reject disallowed extension."""
        with pytest.raises(InvalidExtensionError, match="not allowed"):
            doc_service.validate("application/pdf", "file.exe", 100)

    def test_file_too_large(self, image_service):
        """Should reject files exceeding size limit."""
        with pytest.raises(FileTooLargeError, match="too large"):
            image_service.validate("image/jpeg", "big.jpg", 2 * 1024 * 1024)

    def test_file_at_exact_limit(self, image_service):
        """Should accept files at exactly the size limit."""
        image_service.validate("image/jpeg", "exact.jpg", 1024 * 1024)

    def test_valid_magic_bytes_pdf(self, doc_service):
        """Should pass when magic bytes match."""
        doc_service.validate("application/pdf", "doc.pdf", 100, b"%PDF-1.7 ...")

    def test_invalid_magic_bytes(self, doc_service):
        """Should reject when magic bytes don't match."""
        with pytest.raises(InvalidMagicBytesError, match="does not match"):
            doc_service.validate("application/pdf", "fake.pdf", 100, b"NOT_A_PDF")

    def test_skip_magic_bytes_when_not_required(self, image_service):
        """Should not check magic bytes when config doesn't require it."""
        # image_service has require_magic_bytes=False
        image_service.validate("image/jpeg", "photo.jpg", 100, b"WHATEVER")

    def test_none_content_type_skips_check(self, image_service):
        """Should skip content type check when None."""
        image_service.validate(None, "photo.jpg", 100)

    def test_none_filename_skips_extension_check(self, doc_service):
        """Should skip extension check when filename is None."""
        doc_service.validate("application/pdf", None, 100)


class TestSave:
    """Tests for the save() method."""

    def test_save_creates_file(self, image_service, tmp_upload_dir):
        """Should write file to disk and return result."""
        data = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # JPEG-like
        result = image_service.save(
            file_data=data,
            content_type="image/jpeg",
            original_filename="test.jpg",
        )

        assert isinstance(result, UploadResult)
        assert result.file_path.exists()
        assert result.file_size == len(data)
        assert result.filename.endswith(".jpg")
        assert result.checksum is None  # Not configured

    def test_save_with_subdirs(self, image_service, tmp_upload_dir):
        """Should create subdirectories."""
        data = b"\x89PNG" + b"\x00" * 50
        result = image_service.save(
            file_data=data,
            content_type="image/png",
            subdirs=("org123", "avatars"),
            original_filename="pic.png",
        )

        assert "org123" in str(result.file_path)
        assert result.file_path.exists()

    def test_save_with_prefix(self, image_service):
        """Should include prefix in filename."""
        data = b"\x89PNG" + b"\x00" * 50
        result = image_service.save(
            file_data=data,
            content_type="image/png",
            prefix="logo",
            original_filename="brand.png",
        )

        assert result.filename.startswith("logo_")

    def test_save_with_checksum(self, doc_service):
        """Should compute SHA-256 checksum when configured."""
        data = b"%PDF-1.4 test content"
        result = doc_service.save(
            file_data=data,
            content_type="application/pdf",
            original_filename="report.pdf",
        )

        assert result.checksum is not None
        assert len(result.checksum) == 64  # SHA-256 hex

    def test_save_rejects_oversized_no_file_written(
        self, image_service, tmp_upload_dir
    ):
        """Should NOT write to disk if file is too large."""
        data = b"\x00" * (2 * 1024 * 1024)  # 2MB, limit is 1MB

        with pytest.raises(FileTooLargeError):
            image_service.save(
                file_data=data,
                content_type="image/jpeg",
                original_filename="huge.jpg",
            )

        # Verify nothing was written
        upload_path = image_service.base_path
        if upload_path.exists():
            files = list(upload_path.rglob("*"))
            assert all(f.is_dir() for f in files), "No files should be written"

    def test_save_generates_unique_filenames(self, image_service):
        """Two saves should produce different filenames."""
        data = b"\x89PNG" + b"\x00" * 50
        r1 = image_service.save(data, "image/png", original_filename="same.png")
        r2 = image_service.save(data, "image/png", original_filename="same.png")

        assert r1.filename != r2.filename


class TestDelete:
    """Tests for delete() and delete_by_url()."""

    def test_delete_existing_file(self, image_service):
        """Should delete an existing file and return True."""
        data = b"\x89PNG" + b"\x00" * 50
        result = image_service.save(data, "image/png", original_filename="del.png")

        assert result.file_path.exists()
        deleted = image_service.delete(result.relative_path)
        assert deleted is True
        assert not result.file_path.exists()

    def test_delete_nonexistent_file(self, image_service):
        """Should return False for nonexistent file."""
        # Ensure base dir exists
        image_service.base_path.mkdir(parents=True, exist_ok=True)
        assert image_service.delete("nonexistent.jpg") is False

    def test_delete_path_traversal(self, image_service):
        """Should raise on path traversal attempt."""
        image_service.base_path.mkdir(parents=True, exist_ok=True)
        with pytest.raises(PathTraversalError):
            image_service.delete("../../etc/passwd")

    def test_delete_by_url(self, image_service):
        """Should delete a file identified by its URL."""
        data = b"\x89PNG" + b"\x00" * 50
        result = image_service.save(data, "image/png", original_filename="url.png")

        url = f"/static/uploads/{result.filename}"
        deleted = image_service.delete_by_url(url, "/static/uploads")
        assert deleted is True
        assert not result.file_path.exists()

    def test_delete_by_url_wrong_prefix(self, image_service):
        """Should return False for non-matching URL prefix."""
        assert (
            image_service.delete_by_url("/other/path/file.png", "/static/uploads")
            is False
        )

    def test_delete_by_url_empty(self, image_service):
        """Should return False for empty URL."""
        assert image_service.delete_by_url("", "/prefix") is False
        assert image_service.delete_by_url(None, "/prefix") is False
