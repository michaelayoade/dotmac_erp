"""Tests for audit middleware - read-triggers, skip-paths, and exception logging."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.main import (
    _is_audit_path_skipped,
    _load_audit_settings,
    _to_bool,
    _to_list,
    _to_str,
    audit_middleware,
)
from app.models.domain_settings import DomainSetting, SettingDomain


class TestAuditPathSkipping:
    """Tests for audit path skipping logic."""

    def test_skip_static_path(self):
        """Test that /static paths are skipped."""
        skip_paths = ["/static", "/web", "/health"]
        assert _is_audit_path_skipped("/static/css/style.css", skip_paths) is True

    def test_skip_web_path(self):
        """Test that /web paths are skipped."""
        skip_paths = ["/static", "/web", "/health"]
        assert _is_audit_path_skipped("/web/dashboard", skip_paths) is True

    def test_skip_health_path(self):
        """Test that /health paths are skipped."""
        skip_paths = ["/static", "/web", "/health"]
        assert _is_audit_path_skipped("/health", skip_paths) is True

    def test_no_skip_api_path(self):
        """Test that API paths are not skipped."""
        skip_paths = ["/static", "/web", "/health"]
        assert _is_audit_path_skipped("/api/v1/users", skip_paths) is False

    def test_no_skip_auth_path(self):
        """Test that /auth paths are not skipped."""
        skip_paths = ["/static", "/web", "/health"]
        assert _is_audit_path_skipped("/auth/login", skip_paths) is False

    def test_empty_skip_paths(self):
        """Test with empty skip paths list."""
        assert _is_audit_path_skipped("/any/path", []) is False

    def test_prefix_match(self):
        """Test prefix-based path matching (startswith)."""
        skip_paths = ["/health"]
        assert _is_audit_path_skipped("/health", skip_paths) is True
        # Note: startswith matching means /healthy also matches /health prefix
        assert _is_audit_path_skipped("/healthy", skip_paths) is True
        assert _is_audit_path_skipped("/api/health", skip_paths) is False


class TestAuditSettingsConversion:
    """Tests for audit settings conversion functions."""

    def test_to_bool_true_values(self, db_session):
        """Test _to_bool with various true values."""
        # Test with value_json as bool
        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_bool",
            value_json=True,
        )
        assert _to_bool(setting) is True

        # Test with value_text as "true"
        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_bool",
            value_text="true",
        )
        assert _to_bool(setting) is True

        # Test with value_text as "1"
        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_bool",
            value_text="1",
        )
        assert _to_bool(setting) is True

        # Test with value_text as "yes"
        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_bool",
            value_text="yes",
        )
        assert _to_bool(setting) is True

        # Test with value_text as "on"
        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_bool",
            value_text="on",
        )
        assert _to_bool(setting) is True

    def test_to_bool_false_values(self, db_session):
        """Test _to_bool with various false values."""
        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_bool",
            value_json=False,
        )
        assert _to_bool(setting) is False

        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_bool",
            value_text="false",
        )
        assert _to_bool(setting) is False

        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_bool",
            value_text="0",
        )
        assert _to_bool(setting) is False

    def test_to_str_from_text(self):
        """Test _to_str with value_text."""
        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_str",
            value_text="x-custom-header",
        )
        assert _to_str(setting) == "x-custom-header"

    def test_to_str_from_json(self):
        """Test _to_str with value_json."""
        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_str",
            value_json="json-value",
        )
        assert _to_str(setting) == "json-value"

    def test_to_str_none_returns_empty(self):
        """Test _to_str with None values returns empty string."""
        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_str",
            value_text=None,
            value_json=None,
        )
        assert _to_str(setting) == ""

    def test_to_list_from_json_array(self):
        """Test _to_list with JSON array."""
        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_list",
            value_json=["POST", "PUT", "DELETE"],
        )
        result = _to_list(setting, upper=True)
        assert result == {"POST", "PUT", "DELETE"}

    def test_to_list_from_csv_string(self):
        """Test _to_list with comma-separated string."""
        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_list",
            value_text="post,put,delete",
        )
        result = _to_list(setting, upper=True)
        assert result == {"POST", "PUT", "DELETE"}

    def test_to_list_with_spaces(self):
        """Test _to_list trims whitespace."""
        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_list",
            value_text=" POST , PUT , DELETE ",
        )
        result = _to_list(setting, upper=True)
        assert result == {"POST", "PUT", "DELETE"}

    def test_to_list_no_upper(self):
        """Test _to_list without uppercasing."""
        setting = DomainSetting(
            domain=SettingDomain.audit,
            key="test_list",
            value_json=["/static", "/web"],
        )
        result = _to_list(setting, upper=False)
        assert result == ["/static", "/web"]


class TestAuditSettingsLoading:
    """Tests for loading audit settings from database."""

    def test_load_audit_settings_returns_expected_keys(self, db_session):
        """Test that load_audit_settings returns all expected keys."""
        import app.main as main_module

        main_module._AUDIT_SETTINGS_CACHE = None
        main_module._AUDIT_SETTINGS_CACHE_AT = None

        settings = _load_audit_settings(db_session)

        # Verify all expected keys are present
        assert "enabled" in settings
        assert "methods" in settings
        assert "skip_paths" in settings
        assert "read_trigger_header" in settings
        assert "read_trigger_query" in settings
        # Verify types
        assert isinstance(settings["enabled"], bool)
        assert isinstance(settings["methods"], set)
        assert isinstance(settings["skip_paths"], list)


class TestAuditMiddlewareReadTriggers:
    """Tests for audit middleware read trigger behavior."""

    @pytest.mark.asyncio
    async def test_get_request_not_logged_without_trigger(self):
        """Test that GET requests are not logged without read trigger."""
        request = MagicMock(spec=Request)
        request.url.path = "/api/v1/users"
        request.method = "GET"
        request.headers = MagicMock()
        request.headers.get.return_value = None
        request.query_params = {}

        response = Response(status_code=200)
        call_next = AsyncMock(return_value=response)

        audit_settings = {
            "enabled": True,
            "methods": {"POST", "PUT", "PATCH", "DELETE"},
            "skip_paths": ["/static"],
            "read_trigger_header": "x-audit-read",
            "read_trigger_query": "audit",
        }

        with patch("app.main._load_audit_settings", return_value=audit_settings):
            with patch("app.main.SessionLocal") as mock_session:
                mock_db = MagicMock()
                mock_session.return_value = mock_db
                with patch("app.main.audit_service") as mock_audit:
                    result = await audit_middleware(request, call_next)
                    # GET without trigger should not log
                    mock_audit.audit_events.log_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_request_logged_with_header_trigger(self):
        """Test that GET requests are logged with header trigger."""
        request = MagicMock(spec=Request)
        request.url.path = "/api/v1/users"
        request.method = "GET"
        request.headers = MagicMock()
        request.headers.get.side_effect = lambda h, default="": (
            "true" if h == "x-audit-read" else default
        )
        request.query_params = {}

        response = Response(status_code=200)
        call_next = AsyncMock(return_value=response)

        audit_settings = {
            "enabled": True,
            "methods": {"POST", "PUT", "PATCH", "DELETE"},
            "skip_paths": ["/static"],
            "read_trigger_header": "x-audit-read",
            "read_trigger_query": "audit",
        }

        with patch("app.main._load_audit_settings", return_value=audit_settings):
            with patch("app.main.SessionLocal") as mock_session:
                mock_db = MagicMock()
                mock_session.return_value = mock_db
                with patch("app.main.audit_service") as mock_audit:
                    result = await audit_middleware(request, call_next)
                    # GET with header trigger should log
                    mock_audit.audit_events.log_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_request_logged_with_query_trigger(self):
        """Test that GET requests are logged with query parameter trigger."""
        request = MagicMock(spec=Request)
        request.url.path = "/api/v1/users"
        request.method = "GET"
        request.headers = MagicMock()
        request.headers.get.return_value = ""
        request.query_params = {"audit": "true"}

        response = Response(status_code=200)
        call_next = AsyncMock(return_value=response)

        audit_settings = {
            "enabled": True,
            "methods": {"POST", "PUT", "PATCH", "DELETE"},
            "skip_paths": ["/static"],
            "read_trigger_header": "x-audit-read",
            "read_trigger_query": "audit",
        }

        with patch("app.main._load_audit_settings", return_value=audit_settings):
            with patch("app.main.SessionLocal") as mock_session:
                mock_db = MagicMock()
                mock_session.return_value = mock_db
                with patch("app.main.audit_service") as mock_audit:
                    result = await audit_middleware(request, call_next)
                    mock_audit.audit_events.log_request.assert_called_once()


class TestAuditMiddlewareExceptionLogging:
    """Tests for audit middleware exception logging."""

    @pytest.mark.asyncio
    async def test_exception_logged_for_post_request(self):
        """Test that exceptions are logged for POST requests."""
        request = MagicMock(spec=Request)
        request.url.path = "/api/v1/users"
        request.method = "POST"
        request.headers = MagicMock()
        request.headers.get.return_value = None
        request.query_params = {}

        call_next = AsyncMock(side_effect=Exception("Internal error"))

        audit_settings = {
            "enabled": True,
            "methods": {"POST", "PUT", "PATCH", "DELETE"},
            "skip_paths": ["/static"],
            "read_trigger_header": "x-audit-read",
            "read_trigger_query": "audit",
        }

        with patch("app.main._load_audit_settings", return_value=audit_settings):
            with patch("app.main.SessionLocal") as mock_session:
                mock_db = MagicMock()
                mock_session.return_value = mock_db
                with patch("app.main.audit_service") as mock_audit:
                    with pytest.raises(Exception):
                        await audit_middleware(request, call_next)
                    # Exception should be logged with 500 status
                    mock_audit.audit_events.log_request.assert_called_once()
                    call_args = mock_audit.audit_events.log_request.call_args
                    assert call_args[0][2].status_code == 500

    @pytest.mark.asyncio
    async def test_exception_not_logged_for_skipped_path(self):
        """Test that exceptions are not logged for skipped paths."""
        request = MagicMock(spec=Request)
        request.url.path = "/static/js/app.js"
        request.method = "GET"
        request.headers = MagicMock()
        request.headers.get.return_value = None
        request.query_params = {}

        call_next = AsyncMock(side_effect=Exception("Static file error"))

        audit_settings = {
            "enabled": True,
            "methods": {"POST", "PUT", "PATCH", "DELETE"},
            "skip_paths": ["/static"],
            "read_trigger_header": "x-audit-read",
            "read_trigger_query": "audit",
        }

        with patch("app.main._load_audit_settings", return_value=audit_settings):
            with patch("app.main.SessionLocal") as mock_session:
                mock_db = MagicMock()
                mock_session.return_value = mock_db
                with patch("app.main.audit_service") as mock_audit:
                    with pytest.raises(Exception):
                        await audit_middleware(request, call_next)
                    # Skipped path should not log
                    mock_audit.audit_events.log_request.assert_not_called()


class TestAuditMiddlewareDisabled:
    """Tests for disabled audit middleware."""

    @pytest.mark.asyncio
    async def test_disabled_audit_skips_logging(self):
        """Test that disabled audit middleware skips all logging."""
        request = MagicMock(spec=Request)
        request.url.path = "/api/v1/users"
        request.method = "POST"

        response = Response(status_code=201)
        call_next = AsyncMock(return_value=response)

        audit_settings = {
            "enabled": False,  # Disabled
            "methods": {"POST", "PUT", "PATCH", "DELETE"},
            "skip_paths": ["/static"],
            "read_trigger_header": "x-audit-read",
            "read_trigger_query": "audit",
        }

        with patch("app.main._load_audit_settings", return_value=audit_settings):
            with patch("app.main.SessionLocal") as mock_session:
                mock_db = MagicMock()
                mock_session.return_value = mock_db
                with patch("app.main.audit_service") as mock_audit:
                    result = await audit_middleware(request, call_next)
                    mock_audit.audit_events.log_request.assert_not_called()
