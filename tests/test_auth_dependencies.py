"""Tests for auth_dependencies - API key auth, audit scope enforcement, session expiry."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.models.auth import ApiKey, Session as AuthSession, SessionStatus
from app.services import auth as auth_service
from app.services.auth_dependencies import (
    _extract_bearer_token,
    _has_audit_scope,
    _is_jwt,
    _make_aware,
    require_audit_auth,
    require_user_auth,
)


class TestHelperFunctions:
    """Tests for helper functions in auth_dependencies."""

    def test_make_aware_with_naive_datetime(self):
        """Test _make_aware adds UTC timezone to naive datetime."""
        naive = datetime(2024, 1, 1, 12, 0, 0)
        aware = _make_aware(naive)
        assert aware.tzinfo == timezone.utc

    def test_make_aware_with_aware_datetime(self):
        """Test _make_aware preserves timezone on aware datetime."""
        aware_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = _make_aware(aware_dt)
        assert result == aware_dt

    def test_make_aware_with_none(self):
        """Test _make_aware handles None gracefully."""
        result = _make_aware(None)
        assert result is None

    def test_extract_bearer_token_valid(self):
        """Test extracting valid bearer token."""
        token = _extract_bearer_token("Bearer abc123")
        assert token == "abc123"

    def test_extract_bearer_token_lowercase(self):
        """Test extracting bearer token with lowercase prefix."""
        token = _extract_bearer_token("bearer abc123")
        assert token == "abc123"

    def test_extract_bearer_token_none(self):
        """Test extracting from None returns None."""
        assert _extract_bearer_token(None) is None

    def test_extract_bearer_token_no_bearer_prefix(self):
        """Test extracting without Bearer prefix returns None."""
        assert _extract_bearer_token("abc123") is None

    def test_extract_bearer_token_basic_auth(self):
        """Test that Basic auth is not extracted."""
        assert _extract_bearer_token("Basic abc123") is None

    def test_is_jwt_valid_jwt(self):
        """Test _is_jwt identifies valid JWT format."""
        assert _is_jwt("header.payload.signature") is True

    def test_is_jwt_invalid_token(self):
        """Test _is_jwt rejects non-JWT tokens."""
        assert _is_jwt("simple-token") is False
        assert _is_jwt("token.with.too.many.parts") is False
        assert _is_jwt("") is False


class TestHasAuditScope:
    """Tests for audit scope checking."""

    def test_has_audit_scope_with_audit_read(self):
        """Test audit:read scope grants access."""
        payload = {"scopes": ["audit:read", "other:scope"]}
        assert _has_audit_scope(payload) is True

    def test_has_audit_scope_with_audit_wildcard(self):
        """Test audit:* scope grants access."""
        payload = {"scopes": ["audit:*"]}
        assert _has_audit_scope(payload) is True

    def test_has_audit_scope_with_admin_role(self):
        """Test admin role grants audit access."""
        payload = {"roles": ["admin"]}
        assert _has_audit_scope(payload) is True

    def test_has_audit_scope_with_auditor_role(self):
        """Test auditor role grants audit access."""
        payload = {"roles": ["auditor"]}
        assert _has_audit_scope(payload) is True

    def test_has_audit_scope_with_scope_string(self):
        """Test scope as space-separated string."""
        payload = {"scope": "audit:read openid profile"}
        assert _has_audit_scope(payload) is True

    def test_has_audit_scope_with_single_role_string(self):
        """Test single role as string."""
        payload = {"role": "admin"}
        assert _has_audit_scope(payload) is True

    def test_has_audit_scope_without_permission(self):
        """Test without audit permission returns False."""
        payload = {"scopes": ["users:read"], "roles": ["viewer"]}
        assert _has_audit_scope(payload) is False

    def test_has_audit_scope_empty_payload(self):
        """Test empty payload returns False."""
        assert _has_audit_scope({}) is False


class TestRequireAuditAuthWithApiKey:
    """Tests for API key authentication path in require_audit_auth."""

    def test_api_key_auth_valid(self, db_session):
        """Test valid API key authentication."""
        # Create an API key
        raw_key = f"test_api_key_{uuid.uuid4().hex}"
        api_key = ApiKey(
            label="Test API Key",
            key_hash=auth_service.hash_api_key(raw_key),
            is_active=True,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db_session.add(api_key)
        db_session.commit()
        db_session.refresh(api_key)

        request = MagicMock(spec=Request)
        request.state = MagicMock()

        result = require_audit_auth(
            authorization=None,
            x_session_token=None,
            x_api_key=raw_key,
            request=request,
            db=db_session,
        )
        assert result["actor_type"] == "api_key"
        assert result["actor_id"] == str(api_key.id)

    def test_api_key_auth_revoked(self, db_session):
        """Test revoked API key is rejected."""
        raw_key = f"revoked_key_{uuid.uuid4().hex}"
        api_key = ApiKey(
            label="Revoked API Key",
            key_hash=auth_service.hash_api_key(raw_key),
            is_active=True,
            revoked_at=datetime.now(timezone.utc),  # Revoked
        )
        db_session.add(api_key)
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            require_audit_auth(
                authorization=None,
                x_session_token=None,
                x_api_key=raw_key,
                request=None,
                db=db_session,
            )
        assert exc.value.status_code == 401

    def test_api_key_auth_expired(self, db_session):
        """Test expired API key is rejected."""
        raw_key = f"expired_key_{uuid.uuid4().hex}"
        api_key = ApiKey(
            label="Expired API Key",
            key_hash=auth_service.hash_api_key(raw_key),
            is_active=True,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),  # Expired
        )
        db_session.add(api_key)
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            require_audit_auth(
                authorization=None,
                x_session_token=None,
                x_api_key=raw_key,
                request=None,
                db=db_session,
            )
        assert exc.value.status_code == 401

    def test_api_key_auth_inactive(self, db_session):
        """Test inactive API key is rejected."""
        raw_key = f"inactive_key_{uuid.uuid4().hex}"
        api_key = ApiKey(
            label="Inactive API Key",
            key_hash=auth_service.hash_api_key(raw_key),
            is_active=False,  # Inactive
        )
        db_session.add(api_key)
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            require_audit_auth(
                authorization=None,
                x_session_token=None,
                x_api_key=raw_key,
                request=None,
                db=db_session,
            )
        assert exc.value.status_code == 401

    def test_api_key_auth_no_expiry(self, db_session):
        """Test API key without expiry is valid."""
        raw_key = f"no_expiry_key_{uuid.uuid4().hex}"
        api_key = ApiKey(
            label="No Expiry API Key",
            key_hash=auth_service.hash_api_key(raw_key),
            is_active=True,
            expires_at=None,  # No expiry
        )
        db_session.add(api_key)
        db_session.commit()
        db_session.refresh(api_key)

        result = require_audit_auth(
            authorization=None,
            x_session_token=None,
            x_api_key=raw_key,
            request=None,
            db=db_session,
        )
        assert result["actor_type"] == "api_key"


class TestSessionExpiry:
    """Tests for session expiry edge cases."""

    def test_expired_session_token_via_hash_lookup_returns_401(
        self, db_session, person
    ):
        """Test that expired session via hash lookup returns 401."""
        from app.services.auth_flow import hash_session_token

        raw_token = f"expired_token_{uuid.uuid4().hex}"
        session = AuthSession(
            person_id=person.id,
            token_hash=hash_session_token(raw_token),
            status=SessionStatus.active,
            ip_address="127.0.0.1",
            user_agent="test",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),  # Expired
        )
        db_session.add(session)
        db_session.commit()

        # Use a non-JWT token (no dots) to go through session token path
        with pytest.raises(HTTPException) as exc:
            require_audit_auth(
                authorization=None,
                x_session_token=raw_token,
                x_api_key=None,
                request=None,
                db=db_session,
            )
        # Session expired, but query filters it out, so it's treated as invalid
        assert exc.value.status_code == 401

    def test_session_revoked_returns_401(self, db_session, person):
        """Test that revoked session returns 401."""
        from app.services.auth_flow import hash_session_token

        raw_token = f"revoked_token_{uuid.uuid4().hex}"
        session = AuthSession(
            person_id=person.id,
            token_hash=hash_session_token(raw_token),
            status=SessionStatus.revoked,
            revoked_at=datetime.now(timezone.utc),
            ip_address="127.0.0.1",
            user_agent="test",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db_session.add(session)
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            require_audit_auth(
                authorization=None,
                x_session_token=raw_token,
                x_api_key=None,
                request=None,
                db=db_session,
            )
        assert exc.value.status_code == 401

    def test_require_user_auth_session_expired(self, db_session, person):
        """Test require_user_auth rejects expired sessions via query filter."""
        session = AuthSession(
            person_id=person.id,
            token_hash="user-expired-session",
            status=SessionStatus.active,
            ip_address="127.0.0.1",
            user_agent="test",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),  # Expired
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)

        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(person.id),
            "session_id": str(session.id),
            "typ": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        }

        with patch("app.services.auth_dependencies.decode_access_token") as mock_decode:
            mock_decode.return_value = payload
            with pytest.raises(HTTPException) as exc:
                require_user_auth(
                    authorization="Bearer test-token",
                    request=None,
                    db=db_session,
                )
            # require_user_auth uses query filter that excludes expired, returns 401 Unauthorized
            assert exc.value.status_code == 401


class TestAuditScopeEnforcement:
    """Tests for audit scope enforcement in require_audit_auth."""

    def test_insufficient_scope_returns_403(self, db_session, person):
        """Test that insufficient scope returns 403."""
        now = datetime.now(timezone.utc)
        # Payload without audit scopes - no session_id to skip session lookup
        payload = {
            "sub": str(person.id),
            "typ": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "scopes": ["users:read"],  # No audit scope
            "roles": ["viewer"],  # Not admin or auditor
        }

        # Use JWT-formatted token (header.payload.signature) to pass _is_jwt check
        with patch("app.services.auth_dependencies.decode_access_token") as mock_decode:
            mock_decode.return_value = payload
            with pytest.raises(HTTPException) as exc:
                require_audit_auth(
                    authorization="Bearer header.payload.signature",
                    x_session_token=None,
                    x_api_key=None,
                    request=None,
                    db=db_session,
                )
            assert exc.value.status_code == 403
            assert "scope" in exc.value.detail.lower()

    def test_audit_scope_via_jwt_succeeds(self, db_session, person):
        """Test that valid audit scope in JWT succeeds (without session_id)."""
        now = datetime.now(timezone.utc)
        # Payload with audit scope but no session_id - skips session lookup
        payload = {
            "sub": str(person.id),
            "typ": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "scopes": ["audit:read"],
        }

        request = MagicMock(spec=Request)
        request.state = MagicMock()

        # Use JWT-formatted token (header.payload.signature) to pass _is_jwt check
        with patch("app.services.auth_dependencies.decode_access_token") as mock_decode:
            mock_decode.return_value = payload
            result = require_audit_auth(
                authorization="Bearer header.payload.signature",
                x_session_token=None,
                x_api_key=None,
                request=request,
                db=db_session,
            )
            assert result["actor_type"] == "user"
            assert result["actor_id"] == str(person.id)


class TestSessionTokenAuth:
    """Tests for session token authentication (non-JWT)."""

    def test_session_token_valid(self, db_session, person):
        """Test valid session token authentication."""
        raw_token = f"session_{uuid.uuid4().hex}"
        from app.services.auth_flow import hash_session_token

        session = AuthSession(
            person_id=person.id,
            token_hash=hash_session_token(raw_token),
            status=SessionStatus.active,
            ip_address="127.0.0.1",
            user_agent="test",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db_session.add(session)
        db_session.commit()

        request = MagicMock(spec=Request)
        request.state = MagicMock()

        # Non-JWT token (not in header.payload.signature format)
        result = require_audit_auth(
            authorization=None,
            x_session_token=raw_token,
            x_api_key=None,
            request=request,
            db=db_session,
        )
        assert result["actor_type"] == "user"
        assert result["actor_id"] == str(person.id)

    def test_no_auth_provided_returns_401(self, db_session):
        """Test that no authentication returns 401."""
        with pytest.raises(HTTPException) as exc:
            require_audit_auth(
                authorization=None,
                x_session_token=None,
                x_api_key=None,
                request=None,
                db=db_session,
            )
        assert exc.value.status_code == 401
