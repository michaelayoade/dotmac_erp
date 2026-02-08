"""Tests for app configuration module."""

import os
from unittest.mock import patch

import pytest


class TestSettings:
    """Tests for Settings dataclass."""

    def test_default_database_url(self):
        """Test default database URL when env var not set."""
        # Clear DATABASE_URL from environment for this test
        with patch.dict(os.environ, {}, clear=True):
            # Re-import to get fresh settings
            import importlib

            import app.config as config_module

            importlib.reload(config_module)

            # Should use default value
            assert "postgresql" in config_module.settings.database_url

    def test_default_pool_settings(self):
        """Test default pool settings."""
        from app.config import settings

        # These should have reasonable defaults
        assert settings.db_pool_size >= 1
        assert settings.db_max_overflow >= 0
        assert settings.db_pool_timeout > 0
        assert settings.db_pool_recycle > 0

    def test_avatar_settings(self):
        """Test avatar configuration defaults."""
        from app.config import settings

        assert settings.avatar_upload_dir == "static/avatars"
        assert settings.avatar_max_size_bytes == 2 * 1024 * 1024  # 2MB
        assert "image/jpeg" in settings.avatar_allowed_types
        assert "image/png" in settings.avatar_allowed_types

    def test_branding_settings(self):
        """Test branding configuration defaults."""
        from app.config import settings

        assert settings.brand_name is not None
        assert len(settings.brand_name) > 0
        # brand_logo_url can be None

    def test_settings_immutable(self):
        """Test that settings are frozen/immutable."""
        from app.config import settings

        with pytest.raises(Exception):  # FrozenInstanceError
            settings.database_url = "new_url"

    def test_custom_database_url_from_env(self):
        """Test that DATABASE_URL env var is respected."""
        custom_url = "postgresql://custom:custom@localhost:5432/custom_db"

        with patch.dict(os.environ, {"DATABASE_URL": custom_url}):
            import importlib

            import app.config as config_module

            importlib.reload(config_module)

            assert config_module.settings.database_url == custom_url

    def test_custom_pool_size_from_env(self):
        """Test that DB_POOL_SIZE env var is respected."""
        with patch.dict(os.environ, {"DB_POOL_SIZE": "20"}):
            import importlib

            import app.config as config_module

            importlib.reload(config_module)

            assert config_module.settings.db_pool_size == 20

    def test_custom_avatar_settings_from_env(self):
        """Test that avatar env vars are respected."""
        with patch.dict(
            os.environ,
            {
                "AVATAR_UPLOAD_DIR": "/custom/path",
                "AVATAR_MAX_SIZE_BYTES": "5242880",
            },
        ):
            import importlib

            import app.config as config_module

            importlib.reload(config_module)

            assert config_module.settings.avatar_upload_dir == "/custom/path"
            assert config_module.settings.avatar_max_size_bytes == 5242880

    def test_custom_brand_settings_from_env(self):
        """Test that brand env vars are respected."""
        with patch.dict(
            os.environ,
            {
                "BRAND_NAME": "Custom Brand",
                "BRAND_TAGLINE": "Custom tagline",
                "BRAND_LOGO_URL": "https://example.com/logo.png",
            },
        ):
            import importlib

            import app.config as config_module

            importlib.reload(config_module)

            assert config_module.settings.brand_name == "Custom Brand"
            assert config_module.settings.brand_tagline == "Custom tagline"
            assert (
                config_module.settings.brand_logo_url == "https://example.com/logo.png"
            )
