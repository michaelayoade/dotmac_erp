"""A settings operation refuses an unscoped session instead of escalating it.

`DomainSettingService.list`, `.upsert_by_key` and `.set_value` each carried
this, copy-pasted:

    tenant_context = (
        nullcontext()
        if db.info.get("organization_id") or db.info.get("allow_cross_org")
        else allow_cross_org(db)          # <-- no scope? grant full bypass
    )

The fallback for "the caller forgot to scope this session" was "see and
write every tenant's rows". The lookup then matched on (domain, key) alone
and returned whichever organization's row the index yielded first —
invisible on a single-tenant database, a cross-tenant write the moment there
are two.

Three copies, so fixing one would not have reached the others. They now
share `_scoped_or_refuse`, and this pins all three.

## The distinction being tested

Refusing is NOT the same as banning cross-tenant work. A caller that
genuinely spans organizations opens a `cross_org_session()`, which sets
`allow_cross_org` in `db.info` and passes honestly. What is refused is
silence — a session that states nothing and is handed a bypass anyway.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.domain_settings import SettingsScopeRequired, _scoped_or_refuse


def _session(**info):
    """A stand-in whose only relevant surface is `.info`."""
    return SimpleNamespace(info=dict(info))


# --------------------------------------------------------------------------
# The refusal
# --------------------------------------------------------------------------


def test_an_unscoped_session_is_refused():
    """The load-bearing test: silence must not buy a bypass."""
    with pytest.raises(SettingsScopeRequired):
        _scoped_or_refuse(_session(), "DomainSettingService.upsert_by_key")


def test_the_refusal_names_the_operation_and_the_fix():
    """An error a batch script hits at 3am should say what to do."""
    with pytest.raises(SettingsScopeRequired) as caught:
        _scoped_or_refuse(_session(), "DomainSettingService.set_value")
    message = str(caught.value)
    assert "DomainSettingService.set_value" in message
    assert "session_for_org" in message
    assert "cross_org_session" in message


def test_an_empty_organization_id_does_not_count_as_scope():
    """`db.info["organization_id"] = None` is the shape a half-primed session
    has. It must not satisfy the check — that would restore the hole."""
    with pytest.raises(SettingsScopeRequired):
        _scoped_or_refuse(_session(organization_id=None), "op")


# --------------------------------------------------------------------------
# What is still allowed — the check must not be a blanket ban
# --------------------------------------------------------------------------


def test_a_tenant_scoped_session_passes():
    with _scoped_or_refuse(_session(organization_id=uuid4()), "op"):
        pass  # a no-op context; reaching here is the assertion


def test_an_explicit_cross_org_session_passes():
    """`cross_org_session()` sets this flag. Genuinely cross-tenant work
    remains possible — it just has to SAY so at the call site rather than
    being granted the same power by omission."""
    with _scoped_or_refuse(_session(allow_cross_org=True), "op"):
        pass


# --------------------------------------------------------------------------
# Sensitivity: prove the old behaviour is actually gone
# --------------------------------------------------------------------------


def test_the_escalating_ternary_is_gone_from_every_site():
    """A source check, because the three copies are what made this survive.

    If someone reintroduces the idiom in a fourth place, the unit tests above
    would still pass — they only cover the shared helper."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent.parent
        / "app"
        / "services"
        / "domain_settings.py"
    ).read_text(encoding="utf-8")

    executable = [
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    ]
    # The docstrings quote the old idiom deliberately, to explain it. Look for
    # it as CODE: an `else` arm handing back the bypass.
    offenders = [
        line for line in executable if line.strip().startswith("else allow_cross_org(")
    ]
    assert offenders == [], (
        "An unscoped session is being escalated to a cross-org bypass again. "
        "Use `_scoped_or_refuse(db, '<operation>')`:\n  " + "\n  ".join(offenders)
    )


def test_the_helper_is_used_by_all_three_operations():
    """Counts the call sites, so removing one and inlining the old behaviour
    fails here rather than passing quietly."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent.parent
        / "app"
        / "services"
        / "domain_settings.py"
    ).read_text(encoding="utf-8")

    uses = source.count("_scoped_or_refuse(db,")
    assert uses == 3, (
        f"expected 3 scope checks (list, upsert_by_key, set_value), found {uses}"
    )


def test_a_real_service_call_refuses_rather_than_reading_across_tenants():
    """End-to-end through the actual service method, not just the helper.

    The mock never answers a query — if the refusal did not fire, the call
    would proceed to `db.scalar` and this would fail differently, which is
    the point."""
    from app.models.domain_settings import SettingDomain
    from app.services.domain_settings import DomainSettingService

    service = DomainSettingService(domain=SettingDomain.auth)
    db = MagicMock()
    db.info = {}  # unscoped

    with pytest.raises(SettingsScopeRequired):
        service.set_value(db, key="anything", value_type=MagicMock())

    db.scalar.assert_not_called()
