from datetime import datetime, timedelta, timezone
import uuid

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import urlparse

from starlette.requests import Request

from app.models.auth import Session as AuthSession
from app.models.auth import SessionStatus
from app.services.auth_flow import AuthFlow, hash_session_token
from app.services.auth_web import auth_web_service
from app.web.deps import require_web_auth


def test_web_session_person_lookup_uses_cross_org_without_default_org(monkeypatch):
    import app.web.deps as web_deps

    person_id = uuid.uuid4()
    db = MagicMock()
    db.info = {}
    person = SimpleNamespace(id=person_id)
    events = []

    class FakeCrossOrg:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, exc_type, exc, tb):
            events.append("exit")

    monkeypatch.setattr(web_deps.settings, "default_organization_id", None)
    monkeypatch.setattr(web_deps, "allow_cross_org", lambda session: FakeCrossOrg())
    db.get.return_value = person

    assert web_deps._get_person_for_web_session(db, person_id) is person
    db.get.assert_called_once_with(web_deps.Person, person_id)
    assert events == ["enter", "exit"]


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


def test_login_keeps_absolute_same_host_next_and_drops_a_foreign_one():
    """Redirect sanitization is host-scoped, not scheme-scoped.

    This replaces the OIDC-specific version of the same assertion, deleted with
    the unshipped OIDC implementation. ``_sanitize_redirect_url`` is the
    surviving owner of the open-redirect defence and keeps its coverage here.
    """
    auth = SimpleNamespace(is_authenticated=False, roles=[])

    same_host = "http://testserver/finance/dashboard?tab=summary"
    kept = auth_web_service.login_response(_request("/login"), same_host, auth, db=None)
    assert kept.context["next"] == same_host

    foreign = auth_web_service.login_response(
        _request("/login"), "https://evil.example.com/steal", auth, db=None
    )
    assert foreign.context["next"] == "/"
    assert urlparse(foreign.context["next"]).netloc == ""


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


def test_login_response_sets_no_cache_headers():
    request = _request("/login")
    auth = SimpleNamespace(is_authenticated=False, roles=[])

    response = auth_web_service.login_response(request, "/", auth, db=None)

    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Vary"] == "Cookie"


def test_admin_login_response_sets_no_cache_headers():
    request = _request("/admin/login")
    auth = SimpleNamespace(is_authenticated=False, roles=[])

    response = auth_web_service.admin_login_response(request, "/admin", auth, db=None)

    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Vary"] == "Cookie"


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
