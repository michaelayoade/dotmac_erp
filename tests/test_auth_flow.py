import uuid
from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from jose import jwt
from starlette.requests import Request

from app.models.auth import Session as AuthSession, SessionStatus, UserCredential
from app.models.auth import AuthProvider
from app.services.auth_flow import AuthFlow, decode_access_token, hash_password
from tests.mocks import FakeHTTPXResponse


def _unique_username() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.com"


def _make_request(user_agent: str = "pytest"):
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/auth",
        "headers": [(b"user-agent", user_agent.encode("utf-8"))],
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def test_login_and_refresh_reuse_detection(db_session, person, monkeypatch):
    username = _unique_username()
    credential = UserCredential(
        person_id=person.id,
        provider=AuthProvider.local,
        username=username,
        password_hash=hash_password("secret"),
        is_active=True,
    )
    db_session.add(credential)
    db_session.commit()
    db_session.refresh(credential)

    request = _make_request()
    tokens = AuthFlow.login(db_session, username, "secret", request, None)
    old_refresh = tokens["refresh_token"]

    rotated = AuthFlow.refresh(db_session, old_refresh, request)
    assert rotated["refresh_token"] != old_refresh

    with pytest.raises(HTTPException) as exc:
        AuthFlow.refresh(db_session, old_refresh, request)
    assert exc.value.status_code == 401
    assert "reuse" in str(exc.value.detail).lower()

    session = (
        db_session.query(AuthSession).filter(AuthSession.person_id == person.id).first()
    )
    assert session.status == SessionStatus.revoked
    assert session.revoked_at is not None


def test_mfa_setup_confirm(db_session, person, monkeypatch):
    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", key)
    monkeypatch.setenv("TOTP_ISSUER", "StarterTemplate")

    credential = UserCredential(
        person_id=person.id,
        provider=AuthProvider.local,
        username=_unique_username(),
        password_hash=hash_password("secret"),
        is_active=True,
    )
    db_session.add(credential)
    db_session.commit()

    setup = AuthFlow.mfa_setup(db_session, str(person.id), label="device")
    code = pyotp.TOTP(setup["secret"]).now()
    method = AuthFlow.mfa_confirm(db_session, str(setup["method_id"]), code)

    assert method.enabled is True
    assert method.is_primary is True
    assert method.is_active is True
    assert method.verified_at is not None


def test_decode_access_token_uses_openbao_secret(monkeypatch):
    secret_value = "openbao-secret"
    monkeypatch.setenv("JWT_SECRET", "openbao://secret/data/app#jwt_secret")
    monkeypatch.setenv("OPENBAO_ADDR", "https://bao.local:8200")
    monkeypatch.setenv("OPENBAO_TOKEN", "test-token")
    monkeypatch.setenv("OPENBAO_KV_VERSION", "2")

    mock_response = FakeHTTPXResponse(
        json_data={"data": {"data": {"jwt_secret": secret_value}}}
    )

    def mock_get(url, **kwargs):
        return mock_response

    import httpx

    monkeypatch.setattr(httpx, "get", mock_get)

    now = datetime.now(timezone.utc)
    payload = {
        "sub": "user-id",
        "session_id": "session-id",
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    token = jwt.encode(payload, secret_value, algorithm="HS256")
    decoded = decode_access_token(None, token)

    assert decoded["sub"] == "user-id"
    assert decoded["typ"] == "access"
