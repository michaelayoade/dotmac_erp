import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.models.auth import FederatedIdentity
from app.services.sso.oidc import OIDCClient, OIDCMetadata


def _request(path: str = "/login") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("erp.example.com", 443),
            "scheme": "https",
        }
    )


@pytest.fixture()
def oidc_settings(monkeypatch):
    from app.services.sso import oidc as oidc_module

    monkeypatch.setattr(oidc_module.settings, "oidc_enabled", True)
    monkeypatch.setattr(
        oidc_module.settings, "oidc_issuer", "https://identity.example.com"
    )
    monkeypatch.setattr(oidc_module.settings, "oidc_client_id", "dotmac-erp")
    monkeypatch.setattr(oidc_module.settings, "oidc_client_secret", "test-secret")
    monkeypatch.setattr(oidc_module.settings, "oidc_redirect_uri", None)
    monkeypatch.setattr(oidc_module.settings, "oidc_scopes", "openid profile email")
    return oidc_module


def _metadata() -> OIDCMetadata:
    return OIDCMetadata(
        issuer="https://identity.example.com",
        authorization_endpoint="https://identity.example.com/authorize",
        token_endpoint="https://identity.example.com/token",
        jwks_uri="https://identity.example.com/jwks",
    )


def test_start_login_uses_authorization_code_pkce_and_signed_state(
    db_session, monkeypatch, oidc_settings
):
    client = OIDCClient()
    monkeypatch.setattr(client, "_get_metadata", lambda _issuer: _metadata())

    login = client.start_login(
        db_session,
        _request(),
        "/finance/dashboard?tab=summary",
    )

    query = parse_qs(urlparse(login.authorization_url).query)
    saved = client._verify_state(db_session, login.state_cookie)
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == [saved["state"]]
    assert query["nonce"] == [saved["nonce"]]
    assert saved["next"] == "/finance/dashboard?tab=summary"
    assert "verifier" in saved


def test_complete_login_maps_issuer_subject_to_local_person_only(
    db_session, person, monkeypatch, oidc_settings
):
    client = OIDCClient()
    binding = FederatedIdentity(
        person_id=person.id,
        issuer="https://identity.example.com",
        subject="opaque-provider-subject",
    )
    db_session.add(binding)
    db_session.commit()
    state_cookie = client._sign_state(
        db_session,
        {
            "state": "expected-state",
            "nonce": "expected-nonce",
            "verifier": "pkce-verifier",
            "next": "/dashboard",
            "exp": int(time.time()) + 60,
        },
    )
    monkeypatch.setattr(client, "_get_metadata", lambda _issuer: _metadata())
    monkeypatch.setattr(client, "_exchange_code", lambda *args: "id-token")
    monkeypatch.setattr(
        client,
        "_validate_id_token",
        lambda *args: {
            "iss": "https://identity.example.com",
            "sub": "opaque-provider-subject",
            "roles": ["external-admin-must-not-be-trusted"],
        },
    )

    authentication = client.complete_login(
        db_session,
        _request("/auth/oidc/callback"),
        code="authorization-code",
        state="expected-state",
        state_cookie=state_cookie,
    )

    assert authentication.person_id == str(person.id)
    assert authentication.next_url == "/dashboard"
    assert binding.last_authenticated_at is not None


def test_complete_login_rejects_unlinked_external_identity(
    db_session, monkeypatch, oidc_settings
):
    client = OIDCClient()
    state_cookie = client._sign_state(
        db_session,
        {
            "state": "expected-state",
            "nonce": "expected-nonce",
            "verifier": "pkce-verifier",
            "next": "/",
            "exp": int(time.time()) + 60,
        },
    )
    monkeypatch.setattr(client, "_get_metadata", lambda _issuer: _metadata())
    monkeypatch.setattr(client, "_exchange_code", lambda *args: "id-token")
    monkeypatch.setattr(
        client,
        "_validate_id_token",
        lambda *args: {"sub": "not-linked"},
    )

    with pytest.raises(HTTPException) as exc_info:
        client.complete_login(
            db_session,
            _request("/auth/oidc/callback"),
            code="authorization-code",
            state="expected-state",
            state_cookie=state_cookie,
        )

    assert exc_info.value.status_code == 403


def test_binding_can_be_reenabled_idempotently(db_session, person, oidc_settings):
    client = OIDCClient()
    binding = client.bind_identity(
        db_session,
        person_id=str(person.id),
        subject="stable-subject",
    )
    client.disable_binding(db_session, str(binding.id))

    rebound = client.bind_identity(
        db_session,
        person_id=str(person.id),
        subject="stable-subject",
    )

    assert rebound.id == binding.id
    assert rebound.is_active is True
