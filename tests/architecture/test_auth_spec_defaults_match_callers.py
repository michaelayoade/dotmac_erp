"""An auth spec's default must equal the fallback its caller actually uses.

`auth_flow` reads each of these settings and applies its own fallback when the
setting is unset. That makes TWO answers for one question, and which one runs
depends on the code path: the admin screen shows the spec's, the running system
uses the caller's.

Four had drifted, three of them security properties — `refresh_cookie_secure`
(spec False, caller True), `refresh_cookie_samesite` (lax vs strict),
`refresh_cookie_path` (/auth vs /), and `jwt_access_ttl_minutes` (15 vs 60).
Consolidating those readers onto the resolver without noticing would have
silently weakened three cookie protections and quadrupled access-token lifetime,
with every test still passing — because no test asserts on a default nobody set.

This pins them equal so the consolidation is provably behaviour-preserving, and
so the next divergence fails here instead of in production.

The long-term fix is a deployment profile declaring these values once
(starter ADR-0013), at which point the caller fallbacks go away entirely and
this test retires with them.
"""

from __future__ import annotations

import re

import pytest
from app.models.domain_settings import SettingDomain
from app.services.settings_spec import get_spec

# key -> the fallback `app/services/auth_flow.py` applies when the setting is
# unset. Read off the source; update BOTH together or not at all.
CALLER_FALLBACKS: dict[str, object] = {
    "jwt_algorithm": "HS256",
    "jwt_access_ttl_minutes": 60,
    "jwt_refresh_ttl_days": 30,
    "totp_issuer": "dotmac_erp",
    "refresh_cookie_name": "refresh_token",
    "refresh_cookie_secure": True,
    "refresh_cookie_samesite": "strict",
    "refresh_cookie_path": "/",
}


@pytest.mark.parametrize(("key", "fallback"), sorted(CALLER_FALLBACKS.items()))
def test_spec_default_matches_the_caller_fallback(key: str, fallback: object) -> None:
    spec = get_spec(SettingDomain.auth, key)
    assert spec is not None, f"auth/{key} has no spec"
    assert spec.default == fallback, (
        f"auth/{key}: the spec defaults to {spec.default!r} but auth_flow falls "
        f"back to {fallback!r}. Two answers for one setting — the admin screen "
        "shows one and the running system uses the other. Reconcile them, and "
        "if the change is deliberate, make it deliberately rather than by "
        "letting a reader and a declaration drift apart."
    )


def test_the_scan_covers_the_readers() -> None:
    """A fallback added to `auth_flow` without an entry here would drift
    unnoticed, which is the failure this file exists to prevent."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "app/services/auth_flow.py"
    ).read_text()
    read_keys = set(re.findall(r'_setting_value\(db, "([a-z_0-9]+)"\)', source))
    # `password_reset_ttl_minutes` is read but has no spec at all — a separate
    # gap, tracked rather than silently folded in here.
    untracked = (
        read_keys
        - set(CALLER_FALLBACKS)
        - {
            "password_reset_ttl_minutes",
            "totp_encryption_key",
            "jwt_secret",
            "refresh_cookie_domain",
        }
    )
    assert not untracked, (
        f"auth_flow reads {sorted(untracked)} with a fallback this test does not "
        "cover — add them to CALLER_FALLBACKS"
    )
