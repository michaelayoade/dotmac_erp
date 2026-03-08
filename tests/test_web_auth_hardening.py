from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from starlette.requests import Request

from app.models.auth import Session as AuthSession
from app.models.auth import SessionStatus
from app.services.auth_flow import AuthFlow, hash_session_token
from app.services.auth_web import auth_web_service
from app.web.deps import require_web_auth


def _request(path: str, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": headers or [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def test_sso_login_url_keeps_absolute_safe_next(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "sso_enabled", True, raising=False)
    monkeypatch.setattr(settings, "sso_provider_mode", False, raising=False)
    monkeypatch.setattr(
        settings, "sso_provider_url", "https://sso.example.com", raising=False
    )

    next_url = "https://testserver/finance/dashboard?tab=summary"
    sso_url = auth_web_service._get_sso_login_url(_request("/login"), next_url)
    assert sso_url is not None

    parsed = urlparse(sso_url)
    assert parsed.netloc == "sso.example.com"
    assert parsed.path.endswith("/login")

    next_params = parse_qs(parsed.query).get("next", [])
    assert next_params == [next_url]


def test_logout_response_uses_cookie_defaults_when_settings_lookup_fails(monkeypatch):
    request = _request(
        "/logout", headers=[(b"cookie", b"refresh_token=test-refresh-token")]
    )

    def _raise_error(_db=None):
        raise RuntimeError("settings lookup failed")

    monkeypatch.setattr(AuthFlow, "refresh_cookie_settings", staticmethod(_raise_error))
    monkeypatch.setattr(AuthFlow, "access_cookie_settings", staticmethod(_raise_error))

    response = auth_web_service.logout_response(request, "/login")
    assert response.status_code == 302
    assert response.headers["location"] == "/login"

    set_cookie_headers = [
        value.decode("latin-1")
        for key, value in response.raw_headers
        if key.lower() == b"set-cookie"
    ]
    assert any(header.startswith("access_token=") for header in set_cookie_headers)
    assert any(header.startswith("refresh_token=") for header in set_cookie_headers)


def test_require_web_auth_accepts_valid_refresh_cookie_when_access_token_invalid(
    db_session, person
):
    refresh_token = "refresh-token-for-web-auth"
    session = AuthSession(
        person_id=person.id,
        token_hash=hash_session_token(refresh_token),
        status=SessionStatus.active,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db_session.add(session)
    db_session.commit()

    request = _request(
        "/finance/dashboard",
        headers=[
            (b"authorization", b"Bearer invalid-access-token"),
            (b"cookie", f"refresh_token={refresh_token}".encode()),
        ],
    )

    auth = require_web_auth(
        request=request,
        authorization="Bearer invalid-access-token",
        db=db_session,
    )
    assert auth.is_authenticated is True
    assert auth.person_id == person.id
