from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dotmac_auth_oidc import LoginState, OIDCError, PER_REQUEST_STATE_STORE
from sqlalchemy import func, select

from app.models.auth import OIDCLoginState
from app.services import external_login
from app.services import oidc_runtime


ROOT = Path(__file__).resolve().parents[2]


def test_relying_party_uses_the_published_per_request_store_seam(monkeypatch) -> None:
    monkeypatch.setattr(oidc_runtime, "_held_client", None)
    config = oidc_runtime.OIDCProviderConfig(
        provider_binding="primary",
        issuer="https://idp.example.test/realms/erp",
        client_id="erp-client",
        client_secret="held-test-material",
        redirect_uri="https://erp.example.test/auth/oidc/callback",
        discovery_url=None,
        timeout_seconds=10.0,
        ceremony_ttl_seconds=600,
        clock_skew_seconds=60,
    )
    oidc_runtime.install(config)

    client = oidc_runtime.client()

    assert client.config.provider_binding == "primary"
    assert client._store is PER_REQUEST_STATE_STORE


def test_failed_explicit_refresh_retains_the_working_held_client(monkeypatch) -> None:
    config = oidc_runtime.OIDCProviderConfig(
        provider_binding="primary",
        issuer="https://idp.example.test/realms/erp",
        client_id="erp-client",
        client_secret="held-test-material",
        redirect_uri="https://erp.example.test/auth/oidc/callback",
        discovery_url=None,
        timeout_seconds=10.0,
        ceremony_ttl_seconds=600,
        clock_skew_seconds=60,
    )
    oidc_runtime.install(config)
    working = oidc_runtime.client()

    class RefusingClient:
        def __init__(self, *args, **kwargs) -> None:
            raise OIDCError("replacement refused")

    monkeypatch.setattr(oidc_runtime, "OIDCClient", RefusingClient)

    with pytest.raises(OIDCError):
        oidc_runtime.install(config)

    assert oidc_runtime.client() is working
    assert oidc_runtime.provider_config() is config


def test_request_path_never_reads_environment_or_resolves_secret_material() -> None:
    paths = (
        ROOT / "app" / "services" / "oidc_runtime.py",
        ROOT / "app" / "services" / "external_login.py",
        ROOT / "app" / "web" / "auth.py",
    )
    forbidden = {"getenv", "environ", "resolve_secret", "resolve_openbao_ref"}
    hits: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden:
                hits.append(f"{path.name}:{node.lineno}:{node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in forbidden:
                hits.append(f"{path.name}:{node.lineno}:{node.attr}")

    assert hits == []


def test_external_login_never_consumes_provider_authorization_or_email() -> None:
    path = ROOT / "app" / "services" / "external_login.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {"claims", "email", "roles", "groups", "scopes"}
    hits = [
        f"{node.lineno}:{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden
    ]

    assert hits == []


def test_discovery_failure_rolls_back_the_stored_ceremony(
    monkeypatch, db_session, person
) -> None:
    config = oidc_runtime.OIDCProviderConfig(
        provider_binding="primary",
        issuer="https://idp.example.test/realms/erp",
        client_id="erp-client",
        client_secret="held-test-material",
        redirect_uri="https://erp.example.test/auth/oidc/callback",
        discovery_url=None,
        timeout_seconds=10.0,
        ceremony_ttl_seconds=600,
        clock_skew_seconds=60,
    )
    oidc_runtime.install(config)

    class FailingClient:
        def start_login(self, *, return_to, ttl_seconds, state_store):
            state_store.put(
                LoginState(
                    state_id="opaque-failed-state",
                    nonce="nonce",
                    code_verifier="verifier",
                    redirect_uri=config.redirect_uri,
                    issued_at=int(datetime.now(UTC).timestamp()),
                    return_to=return_to,
                ),
                ttl_seconds=ttl_seconds,
            )
            raise OIDCError("discovery refused")

    monkeypatch.setattr(
        external_login,
        "installation",
        lambda: (config, FailingClient()),
    )

    with pytest.raises(external_login.ExternalLoginRefused):
        external_login.start_external_login(
            db_session,
            organization_id=person.organization_id,
            return_to="/",
        )

    assert db_session.scalar(select(func.count()).select_from(OIDCLoginState)) == 0
