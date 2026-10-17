"""Tests for avatar service - type validation, size limits, and file cleanup.

Avatars are platform-plane ``dotmac-files`` records since the files
cutover: save stages through the module, delete retires by file id, and
pre-cutover filename URLs still delete through the legacy object path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile

from app.services import avatar as avatar_service


@pytest.fixture(autouse=True)
def _mock_storage():
    """Mock the legacy upload storage used only for pre-cutover deletes."""
    mock = MagicMock()
    with patch(
        "app.services.file_upload.FileUploadService._get_storage",
        return_value=mock,
    ):
        yield mock


def _prepared():
    prepared = MagicMock()
    prepared.id = "12345678-1234-5678-1234-567812345678"
    return prepared


class TestAvatarValidation:
    """Tests for avatar file type validation."""

    def test_get_allowed_types(self):
        """Test getting allowed avatar types from settings."""
        with patch.object(
            avatar_service.settings,
            "avatar_allowed_types",
            "image/jpeg,image/png,image/gif",
        ):
            allowed = avatar_service.get_allowed_types()
            assert "image/jpeg" in allowed
            assert "image/png" in allowed
            assert "image/gif" in allowed
            assert len(allowed) == 3

    def test_validate_avatar_valid_type(self):
        """Test validation passes for allowed content type."""
        with patch.object(
            avatar_service.settings,
            "avatar_allowed_types",
            "image/jpeg,image/png",
        ):
            file = MagicMock(spec=UploadFile)
            file.content_type = "image/jpeg"
            # Should not raise
            avatar_service.validate_avatar(file)

    def test_validate_avatar_invalid_type(self):
        """Test validation fails for disallowed content type."""
        with patch.object(
            avatar_service.settings,
            "avatar_allowed_types",
            "image/jpeg,image/png",
        ):
            file = MagicMock(spec=UploadFile)
            file.content_type = "application/pdf"
            with pytest.raises(HTTPException) as exc:
                avatar_service.validate_avatar(file)
            assert exc.value.status_code == 400
            assert "Invalid file type" in exc.value.detail

    def test_validate_avatar_svg_blocked(self):
        """Test that SVG files are blocked (security risk)."""
        with patch.object(
            avatar_service.settings,
            "avatar_allowed_types",
            "image/jpeg,image/png",
        ):
            file = MagicMock(spec=UploadFile)
            file.content_type = "image/svg+xml"
            with pytest.raises(HTTPException) as exc:
                avatar_service.validate_avatar(file)
            assert exc.value.status_code == 400


class TestAvatarSizeLimits:
    """Tests for avatar file size validation."""

    @pytest.mark.asyncio
    async def test_save_avatar_within_size_limit(self):
        """Test saving avatar that's within size limit stages a file record."""
        content = b"x" * 1000  # 1KB file
        file = MagicMock(spec=UploadFile)
        file.content_type = "image/jpeg"
        file.filename = "photo.jpg"
        file.read = AsyncMock(return_value=content)
        db = MagicMock()

        with (
            patch.object(avatar_service.settings, "avatar_allowed_types", "image/jpeg"),
            patch.object(avatar_service.settings, "avatar_max_size_bytes", 1024 * 1024),
            patch.object(
                avatar_service, "get_dotmac_files_provider", return_value=MagicMock()
            ),
            patch.object(avatar_service, "prepare_upload", return_value=_prepared()),
            patch.object(avatar_service, "stage_platform_file") as stage,
        ):
            url = await avatar_service.save_avatar(file, "person-123", db)
            assert url == "/files/avatars/12345678-1234-5678-1234-567812345678"
            stage.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_avatar_exceeds_size_limit(self):
        """Test saving avatar that exceeds size limit."""
        content = b"x" * (3 * 1024 * 1024)  # 3MB file
        file = MagicMock(spec=UploadFile)
        file.content_type = "image/jpeg"
        file.filename = "big.jpg"
        file.read = AsyncMock(return_value=content)

        with (
            patch.object(avatar_service.settings, "avatar_allowed_types", "image/jpeg"),
            patch.object(
                avatar_service.settings, "avatar_max_size_bytes", 2 * 1024 * 1024
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await avatar_service.save_avatar(file, "person-123", MagicMock())
            assert exc.value.status_code == 400
            assert "too large" in exc.value.detail.lower()


class TestAvatarFileCleanup:
    """Tests for avatar file deletion and cleanup."""

    def test_delete_avatar_by_file_id_requests_deletion(self):
        db = MagicMock()
        with patch.object(avatar_service, "request_deletion") as retire:
            avatar_service.delete_avatar(
                "/files/avatars/12345678-1234-5678-1234-567812345678", db
            )
            retire.assert_called_once()

    def test_delete_avatar_via_new_url(self, _mock_storage):
        """Test deleting a pre-cutover avatar via /files/avatars/ filename."""
        avatar_url = "/files/avatars/test_avatar.jpg"

        with patch.object(
            avatar_service.settings, "avatar_url_prefix", "/static/avatars"
        ):
            avatar_service.delete_avatar(avatar_url, MagicMock())
            _mock_storage.delete.assert_called_once()

    def test_delete_avatar_via_legacy_url(self, _mock_storage):
        """Test deleting an avatar via legacy /static/avatars/ URL."""
        avatar_url = "/static/avatars/test_avatar.jpg"

        with patch.object(
            avatar_service.settings, "avatar_url_prefix", "/static/avatars"
        ):
            avatar_service.delete_avatar(avatar_url, MagicMock())
            _mock_storage.delete.assert_called_once()

    def test_delete_avatar_none_url(self, _mock_storage):
        """Test delete_avatar handles None gracefully."""
        avatar_service.delete_avatar(None)
        _mock_storage.delete.assert_not_called()

    def test_delete_avatar_empty_url(self, _mock_storage):
        """Test delete_avatar handles empty string gracefully."""
        avatar_service.delete_avatar("")
        _mock_storage.delete.assert_not_called()

    def test_delete_avatar_external_url(self, _mock_storage):
        """Test that external URLs are not deleted (security)."""
        external_url = "https://example.com/avatar.jpg"

        with patch.object(
            avatar_service.settings, "avatar_url_prefix", "/static/avatars"
        ):
            avatar_service.delete_avatar(external_url)
            _mock_storage.delete.assert_not_called()


class TestAvatarExtensions:
    """Tests for file extension mapping."""

    def test_get_extension_jpeg(self):
        """Test extension for JPEG content type."""
        assert avatar_service._get_extension("image/jpeg") == ".jpg"

    def test_get_extension_png(self):
        """Test extension for PNG content type."""
        assert avatar_service._get_extension("image/png") == ".png"

    def test_get_extension_gif(self):
        """Test extension for GIF content type."""
        assert avatar_service._get_extension("image/gif") == ".gif"

    def test_get_extension_webp(self):
        """Test extension for WebP content type."""
        assert avatar_service._get_extension("image/webp") == ".webp"

    def test_get_extension_unknown_defaults_to_jpg(self):
        """Test that unknown content types default to .jpg."""
        assert avatar_service._get_extension("image/unknown") == ".jpg"
        assert avatar_service._get_extension("application/octet-stream") == ".jpg"
