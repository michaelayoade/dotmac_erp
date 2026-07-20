from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_erp_has_no_shared_auth_database_or_cross_app_session_configuration() -> None:
    source_files = [
        ROOT / "app/config.py",
        ROOT / "app/db/__init__.py",
        ROOT / "app/services/auth_dependencies.py",
        ROOT / "app/services/auth_flow.py",
        ROOT / "app/services/auth_web.py",
        ROOT / "app/web/deps.py",
    ]
    source = "\n".join(path.read_text() for path in source_files)
    forbidden = (
        "AUTH_DATABASE_URL",
        "get_auth_db_session",
        "SSO_JWT_SECRET",
        "SSO_COOKIE_DOMAIN",
        "sso_provider_mode",
    )
    for value in forbidden:
        assert value not in source


def test_oidc_adapter_issues_local_sessions_after_identity_mapping() -> None:
    source = (ROOT / "app/services/sso/oidc.py").read_text()

    assert "FederatedIdentity.issuer == issuer" in source
    assert "FederatedIdentity.subject == subject" in source
    assert (
        "roles"
        not in source.split("def complete_login", 1)[1].split("def list_bindings", 1)[0]
    )
    assert "AuthSession" not in source
