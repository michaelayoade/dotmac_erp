"""Tests for avatar service - type validation, size limits, and file cleanup."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile

from app.services import avatar as avatar_service


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
    async def test_save_avatar_within_size_limit(self, tmp_path):
        """Test saving avatar that's within size limit."""
        content = b"x" * 1000  # 1KB file
        file = MagicMock(spec=UploadFile)
        file.content_type = "image/jpeg"
        file.filename = "photo.jpg"
        file.read = AsyncMock(return_value=content)

        with (
            patch.object(avatar_service.settings, "avatar_allowed_types", "image/jpeg"),
            patch.object(avatar_service.settings, "avatar_max_size_bytes", 1024 * 1024),
            patch.object(avatar_service.settings, "avatar_upload_dir", str(tmp_path)),
            patch.object(
                avatar_service.settings, "avatar_url_prefix", "/static/avatars"
            ),
        ):
            url = await avatar_service.save_avatar(file, "person-123")
            assert url.startswith("/static/avatars/")
            assert "person-123" in url

    @pytest.mark.asyncio
    async def test_save_avatar_exceeds_size_limit(self, tmp_path):
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
            patch.object(avatar_service.settings, "avatar_upload_dir", str(tmp_path)),
        ):
            with pytest.raises(HTTPException) as exc:
                await avatar_service.save_avatar(file, "person-123")
            assert exc.value.status_code == 400
            assert "too large" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_save_avatar_creates_directory(self, tmp_path):
        """Test that save_avatar creates upload directory if it doesn't exist."""
        upload_dir = tmp_path / "avatars" / "nested"
        content = b"x" * 100
        file = MagicMock(spec=UploadFile)
        file.content_type = "image/png"
        file.filename = "pic.png"
        file.read = AsyncMock(return_value=content)

        with patch.object(avatar_service.settings, "avatar_allowed_types", "image/png"):
            with patch.object(
                avatar_service.settings, "avatar_max_size_bytes", 1024 * 1024
            ):
                with patch.object(
                    avatar_service.settings, "avatar_upload_dir", str(upload_dir)
                ):
                    with patch.object(
                        avatar_service.settings, "avatar_url_prefix", "/static/avatars"
                    ):
                        await avatar_service.save_avatar(file, "person-456")
                        assert upload_dir.exists()


class TestAvatarFileCleanup:
    """Tests for avatar file deletion and cleanup."""

    def test_delete_avatar_existing_file(self, tmp_path):
        """Test deleting an existing avatar file."""
        # Create a test file
        avatar_file = tmp_path / "test_avatar.jpg"
        avatar_file.write_bytes(b"fake image content")
        assert avatar_file.exists()

        avatar_url = "/static/avatars/test_avatar.jpg"

        with (
            patch.object(
                avatar_service.settings, "avatar_url_prefix", "/static/avatars"
            ),
            patch.object(avatar_service.settings, "avatar_upload_dir", str(tmp_path)),
        ):
            avatar_service.delete_avatar(avatar_url)
            assert not avatar_file.exists()

    def test_delete_avatar_nonexistent_file(self, tmp_path):
        """Test deleting a non-existent avatar file doesn't raise."""
        avatar_url = "/static/avatars/nonexistent.jpg"

        with (
            patch.object(
                avatar_service.settings, "avatar_url_prefix", "/static/avatars"
            ),
            patch.object(avatar_service.settings, "avatar_upload_dir", str(tmp_path)),
        ):
            # Should not raise
            avatar_service.delete_avatar(avatar_url)

    def test_delete_avatar_none_url(self):
        """Test delete_avatar handles None gracefully."""
        # Should not raise
        avatar_service.delete_avatar(None)

    def test_delete_avatar_empty_url(self):
        """Test delete_avatar handles empty string gracefully."""
        # Should not raise
        avatar_service.delete_avatar("")

    def test_delete_avatar_external_url(self, tmp_path):
        """Test that external URLs are not deleted (security)."""
        # Create a file that shouldn't be deleted
        test_file = tmp_path / "should_not_delete.jpg"
        test_file.write_bytes(b"important data")

        external_url = "https://example.com/avatar.jpg"

        with (
            patch.object(
                avatar_service.settings, "avatar_url_prefix", "/static/avatars"
            ),
            patch.object(avatar_service.settings, "avatar_upload_dir", str(tmp_path)),
        ):
            avatar_service.delete_avatar(external_url)
            # File should still exist since external URL was passed
            assert test_file.exists()


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
