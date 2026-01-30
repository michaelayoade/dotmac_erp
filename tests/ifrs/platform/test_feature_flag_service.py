"""
Tests for FeatureFlagService.
"""

import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.ifrs.platform.conftest import MockColumn, MockSystemConfiguration


@contextmanager
def patch_feature_flag_service():
    """Helper context manager that sets up all required patches for FeatureFlagService."""
    with patch('app.services.finance.platform.feature_flag.SystemConfiguration') as mock_config:
        mock_config.organization_id = MockColumn()
        mock_config.config_key = MockColumn()
        with patch('app.services.finance.platform.feature_flag.and_', return_value=MagicMock()):
            with patch('app.services.finance.platform.feature_flag.or_', return_value=MagicMock()):
                with patch('app.services.finance.platform.feature_flag.coerce_uuid', side_effect=lambda x: x):
                    yield mock_config


class TestFeatureFlagService:
    """Tests for FeatureFlagService."""

    @pytest.fixture
    def service(self):
        """Import the service with mocked dependencies."""
        with patch.dict('sys.modules', {
            'app.models.ifrs.core_config.system_configuration': MagicMock(),
        }):
            from app.services.finance.platform.feature_flag import FeatureFlagService
            return FeatureFlagService

    def test_is_enabled_returns_true_for_org_flag(
        self, service, mock_db_session, organization_id
    ):
        """is_enabled should return True for org-specific enabled flag."""
        org_flag = MockSystemConfiguration(
            organization_id=organization_id,
            config_key="feature.MULTI_CURRENCY",
            config_value="true",
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = org_flag

        with patch('app.services.finance.platform.feature_flag.SystemConfiguration'):
            with patch('app.services.finance.platform.feature_flag.coerce_uuid', side_effect=lambda x: x):
                result = service.is_enabled(
                    mock_db_session,
                    organization_id=organization_id,
                    feature_code="MULTI_CURRENCY",
                )

        assert result is True

    def test_is_enabled_returns_false_for_org_flag(
        self, service, mock_db_session, organization_id
    ):
        """is_enabled should return False for org-specific disabled flag."""
        org_flag = MockSystemConfiguration(
            organization_id=organization_id,
            config_key="feature.ADVANCED_REPORTING",
            config_value="false",
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = org_flag

        with patch('app.services.finance.platform.feature_flag.SystemConfiguration'):
            with patch('app.services.finance.platform.feature_flag.coerce_uuid', side_effect=lambda x: x):
                result = service.is_enabled(
                    mock_db_session,
                    organization_id=organization_id,
                    feature_code="ADVANCED_REPORTING",
                )

        assert result is False

    def test_is_enabled_falls_back_to_system_default(
        self, service, mock_db_session, organization_id
    ):
        """is_enabled should check system default when org flag missing."""
        system_flag = MockSystemConfiguration(
            organization_id=None,
            config_key="feature.NEW_UI",
            config_value="true",
        )
        # First call: org flag (None), second call: system flag
        mock_db_session.query.return_value.filter.return_value.first.side_effect = [
            None,  # No org flag
            system_flag,  # System default
        ]

        with patch_feature_flag_service():
            result = service.is_enabled(
                mock_db_session,
                organization_id=organization_id,
                feature_code="NEW_UI",
            )

        assert result is True

    def test_is_enabled_returns_false_when_not_configured(
        self, service, mock_db_session, organization_id
    ):
        """is_enabled should return False for unconfigured features."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        with patch_feature_flag_service():
            result = service.is_enabled(
                mock_db_session,
                organization_id=organization_id,
                feature_code="UNKNOWN_FEATURE",
            )

        assert result is False

    def test_is_enabled_accepts_various_true_values(
        self, service, mock_db_session, organization_id
    ):
        """is_enabled should accept 'true', '1', 'yes', 'on' as True."""
        for value in ["true", "TRUE", "1", "yes", "YES", "on", "ON"]:
            org_flag = MockSystemConfiguration(
                organization_id=organization_id,
                config_key="feature.TEST",
                config_value=value,
            )
            mock_db_session.query.return_value.filter.return_value.first.return_value = org_flag

            with patch('app.services.finance.platform.feature_flag.SystemConfiguration'):
                with patch('app.services.finance.platform.feature_flag.coerce_uuid', side_effect=lambda x: x):
                    result = service.is_enabled(
                        mock_db_session,
                        organization_id=organization_id,
                        feature_code="TEST",
                    )

            assert result is True, f"Failed for value: {value}"

    def test_get_features_returns_merged_dict(
        self, service, mock_db_session, organization_id
    ):
        """get_features should merge system defaults with org overrides."""
        flags = [
            MockSystemConfiguration(
                organization_id=None,
                config_key="feature.A",
                config_value="true",
            ),
            MockSystemConfiguration(
                organization_id=None,
                config_key="feature.B",
                config_value="true",
            ),
            MockSystemConfiguration(
                organization_id=organization_id,
                config_key="feature.B",
                config_value="false",
            ),
        ]
        mock_db_session.query.return_value.filter.return_value.all.return_value = flags

        with patch_feature_flag_service():
            result = service.get_features(
                mock_db_session,
                organization_id=organization_id,
            )

        assert result["A"] is True
        assert result["B"] is False  # Org override

    def test_set_feature_creates_new_flag(
        self, service, mock_db_session, organization_id, user_id
    ):
        """set_feature should create new flag when not exists."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        with patch('app.services.finance.platform.feature_flag.SystemConfiguration') as MockConfig:
            mock_instance = MagicMock()
            MockConfig.return_value = mock_instance
            with patch('app.services.finance.platform.feature_flag.ConfigType') as MockType:
                MockType.BOOLEAN = "BOOLEAN"
                with patch('app.services.finance.platform.feature_flag.coerce_uuid', side_effect=lambda x: x):
                    service.set_feature(
                        mock_db_session,
                        organization_id=organization_id,
                        feature_code="NEW_FEATURE",
                        enabled=True,
                        updated_by_user_id=user_id,
                    )

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called_once()

    def test_set_feature_updates_existing_flag(
        self, service, mock_db_session, organization_id, user_id
    ):
        """set_feature should update existing flag."""
        existing_flag = MockSystemConfiguration(
            organization_id=organization_id,
            config_key="feature.EXISTING",
            config_value="true",
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = existing_flag

        with patch('app.services.finance.platform.feature_flag.SystemConfiguration'):
            with patch('app.services.finance.platform.feature_flag.coerce_uuid', side_effect=lambda x: x):
                service.set_feature(
                    mock_db_session,
                    organization_id=organization_id,
                    feature_code="EXISTING",
                    enabled=False,
                    updated_by_user_id=user_id,
                )

        assert existing_flag.config_value == "false"
        mock_db_session.add.assert_not_called()
        mock_db_session.commit.assert_called_once()

    def test_set_system_default_creates_new(
        self, service, mock_db_session, user_id
    ):
        """set_system_default should create new system flag."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        with patch('app.services.finance.platform.feature_flag.SystemConfiguration') as MockConfig:
            mock_instance = MagicMock()
            MockConfig.return_value = mock_instance
            MockConfig.organization_id = MockColumn()
            MockConfig.config_key = MockColumn()
            with patch('app.services.finance.platform.feature_flag.ConfigType') as MockType:
                MockType.BOOLEAN = "BOOLEAN"
                with patch('app.services.finance.platform.feature_flag.and_', return_value=MagicMock()):
                    with patch('app.services.finance.platform.feature_flag.coerce_uuid', side_effect=lambda x: x):
                        service.set_system_default(
                            mock_db_session,
                            feature_code="GLOBAL_FEATURE",
                            enabled=True,
                            updated_by_user_id=user_id,
                        )

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called_once()

    def test_set_system_default_updates_existing(
        self, service, mock_db_session, user_id
    ):
        """set_system_default should update existing system flag."""
        existing_flag = MockSystemConfiguration(
            organization_id=None,
            config_key="feature.GLOBAL",
            config_value="false",
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = existing_flag

        with patch_feature_flag_service():
            service.set_system_default(
                mock_db_session,
                feature_code="GLOBAL",
                enabled=True,
                updated_by_user_id=user_id,
            )

        assert existing_flag.config_value == "true"

    def test_require_feature_passes_when_enabled(
        self, service, mock_db_session, organization_id
    ):
        """require_feature should not raise when feature enabled."""
        org_flag = MockSystemConfiguration(
            organization_id=organization_id,
            config_key="feature.REQUIRED",
            config_value="true",
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = org_flag

        with patch('app.services.finance.platform.feature_flag.SystemConfiguration'):
            with patch('app.services.finance.platform.feature_flag.coerce_uuid', side_effect=lambda x: x):
                # Should not raise
                service.require_feature(
                    mock_db_session,
                    organization_id=organization_id,
                    feature_code="REQUIRED",
                )

    def test_require_feature_raises_403_when_disabled(
        self, service, mock_db_session, organization_id
    ):
        """require_feature should raise 403 when feature disabled."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        with patch_feature_flag_service():
            with pytest.raises(HTTPException) as exc_info:
                service.require_feature(
                    mock_db_session,
                    organization_id=organization_id,
                    feature_code="DISABLED_FEATURE",
                )

        assert exc_info.value.status_code == 403
        assert "not enabled" in exc_info.value.detail

    def test_delete_feature_removes_org_flag(
        self, service, mock_db_session, organization_id
    ):
        """delete_feature should remove org-specific flag."""
        existing_flag = MockSystemConfiguration(
            organization_id=organization_id,
            config_key="feature.TO_DELETE",
        )
        mock_db_session.query.return_value.filter.return_value.filter.return_value.first.return_value = existing_flag

        with patch('app.services.finance.platform.feature_flag.SystemConfiguration'):
            with patch('app.services.finance.platform.feature_flag.coerce_uuid', side_effect=lambda x: x):
                result = service.delete_feature(
                    mock_db_session,
                    organization_id=organization_id,
                    feature_code="TO_DELETE",
                )

        assert result is True
        mock_db_session.delete.assert_called_once_with(existing_flag)
        mock_db_session.commit.assert_called_once()

    def test_delete_feature_returns_false_when_not_found(
        self, service, mock_db_session, organization_id
    ):
        """delete_feature should return False when flag not found."""
        mock_db_session.query.return_value.filter.return_value.filter.return_value.first.return_value = None

        with patch('app.services.finance.platform.feature_flag.SystemConfiguration'):
            with patch('app.services.finance.platform.feature_flag.coerce_uuid', side_effect=lambda x: x):
                result = service.delete_feature(
                    mock_db_session,
                    organization_id=organization_id,
                    feature_code="NONEXISTENT",
                )

        assert result is False
        mock_db_session.delete.assert_not_called()

    def test_list_all_flags_returns_feature_configs(
        self, service, mock_db_session, organization_id
    ):
        """list_all_flags should return feature flag configurations."""
        flags = [
            MockSystemConfiguration(
                organization_id=organization_id,
                config_key="feature.A",
            ),
            MockSystemConfiguration(
                organization_id=organization_id,
                config_key="feature.B",
            ),
        ]
        mock_db_session.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = flags

        with patch_feature_flag_service():
            result = service.list_all_flags(
                mock_db_session,
                organization_id=str(organization_id),
                limit=50,
                offset=0,
            )

        assert len(result) == 2

    def test_list_uses_list_all_flags(self, service, mock_db_session, organization_id):
        """list should delegate to list_all_flags."""
        with patch.object(service, 'list_all_flags') as mock_list:
            mock_list.return_value = []
            service.list(
                mock_db_session,
                organization_id=str(organization_id),
                limit=10,
                offset=5,
            )

        mock_list.assert_called_once_with(
            mock_db_session,
            str(organization_id),
            include_system_defaults=True,
            limit=10,
            offset=5,
        )
