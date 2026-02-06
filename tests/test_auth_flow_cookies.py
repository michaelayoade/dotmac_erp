"""Tests for auth_flow cookie settings - domain/samesite/secure and concurrent refresh."""

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from starlette.requests import Request

from app.models.auth import Session as AuthSession, SessionStatus, UserCredential
from app.models.domain_settings import DomainSetting, SettingDomain
from app.services.auth_flow import (
    AuthFlow,
    _refresh_cookie_domain,
    _refresh_cookie_name,
    _refresh_cookie_path,
    _refresh_cookie_samesite,
    _refresh_cookie_secure,
    hash_password,
)


def _upsert_auth_setting(db_session, key: str, value_text: str):
    setting = (
        db_session.query(DomainSetting)
        .filter(DomainSetting.domain == SettingDomain.auth)
        .filter(DomainSetting.key == key)
        .first()
    )
    if setting:
        setting.value_text = value_text
        setting.is_active = True
    else:
        setting = DomainSetting(
            domain=SettingDomain.auth,
            key=key,
            value_text=value_text,
            is_active=True,
        )
        db_session.add(setting)
    db_session.commit()
    return setting


class TestRefreshCookieSettings:
    """Tests for refresh cookie configuration functions."""

    def test_cookie_name_from_env(self, monkeypatch):
        """Test cookie name from environment variable."""
        monkeypatch.setenv("REFRESH_COOKIE_NAME", "custom_refresh")
        assert _refresh_cookie_name(None) == "custom_refresh"

    def test_cookie_name_default(self, monkeypatch):
        """Test default cookie name."""
        monkeypatch.delenv("REFRESH_COOKIE_NAME", raising=False)
        assert _refresh_cookie_name(None) == "refresh_token"

    def test_cookie_name_from_db(self, db_session, monkeypatch):
        """Test cookie name from database setting."""
        monkeypatch.delenv("REFRESH_COOKIE_NAME", raising=False)
        _upsert_auth_setting(db_session, "refresh_cookie_name", "db_refresh_token")
        assert _refresh_cookie_name(db_session) == "db_refresh_token"

    def test_cookie_secure_true_from_env(self, monkeypatch):
        """Test secure=true from environment variable."""
        for value in ["1", "true", "True", "TRUE", "yes", "on"]:
            monkeypatch.setenv("REFRESH_COOKIE_SECURE", value)
            assert _refresh_cookie_secure(None) is True

    def test_cookie_secure_false_from_env(self, monkeypatch):
        """Test secure=false from environment variable."""
        for value in ["0", "false", "no", "off"]:
            monkeypatch.setenv("REFRESH_COOKIE_SECURE", value)
            assert _refresh_cookie_secure(None) is False

    def test_cookie_secure_default(self, monkeypatch):
        """Test default secure=true (production default)."""
        monkeypatch.delenv("REFRESH_COOKIE_SECURE", raising=False)
        assert _refresh_cookie_secure(None) is True

    def test_cookie_secure_from_db(self, db_session, monkeypatch):
        """Test secure setting from database."""
        monkeypatch.delenv("REFRESH_COOKIE_SECURE", raising=False)
        _upsert_auth_setting(db_session, "refresh_cookie_secure", "true")
        assert _refresh_cookie_secure(db_session) is True

    def test_cookie_samesite_from_env(self, monkeypatch):
        """Test samesite from environment variable."""
        monkeypatch.setenv("REFRESH_COOKIE_SAMESITE", "strict")
        assert _refresh_cookie_samesite(None) == "strict"

    def test_cookie_samesite_default(self, monkeypatch):
        """Test default samesite=strict (production default)."""
        monkeypatch.delenv("REFRESH_COOKIE_SAMESITE", raising=False)
        assert _refresh_cookie_samesite(None) == "strict"

    def test_cookie_samesite_none(self, monkeypatch):
        """Test samesite=none configuration."""
        monkeypatch.setenv("REFRESH_COOKIE_SAMESITE", "none")
        assert _refresh_cookie_samesite(None) == "none"

    def test_cookie_domain_from_env(self, monkeypatch):
        """Test domain from environment variable."""
        monkeypatch.setenv("REFRESH_COOKIE_DOMAIN", ".example.com")
        assert _refresh_cookie_domain(None) == ".example.com"

    def test_cookie_domain_default_none(self, monkeypatch):
        """Test default domain is None."""
        monkeypatch.delenv("REFRESH_COOKIE_DOMAIN", raising=False)
        assert _refresh_cookie_domain(None) is None

    def test_cookie_path_from_env(self, monkeypatch):
        """Test path from environment variable."""
        monkeypatch.setenv("REFRESH_COOKIE_PATH", "/api")
        assert _refresh_cookie_path(None) == "/api"

    def test_cookie_path_default(self, monkeypatch):
        """Test default path=/."""
        monkeypatch.delenv("REFRESH_COOKIE_PATH", raising=False)
        assert _refresh_cookie_path(None) == "/"


class TestRefreshCookieSettingsDict:
    """Tests for the complete cookie settings dictionary."""

    def test_refresh_cookie_settings_complete(self, monkeypatch):
        """Test refresh_cookie_settings returns complete settings."""
        monkeypatch.setenv("REFRESH_COOKIE_NAME", "test_refresh")
        monkeypatch.setenv("REFRESH_COOKIE_SECURE", "true")
        monkeypatch.setenv("REFRESH_COOKIE_SAMESITE", "strict")
        monkeypatch.setenv("REFRESH_COOKIE_DOMAIN", ".test.com")
        monkeypatch.setenv("REFRESH_COOKIE_PATH", "/auth")
        monkeypatch.setenv("JWT_REFRESH_TTL_DAYS", "7")

        settings = AuthFlow.refresh_cookie_settings(None)

        assert settings["key"] == "test_refresh"
        assert settings["httponly"] is True  # Always true
        assert settings["secure"] is True
        assert settings["samesite"] == "strict"
        assert settings["domain"] == ".test.com"
        assert settings["path"] == "/auth"
        assert settings["max_age"] == 7 * 24 * 60 * 60  # 7 days in seconds

    def test_refresh_cookie_settings_defaults(self, monkeypatch):
        """Test refresh_cookie_settings with defaults."""
        # Clear all env vars
        for var in [
            "REFRESH_COOKIE_NAME",
            "REFRESH_COOKIE_SECURE",
            "REFRESH_COOKIE_SAMESITE",
            "REFRESH_COOKIE_DOMAIN",
            "REFRESH_COOKIE_PATH",
            "JWT_REFRESH_TTL_DAYS",
        ]:
            monkeypatch.delenv(var, raising=False)

        settings = AuthFlow.refresh_cookie_settings(None)

        assert settings["key"] == "refresh_token"
        assert settings["httponly"] is True
        assert settings["secure"] is True
        assert settings["samesite"] == "strict"
        assert settings["domain"] is None
        assert settings["path"] == "/"
        assert settings["max_age"] == 30 * 24 * 60 * 60  # 30 days default


class TestConcurrentRefreshRotation:
    """Tests for concurrent refresh token rotation behavior."""

    def _make_request(self, user_agent: str = "pytest"):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/auth/refresh",
            "headers": [(b"user-agent", user_agent.encode("utf-8"))],
            "client": ("127.0.0.1", 12345),
        }
        return Request(scope)

    def test_refresh_token_rotation(self, db_session, person):
        """Test that refresh rotates token and stores previous hash."""
        credential = UserCredential(
            person_id=person.id,
            username=f"rotation_user_{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("password"),
            is_active=True,
        )
        db_session.add(credential)
        db_session.commit()

        request = self._make_request()
        tokens = AuthFlow.login(
            db_session, credential.username, "password", request, None
        )
        original_refresh = tokens["refresh_token"]

        # Perform refresh
        rotated = AuthFlow.refresh(db_session, original_refresh, request)

        assert rotated["refresh_token"] != original_refresh
        assert "access_token" in rotated

        # Check session has previous_token_hash
        session = (
            db_session.query(AuthSession)
            .filter(AuthSession.person_id == person.id)
            .first()
        )
        assert session.previous_token_hash is not None
        assert session.token_rotated_at is not None

    def test_refresh_reuse_detection_revokes_session(self, db_session, person):
        """Test that reusing old refresh token revokes the session."""
        credential = UserCredential(
            person_id=person.id,
            username=f"reuse_user_{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("password"),
            is_active=True,
        )
        db_session.add(credential)
        db_session.commit()

        request = self._make_request()
        tokens = AuthFlow.login(
            db_session, credential.username, "password", request, None
        )
        old_refresh = tokens["refresh_token"]

        # First refresh - should succeed
        AuthFlow.refresh(db_session, old_refresh, request)

        # Second refresh with old token - should fail and revoke
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            AuthFlow.refresh(db_session, old_refresh, request)
        assert exc.value.status_code == 401
        assert "reuse" in exc.value.detail.lower()

        # Session should be revoked
        session = (
            db_session.query(AuthSession)
            .filter(AuthSession.person_id == person.id)
            .first()
        )
        assert session.status == SessionStatus.revoked
        assert session.revoked_at is not None

    def test_concurrent_refresh_first_wins(self, db_session, person):
        """Test concurrent refresh behavior - first request wins."""
        credential = UserCredential(
            person_id=person.id,
            username=f"concurrent_user_{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("password"),
            is_active=True,
        )
        db_session.add(credential)
        db_session.commit()

        request = self._make_request()
        tokens = AuthFlow.login(
            db_session, credential.username, "password", request, None
        )
        shared_refresh = tokens["refresh_token"]

        # Simulate concurrent refresh by using same token twice
        # First request succeeds
        result1 = AuthFlow.refresh(db_session, shared_refresh, request)
        assert "access_token" in result1

        # Second request with same token fails (reuse detection)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            AuthFlow.refresh(db_session, shared_refresh, request)
        assert exc.value.status_code == 401

    def test_refresh_updates_last_seen_and_ip(self, db_session, person):
        """Test that refresh updates last_seen_at and ip_address."""
        credential = UserCredential(
            person_id=person.id,
            username=f"update_user_{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("password"),
            is_active=True,
        )
        db_session.add(credential)
        db_session.commit()

        request1 = self._make_request(user_agent="client1")
        tokens = AuthFlow.login(
            db_session, credential.username, "password", request1, None
        )

        session = (
            db_session.query(AuthSession)
            .filter(AuthSession.person_id == person.id)
            .first()
        )
        # Use naive datetime for SQLite compatibility
        past = datetime(2000, 1, 1)
        session.last_seen_at = past
        db_session.commit()

        request2 = self._make_request(user_agent="client2")
        AuthFlow.refresh(db_session, tokens["refresh_token"], request2)

        db_session.refresh(session)
        assert session.user_agent == "client2"
        # Compare as naive datetime (SQLite returns naive datetimes)
        last_seen = (
            session.last_seen_at.replace(tzinfo=None)
            if session.last_seen_at.tzinfo
            else session.last_seen_at
        )
        assert last_seen > past

    def test_refresh_expired_token_fails(self, db_session, person):
        """Test that expired refresh token fails."""
        credential = UserCredential(
            person_id=person.id,
            username=f"expired_user_{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("password"),
            is_active=True,
        )
        db_session.add(credential)
        db_session.commit()

        request = self._make_request()
        tokens = AuthFlow.login(
            db_session, credential.username, "password", request, None
        )

        # Manually expire the session
        session = (
            db_session.query(AuthSession)
            .filter(AuthSession.person_id == person.id)
            .first()
        )
        session.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db_session.commit()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            AuthFlow.refresh(db_session, tokens["refresh_token"], request)
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail.lower()


class TestRefreshTokenResolution:
    """Tests for refresh token resolution from cookie or body."""

    def test_resolve_refresh_token_from_body(self, monkeypatch):
        """Test resolving refresh token from request body."""
        monkeypatch.delenv("REFRESH_COOKIE_NAME", raising=False)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/auth/refresh",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
        request = Request(scope)
        # Simulate no cookie
        request._cookies = {}

        result = AuthFlow.resolve_refresh_token(request, "body_token", None)
        assert result == "body_token"

    def test_resolve_refresh_token_from_cookie(self, monkeypatch):
        """Test resolving refresh token from cookie when body is None."""
        monkeypatch.setenv("REFRESH_COOKIE_NAME", "my_refresh")

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/auth/refresh",
            "headers": [(b"cookie", b"my_refresh=cookie_token")],
            "client": ("127.0.0.1", 12345),
        }
        request = Request(scope)

        result = AuthFlow.resolve_refresh_token(request, None, None)
        assert result == "cookie_token"

    def test_resolve_refresh_token_body_takes_precedence(self, monkeypatch):
        """Test that body token takes precedence over cookie."""
        monkeypatch.setenv("REFRESH_COOKIE_NAME", "my_refresh")

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/auth/refresh",
            "headers": [(b"cookie", b"my_refresh=cookie_token")],
            "client": ("127.0.0.1", 12345),
        }
        request = Request(scope)

        result = AuthFlow.resolve_refresh_token(request, "body_token", None)
        assert result == "body_token"

    def test_resolve_refresh_token_returns_none(self, monkeypatch):
        """Test returns None when no token available."""
        monkeypatch.delenv("REFRESH_COOKIE_NAME", raising=False)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/auth/refresh",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
        request = Request(scope)
        request._cookies = {}

        result = AuthFlow.resolve_refresh_token(request, None, None)
        assert result is None
