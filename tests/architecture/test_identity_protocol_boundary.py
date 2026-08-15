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
    assert not offenders, (
        "external auth-protocol machinery found under app/ — ERP mints its own "
        "sessions and speaks no identity protocol in-process:\n  "
        + "\n  ".join(offenders)
    )


def test_no_oidc_configuration_knob_has_been_reintroduced() -> None:
    """A knob that configures nothing is worse than no knob: an operator who
    sets it believes federated login is on."""
    config = (ROOT / "app/config.py").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("OIDC_ENABLED", "OIDC_ISSUER", "OIDC_CLIENT_ID"):
        assert f'os.getenv("{name}"' not in config, f"{name} setting is back"
        assert f"\n{name}=" not in env_example, f"{name} is back in .env.example"


def test_federated_identity_has_no_reader_or_writer() -> None:
    """The table outlives the code, but nothing may start using it again
    without also reopening the retirement decision."""
    offenders = [
        str(path)
        for path, source in _app_sources()
        if "FederatedIdentity" in source and path not in _FEDERATED_IDENTITY_ALLOWED
    ]
    assert not offenders, (
        "federated_identities gained a consumer without an identity-provider "
        "integration to justify it:\n  " + "\n  ".join(offenders)
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
