"""Identity composition boundary and sensitivity proofs."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
PROTOCOL_MARKERS = {
    "authorization_endpoint",
    "code_challenge",
    "id_token",
    "jwks_uri",
    "token_endpoint",
}
ADAPTER_MODULES = {
    Path("app/services/oidc_runtime.py"),
    Path("app/services/external_login.py"),
    Path("app/services/oidc_state_store.py"),
    Path("app/services/oidc_bootstrap.py"),
}
IDENTITY_WRITER = Path("app/services/external_identity.py")
IDENTITY_MODEL_FILES = {
    Path("app/models/auth.py"),
    Path("app/models/__init__.py"),
}


def _source_files() -> list[tuple[Path, str]]:
    return [
        (path.relative_to(ROOT), path.read_text(encoding="utf-8"))
        for base in (APP, ROOT / "scripts", ROOT / "tools")
        if base.exists()
        for path in sorted(base.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def find_local_protocol(source: str) -> list[int]:
    tree = ast.parse(source)
    hits: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id in PROTOCOL_MARKERS
            or isinstance(node, ast.Attribute)
            and node.attr in PROTOCOL_MARKERS
            or isinstance(node, ast.keyword)
            and node.arg in PROTOCOL_MARKERS
        ):
            hits.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(marker in node.value for marker in PROTOCOL_MARKERS):
                hits.append(node.lineno)
    return hits


def find_binding_reference(source: str) -> list[int]:
    tree = ast.parse(source)
    hits: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id == "FederatedIdentity"
            or isinstance(node, ast.Attribute)
            and node.attr == "FederatedIdentity"
        ):
            hits.append(node.lineno)
    return hits


def test_no_shared_auth_database_or_cross_application_session_authority() -> None:
    source = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "app/config.py",
            "app/db/__init__.py",
            "app/services/auth_flow.py",
            "app/services/auth_dependencies.py",
        )
    )
    for forbidden in (
        "AUTH_DATABASE_URL",
        "get_auth_db_session",
        "SSO_JWT_SECRET",
        "SSO_COOKIE_DOMAIN",
        "dotmac_kernel.models.AuthSession",
    ):
        assert forbidden not in source


def test_shared_adapter_is_the_only_external_protocol_implementation() -> None:
    assert not (APP / "services" / "sso").exists()
    offenders = [
        f"{path}:{line}"
        for path, source in _source_files()
        if path not in ADAPTER_MODULES
        for line in find_local_protocol(source)
    ]
    assert offenders == []

    imports = {path for path, source in _source_files() if "dotmac_auth_oidc" in source}
    assert imports <= ADAPTER_MODULES
    assert {
        Path("app/services/oidc_runtime.py"),
        Path("app/services/external_login.py"),
        Path("app/services/oidc_state_store.py"),
    } <= imports


def test_external_identity_authority_is_the_only_binding_writer() -> None:
    offenders = [
        path
        for path, source in _source_files()
        if path not in IDENTITY_MODEL_FILES | {IDENTITY_WRITER}
        and find_binding_reference(source)
    ]
    assert offenders == []
    owner = (ROOT / IDENTITY_WRITER).read_text(encoding="utf-8")
    assert "with_for_update" in owner
    assert "commit(" not in owner


def test_login_composition_cannot_jit_or_link_by_email_or_provider_roles() -> None:
    source = (ROOT / "app/services/external_login.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"claims", "email", "roles", "groups", "scopes"}
    assert [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden
    ] == []
    assert "Person(" not in source
    assert ".bind(" not in source


def test_session_provenance_and_state_are_database_enforced() -> None:
    model = (ROOT / "app/models/auth.py").read_text(encoding="utf-8")
    migration = (
        ROOT / "alembic/versions/20260817_managed_application_lifecycle.py"
    ).read_text(encoding="utf-8")
    assert "external_identity_binding_id" in model
    assert "fk_sessions_external_identity_person" in migration
    assert "DELETE ... RETURNING" in (
        ROOT / "docs/oidc_identity_contract.md"
    ).read_text(encoding="utf-8")
    assert ".returning(" in (ROOT / "app/services/oidc_state_store.py").read_text(
        encoding="utf-8"
    )


def test_protocol_and_writer_detectors_are_sensitive() -> None:
    assert find_local_protocol("claims = response.id_token\n")
    assert find_local_protocol("jwks_uri = metadata.authorization_endpoint\n")
    assert find_binding_reference("binding = select(FederatedIdentity)\n")
    assert find_local_protocol("# id_token in prose only\n") == []
