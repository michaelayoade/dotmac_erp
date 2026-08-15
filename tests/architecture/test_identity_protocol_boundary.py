"""ERP owns its identity end to end and speaks no external auth protocol.

Two separate premises live here, and they are enforceable independently:

1. ERP never shares an auth database, JWT signing secret, or cookie authority
   with another application. This is the retired shared-auth-database SSO
   boundary; it is unaffected by anything below and still bites.

2. ERP ships NO external-identity protocol adapter at all. The previous version
   of this file asserted that ERP's own OIDC adapter mapped issuer/subject to a
   local person before minting a session. That adapter
   (``app/services/sso/oidc.py``) was deleted: it was never enabled, held zero
   rows in production, and had every signature- and claim-validation path
   monkeypatched out of its tests. With the code gone the old assertions could
   only have been retired or re-aimed, and retiring them would have left the
   region unmonitored — a hand-rolled port could grow back unreviewed.

   So the premise is re-aimed at the stronger, still-enforceable claim: there
   is no protocol adapter, and reintroducing one is a deliberate act that fails
   CI until this guard is updated alongside it. ERP remains the intended second
   consumer of the released ``dotmac-auth-oidc`` package (the Workspace pilot
   is first); adopting it is what re-opens this file, not a fresh in-repo
   implementation. See ``docs/oidc_identity_contract.md``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"

# Names that only appear when someone is speaking an external auth protocol
# in-process: the token/JWKS ceremony, not the words "oidc" or "sso" in prose.
# Matched on word boundaries — a bare substring test for ``id_token`` also hits
# ``_require_valid_token`` in app/web/onboarding_portal.py, which has nothing to
# do with any of this.
_PROTOCOL_MARKERS = (
    "openid-configuration",
    "jwks_uri",
    "code_challenge",
    "id_token",
    "authorization_endpoint",
    "token_endpoint",
)


def _mentions(source: str, marker: str) -> bool:
    return re.search(rf"\b{re.escape(marker)}\b", source) is not None


# The model class survives its own deletion because migration
# 20260720_federated_identity still owns the table. Only these files may name
# it — see the FederatedIdentity docstring.
_FEDERATED_IDENTITY_ALLOWED = {
    Path("app/models/auth.py"),
    Path("app/models/__init__.py"),
}


def _app_sources() -> list[tuple[Path, str]]:
    return [
        (path.relative_to(ROOT), path.read_text(encoding="utf-8"))
        for path in sorted(APP.rglob("*.py"))
    ]


# ── Structural scanners ─────────────────────────────────────────────────────
#
# The marker list above catches an in-repo protocol implementation by its WIRE
# STRINGS. It cannot catch the other way ERP could regain a federated login:
# importing the released `dotmac-auth-oidc` adapter and calling it. That code
# contains none of those strings — a review sensitivity probe fed the guard a
# real adapter import calling `start_login()` and got ZERO matches.
#
# Adopting the adapter is the intended future path, but it is gated
# (docs/oidc_identity_contract.md § Reintroduction). Gated means the gate has to
# be reachable: this guard fails on adoption so that reintroduction is a
# deliberate change to this file plus the contract, never a quiet import.
#
# AST, not text, for the same reason the rest of this repository moved that way:
# a guard that greps for a concept also flags the comment explaining the
# concept, and the cheapest way to satisfy it is to delete the explanation.

_ADAPTER_ROOTS = frozenset({"dotmac_auth_oidc"})
_ADAPTER_CALLS = frozenset({"start_login", "complete_login"})
_FEDERATED_TABLE = "federated_identities"


def _parse(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
        return None


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """Identity of every docstring Constant, so prose can be excluded.

    By node identity rather than by value: `ast.get_docstring` returns cleaned
    text that never equals the raw constant, which is how an earlier version of
    a guard like this failed to exempt the docstring it was meant to exempt.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def find_adapter_usage(rel_path: str, source: str) -> list[str]:
    """An import of the OIDC adapter, or a call to its ceremony entry points."""
    tree = _parse(source)
    if tree is None:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else func.id
                    if isinstance(func, ast.Name)
                    else ""
                )
                if name in _ADAPTER_CALLS:
                    hits.append(f"{rel_path}:{node.lineno} calls {name}()")
            continue
        for name in names:
            if name.split(".")[0] in _ADAPTER_ROOTS:
                hits.append(f"{rel_path}:{node.lineno} imports {name}")
    return hits


def find_oidc_settings(rel_path: str, source: str) -> list[str]:
    """ANY `oidc_*` setting or `OIDC_*` environment read.

    Generic rather than a list of the eight that were deleted — a NINTH knob
    reintroduced under a new name is the same defect, and an enumeration would
    miss it. An earlier version checked three of the eight.
    """
    tree = _parse(source)
    if tree is None:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in {"getenv", "environ"}:
                for arg in node.args:
                    if (
                        isinstance(arg, ast.Constant)
                        and isinstance(arg.value, str)
                        and arg.value.upper().startswith("OIDC_")
                    ):
                        hits.append(f"{rel_path}:{node.lineno} reads {arg.value}")
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        else:
            continue
        for target in targets:
            if target.id.lower().startswith("oidc_"):
                hits.append(f"{rel_path}:{node.lineno} defines {target.id}")
    return hits


def find_federated_identity_use(rel_path: str, source: str) -> list[str]:
    """The ORM class, or the raw table name in SQL.

    The raw table name matters and was previously unchecked: a
    `text("select ... from federated_identities")` reaches the table without
    ever naming the model, so a model-only scan sees nothing. Docstrings are
    excluded so the FederatedIdentity docstring explaining its own retirement
    does not trip the guard protecting it.
    """
    tree = _parse(source)
    if tree is None:
        return []
    docstrings = _docstring_nodes(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        # The model reached either bare or through a module — `FederatedIdentity`
        # and `models.FederatedIdentity` are the same consumer.
        if (isinstance(node, ast.Name) and node.id == "FederatedIdentity") or (
            isinstance(node, ast.Attribute) and node.attr == "FederatedIdentity"
        ):
            hits.append(f"{rel_path}:{node.lineno} references FederatedIdentity")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and re.search(rf"\b{_FEDERATED_TABLE}\b", node.value)
        ):
            hits.append(f"{rel_path}:{node.lineno} names the table in SQL")
    return hits


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


def test_erp_ships_no_external_identity_protocol_adapter() -> None:
    """The deleted OIDC package has not regrown under any name."""
    assert not (APP / "services/sso").exists(), (
        "app/services/sso/ is back. ERP's external-identity boundary is an "
        "adoption of the released dotmac-auth-oidc package, not an in-repo "
        "protocol implementation — see docs/oidc_identity_contract.md."
    )

    offenders = [
        f"{path}: {marker}"
        for path, source in _app_sources()
        for marker in _PROTOCOL_MARKERS
        if _mentions(source, marker)
    ]
    # The other way back: importing the released adapter rather than rewriting
    # the ceremony. Structural, because that code contains none of the wire
    # strings above.
    offenders += [
        hit
        for path, source in _app_sources()
        for hit in find_adapter_usage(str(path), source)
    ]
    assert not offenders, (
        "external auth-protocol machinery found under app/ — ERP mints its own "
        "sessions and speaks no identity protocol in-process. Adopting "
        "dotmac-auth-oidc is the intended path, but it is GATED: see "
        "docs/oidc_identity_contract.md § Reintroduction, and change this guard "
        "in the same commit.\n  " + "\n  ".join(offenders)
    )


def test_no_oidc_configuration_knob_has_been_reintroduced() -> None:
    """A knob that configures nothing is worse than no knob: an operator who
    sets it believes federated login is on."""
    offenders = [
        hit
        for path, source in _app_sources()
        for hit in find_oidc_settings(str(path), source)
    ]
    assert not offenders, (
        "an OIDC configuration knob is back, and it configures nothing:\n  "
        + "\n  ".join(offenders)
    )

    # `.env.example` is not Python, so it stays a text scan — but over EVERY
    # `OIDC_*` name rather than the three that were spot-checked before.
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    stale = re.findall(r"^\s*(OIDC_[A-Z_]+)\s*=", env_example, re.MULTILINE)
    assert not stale, f"OIDC settings are back in .env.example: {stale}"


def test_federated_identity_has_no_reader_or_writer() -> None:
    """The table outlives the code, but nothing may start using it again
    without also reopening the retirement decision."""
    offenders = [
        hit
        for path, source in _app_sources()
        if path not in _FEDERATED_IDENTITY_ALLOWED
        for hit in find_federated_identity_use(str(path), source)
    ]
    assert not offenders, (
        "federated_identities gained a consumer without an identity-provider "
        "integration to justify it. This covers the RAW TABLE NAME as well as "
        "the model: a text() query reaches the table without naming the "
        "class.\n  " + "\n  ".join(offenders)
    )


def test_the_protocol_detector_still_bites() -> None:
    """Sensitivity proof: the two scans above pass over the current tree, and a
    check that can no longer fail passes for the wrong reason. Feed each one a
    sample of exactly what it exists to catch."""
    reintroduced = (
        "metadata = httpx.get(f'{issuer}/.well-known/openid-configuration')\n"
        "keys = httpx.get(metadata['jwks_uri']).json()['keys']\n"
        "claims = jwt.decode(id_token, keys[0])\n"
    )
    assert all(
        _mentions(reintroduced, marker)
        for marker in ("openid-configuration", "jwks_uri", "id_token")
    )
    # ...and the word-boundary form does not fire on the unrelated name that a
    # bare substring test would have caught.
    assert not _mentions(
        "onboarding, service = _require_valid_token(token, db)", "id_token"
    )

    binding_reader = "binding = db.scalar(select(FederatedIdentity))"
    assert "FederatedIdentity" in binding_reader
    assert Path("app/services/sso/oidc.py") not in _FEDERATED_IDENTITY_ALLOWED


# ── Sensitivity proofs for the structural scanners ──────────────────────────


def test_the_adapter_scanner_catches_the_probe_that_found_this_gap() -> None:
    """The exact shape a review probe fed the previous guard, which returned
    ZERO matches: a real adapter import calling `start_login()`. It contains
    none of the wire strings the marker list looks for, which is precisely why
    a text scan could not see it."""
    probe = (
        "from dotmac_auth_oidc import OIDCClient, RelyingPartyConfig\n"
        "\n"
        "def login(request):\n"
        "    client = OIDCClient(RelyingPartyConfig(...), state_store=store)\n"
        "    redirect = client.start_login(return_to='/')\n"
        "    return redirect.url\n"
    )
    hits = find_adapter_usage("app/services/auth_web.py", probe)
    assert any("imports dotmac_auth_oidc" in h for h in hits), hits
    assert any("start_login()" in h for h in hits), hits

    # And the module-attribute form, which an import-only scan would miss.
    assert find_adapter_usage("x.py", "import dotmac_auth_oidc\n")
    assert find_adapter_usage("x.py", "client.complete_login(code=c)\n")


def test_the_adapter_scanner_is_silent_on_prose_and_on_erp_login() -> None:
    """The complement. This repository documents that adopting the adapter is
    the intended future path — saying so must not trip the guard, or the only
    way to green is to delete the explanation."""
    assert (
        find_adapter_usage(
            "x.py", '"""Reintroduction is an adoption of dotmac_auth_oidc."""\n'
        )
        == []
    )
    assert find_adapter_usage("x.py", "# see dotmac_auth_oidc start_login\n") == []
    # ERP's own local login is not an OIDC ceremony.
    assert find_adapter_usage("x.py", "def login_response(db, request): ...\n") == []


def test_the_settings_scanner_catches_every_knob_not_a_chosen_three() -> None:
    """All eight deleted settings, and a ninth invented one — the enumeration
    this replaces checked three."""
    for name in (
        "OIDC_ENABLED",
        "OIDC_ISSUER",
        "OIDC_CLIENT_ID",
        "OIDC_CLIENT_SECRET",
        "OIDC_DISCOVERY_URL",
        "OIDC_REDIRECT_URI",
        "OIDC_SCOPES",
        "OIDC_REQUEST_TIMEOUT",
        "OIDC_BACKCHANNEL_LOGOUT_URL",
    ):
        assert find_oidc_settings("app/config.py", f'x = os.getenv("{name}", "")\n'), (
            f"{name} would not have been caught"
        )
    assert find_oidc_settings("app/config.py", "oidc_enabled: bool = False\n")
    assert find_oidc_settings("app/config.py", 'x = os.environ("OIDC_ISSUER")\n')
    # Prose, and an unrelated setting, stay silent.
    assert find_oidc_settings("x.py", '"""OIDC_ENABLED was removed."""\n') == []
    assert (
        find_oidc_settings("x.py", 'database_url = os.getenv("DATABASE_URL")\n') == []
    )


def test_the_federated_identity_scanner_catches_raw_sql_not_only_the_model() -> None:
    """The gap that mattered: a raw query reaches the table without ever naming
    the class, so a model-only scan sees nothing."""
    assert find_federated_identity_use(
        "app/services/x.py",
        'rows = db.execute(text("select 1 from federated_identities")).all()\n',
    )
    assert find_federated_identity_use(
        "app/services/x.py", "binding = db.scalar(select(FederatedIdentity))\n"
    )
    assert find_federated_identity_use(
        "app/services/x.py", "models.FederatedIdentity\n"
    )
    # The model's own docstring explains its retirement — that must not fire.
    assert (
        find_federated_identity_use(
            "app/models/x.py",
            '"""Kept only because migration 20260720 owns federated_identities."""\n',
        )
        == []
    )
