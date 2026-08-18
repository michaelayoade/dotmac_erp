"""A PLATFORM-owned setting refuses an organization row, and ignores one.

The four outbound-webhook SSRF keys (plus the timeout ceiling) exist to
CONSTRAIN an organization. A per-organization row for one of them would let
the constrained party rewrite its own constraint, so:

* the WRITE is refused at the ORM boundary — one listener on `DomainSetting`,
  which is the single place every `DomainSetting(...)` constructor, the
  settings import path and the history-restore path all pass through;
* the READ refuses to let an organization row answer, on all FOUR read paths,
  so a row created OUTSIDE the ORM (raw SQL, a psql session, a replica that
  predates this change) is inert rather than authoritative. Three of them —
  `resolve_value`, `DomainSettings.get_by_key`, and `SettingsCache`'s
  single-key path — discard the caller's organization. The fourth,
  `SettingsCache._load_domain_rows` behind `get_domain_settings`, selects both
  scopes in one statement and lets an `ORDER BY` decide, so it skips the
  organization row instead. The count is stated because an earlier version of
  this docstring said "three", which was true of the three it named and left
  the bulk path applying plain override semantics.

Both halves are needed. `public.domain_settings` has no RLS policy — a
platform row's `organization_id` is NULL and matches no
`get_current_organization_id()` — so this application layer is the whole
boundary, and a mechanism with only a write half would be one raw INSERT away
from being no mechanism at all.

These tests assert the REFUSAL. A happy path that merely showed a platform row
being readable would pass against a listener that had been deleted.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.domain_settings import (
    DomainSetting,
    PlatformOwnedSettingError,
    SettingDomain,
    SettingScope,
    SettingValueType,
)
from app.services.setting_scopes import is_platform_owned, platform_owned_keys
from app.services.settings_cache import settings_cache
from app.services.settings_spec import (
    SettingScopeAuthority,
    get_spec,
    resolve_value,
)

# The ceiling keys, and the keys that deliberately are NOT platform-owned.
CEILING_KEYS = (
    "webhook_allowed_hosts",
    "webhook_allowed_domains",
    "webhook_allow_insecure",
    "webhook_allow_localhost",
    "webhook_max_timeout_seconds",
)
# Every platform-owned key in the automation domain, which is the ceiling above
# plus one. `openbao_allow_insecure` is not part of the webhook ceiling and is
# not composed by `webhook_policy`; it is platform-owned for the same reason —
# a tenant-writable row that turns off TLS verification, here against the store
# holding every other secret — and it shares the refusal mechanism exactly, so
# the write and read tests below must cover it too.
PLATFORM_OWNED_KEYS = CEILING_KEYS + ("openbao_allow_insecure",)
TENANT_OWNED_KEYS = (
    "webhook_timeout_seconds",
    "webhook_tenant_allowed_hosts",
    "webhook_tenant_allowed_domains",
    "webhook_tenant_allow_insecure",
    "webhook_tenant_allow_localhost",
)


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    settings_cache.clear_inmemory()
    yield
    settings_cache.clear_inmemory()


@pytest.fixture()
def org_id():
    return uuid.uuid4()


@pytest.fixture()
def cleanup(db_session):
    """Delete rows this test made, whichever way they were made."""
    made: list[uuid.UUID] = []
    yield made
    if made:
        db_session.rollback()
        db_session.execute(
            DomainSetting.__table__.delete().where(DomainSetting.id.in_(made))
        )
        db_session.commit()


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", PLATFORM_OWNED_KEYS)
def test_the_ceiling_keys_are_declared_platform_owned(key):
    spec = get_spec(SettingDomain.automation, key)
    assert spec is not None, f"automation/{key} is not a registered spec"
    assert spec.scope is SettingScopeAuthority.PLATFORM
    assert is_platform_owned(SettingDomain.automation, key) is True


@pytest.mark.parametrize("key", TENANT_OWNED_KEYS)
def test_the_narrowing_keys_are_not_platform_owned(key):
    """The sensitivity proof for every test below.

    A registry that answered True for everything would make each refusal test
    pass while proving nothing about the boundary. These five keys live in the
    same domain, are named alike, and must answer False — a timeout is a
    preference and a narrowing is the organization's own.
    """
    spec = get_spec(SettingDomain.automation, key)
    assert spec is not None, f"automation/{key} is not a registered spec"
    assert spec.scope is SettingScopeAuthority.TENANT
    assert is_platform_owned(SettingDomain.automation, key) is False


def test_the_registry_is_populated_and_narrow():
    """It is neither empty (a check that fails open) nor everything."""
    declared = platform_owned_keys()
    assert declared, "no platform-owned keys registered — the check fails open"
    for key in PLATFORM_OWNED_KEYS:
        assert (str(SettingDomain.automation), key) in declared
    for key in TENANT_OWNED_KEYS:
        assert (str(SettingDomain.automation), key) not in declared


# ---------------------------------------------------------------------------
# The write refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", PLATFORM_OWNED_KEYS)
def test_an_organization_row_for_a_platform_key_is_refused_on_insert(
    db_session, org_id, key
):
    row = DomainSetting(
        domain=SettingDomain.automation,
        key=key,
        organization_id=org_id,
        scope=SettingScope.ORG_SPECIFIC,
        value_type=SettingValueType.string,
        value_text="attacker.example",
        is_active=True,
    )
    db_session.add(row)

    with pytest.raises(PlatformOwnedSettingError) as excinfo:
        db_session.flush()

    assert key in str(excinfo.value)
    db_session.rollback()


def test_the_refusal_names_the_way_out(db_session, org_id):
    """A refusal that does not say what to do instead becomes a workaround."""
    db_session.add(
        DomainSetting(
            domain=SettingDomain.automation,
            key="webhook_allowed_hosts",
            organization_id=org_id,
            scope=SettingScope.ORG_SPECIFIC,
            value_type=SettingValueType.string,
            value_text="attacker.example",
        )
    )
    with pytest.raises(PlatformOwnedSettingError) as excinfo:
        db_session.flush()
    message = str(excinfo.value)
    db_session.rollback()

    assert "platform-owned" in message
    assert "webhook_tenant_" in message


def test_a_platform_row_may_not_be_moved_to_an_organization(
    db_session, org_id, cleanup
):
    """The UPDATE half. Insert-only enforcement is trivially defeated.

    Create the legitimate platform row, then try to reassign it to one
    organization — which is both a privilege escalation and a denial of
    service for everybody else, since the platform row would stop existing.
    """
    row = DomainSetting(
        domain=SettingDomain.automation,
        key="webhook_allowed_hosts",
        organization_id=None,
        scope=SettingScope.GLOBAL,
        value_type=SettingValueType.string,
        value_text="selfcare.dotmac.io",
    )
    db_session.add(row)
    db_session.commit()
    cleanup.append(row.id)

    row.organization_id = org_id
    row.scope = SettingScope.ORG_SPECIFIC
    with pytest.raises(PlatformOwnedSettingError):
        db_session.flush()
    db_session.rollback()


def test_an_organization_row_for_a_narrowing_key_is_allowed(
    db_session, org_id, cleanup
):
    """The other half of the sensitivity proof: the listener is not a blanket ban.

    An organization narrowing its own webhook policy is exactly the supported
    move, and it goes through the same listener. If this ever fails, the
    refusal above has stopped being about platform ownership.
    """
    row = DomainSetting(
        domain=SettingDomain.automation,
        key="webhook_tenant_allowed_hosts",
        organization_id=org_id,
        scope=SettingScope.ORG_SPECIFIC,
        value_type=SettingValueType.string,
        value_text="api.acme.com",
    )
    db_session.add(row)
    db_session.commit()
    cleanup.append(row.id)

    assert row.id is not None


def test_a_platform_row_for_a_platform_key_is_allowed(db_session, cleanup):
    """The listener refuses a SCOPE, not a key."""
    row = DomainSetting(
        domain=SettingDomain.automation,
        key="webhook_allowed_domains",
        organization_id=None,
        scope=SettingScope.GLOBAL,
        value_type=SettingValueType.string,
        value_text="dotmac.io",
    )
    db_session.add(row)
    db_session.commit()
    cleanup.append(row.id)

    assert row.id is not None


# ---------------------------------------------------------------------------
# The read-side override — a row that should not exist is inert
# ---------------------------------------------------------------------------


def _insert_outside_the_orm(db_session, **values) -> uuid.UUID:
    """Insert a row the way raw SQL would: no mapper, therefore no listener.

    A Core-level insert against the table bypasses `before_insert` entirely,
    which is precisely the case the read-side override exists for.
    """
    row_id = uuid.uuid4()
    db_session.execute(
        DomainSetting.__table__.insert().values(id=row_id, is_active=True, **values)
    )
    db_session.commit()
    return row_id


def test_a_smuggled_organization_row_does_not_widen_the_policy(
    db_session, org_id, cleanup
):
    """`resolve_value` discards the organization for a platform-owned key."""
    cleanup.append(
        _insert_outside_the_orm(
            db_session,
            domain=SettingDomain.automation,
            key="webhook_allow_localhost",
            organization_id=org_id,
            scope=SettingScope.ORG_SPECIFIC,
            value_type=SettingValueType.boolean,
            value_text="true",
        )
    )

    # The row really is there — this test proves inertness, not absence.
    stored = db_session.execute(
        DomainSetting.__table__.select().where(
            DomainSetting.organization_id == org_id,
            DomainSetting.key == "webhook_allow_localhost",
        )
    ).first()
    assert stored is not None

    resolved = resolve_value(
        db_session,
        SettingDomain.automation,
        "webhook_allow_localhost",
        organization_id=org_id,
    )
    assert resolved is False, (
        "an organization-scoped row was honoured for a platform-owned key; the "
        "read-side scope override in resolve_value is not doing its job"
    )


def test_a_smuggled_row_does_not_win_over_the_platform_row(db_session, org_id, cleanup):
    """With both rows present, the platform row is the one that answers."""
    platform_row = DomainSetting(
        domain=SettingDomain.automation,
        key="webhook_allowed_hosts",
        organization_id=None,
        scope=SettingScope.GLOBAL,
        value_type=SettingValueType.string,
        value_text="selfcare.dotmac.io",
    )
    db_session.add(platform_row)
    db_session.commit()
    cleanup.append(platform_row.id)

    cleanup.append(
        _insert_outside_the_orm(
            db_session,
            domain=SettingDomain.automation,
            key="webhook_allowed_hosts",
            organization_id=org_id,
            scope=SettingScope.ORG_SPECIFIC,
            value_type=SettingValueType.string,
            value_text="attacker.example",
        )
    )

    resolved = resolve_value(
        db_session,
        SettingDomain.automation,
        "webhook_allowed_hosts",
        organization_id=org_id,
    )
    assert resolved == "selfcare.dotmac.io"


def test_the_cached_path_collapses_a_platform_key_to_one_entry(
    db_session, org_id, cleanup
):
    """One scope, therefore one cache entry — and one invalidation.

    Without the collapse, N organizations would each cache the same platform
    value under their own key, so invalidating a platform write would have to
    sweep all N — and any one it missed would keep serving the old ceiling.
    """
    platform_row = DomainSetting(
        domain=SettingDomain.automation,
        key="webhook_allowed_hosts",
        organization_id=None,
        scope=SettingScope.GLOBAL,
        value_type=SettingValueType.string,
        value_text="selfcare.dotmac.io",
    )
    db_session.add(platform_row)
    db_session.commit()
    cleanup.append(platform_row.id)

    value = settings_cache.get_setting_value(
        db_session,
        SettingDomain.automation,
        "webhook_allowed_hosts",
        organization_id=org_id,
    )
    assert value == "selfcare.dotmac.io"

    global_key = settings_cache._make_key(
        SettingDomain.automation, None, "webhook_allowed_hosts"
    )
    org_key = settings_cache._make_key(
        SettingDomain.automation, org_id, "webhook_allowed_hosts"
    )
    assert global_key != org_key, "the two cache keys are not distinguishable"
    assert settings_cache._inmemory.get(global_key) == "selfcare.dotmac.io"
    assert settings_cache._inmemory.get(org_key) is None, (
        "the read cached under an organization scope; a platform write would "
        "have to sweep one entry per organization to invalidate it"
    )


# ---------------------------------------------------------------------------
# The fourth read path — the bulk one
# ---------------------------------------------------------------------------


def test_the_bulk_path_does_not_let_an_organization_row_answer(
    db_session, org_id, cleanup
):
    """`get_domain_settings` selects both scopes at once, so it needs its own check.

    The three single-key paths discard the caller's organization. This one
    cannot: one statement fetches the organization row and the platform row
    together and an `ORDER BY` puts the organization's last, so plain override
    semantics would hand back the smuggled value — the same widening the other
    three refuse, reached through a different public method while three
    docstrings said the read side was covered.
    """
    platform_row = DomainSetting(
        domain=SettingDomain.automation,
        key="webhook_allowed_hosts",
        organization_id=None,
        scope=SettingScope.GLOBAL,
        value_type=SettingValueType.string,
        value_text="selfcare.dotmac.io",
    )
    db_session.add(platform_row)
    db_session.commit()
    cleanup.append(platform_row.id)

    cleanup.append(
        _insert_outside_the_orm(
            db_session,
            domain=SettingDomain.automation,
            key="webhook_allowed_hosts",
            organization_id=org_id,
            scope=SettingScope.ORG_SPECIFIC,
            value_type=SettingValueType.string,
            value_text="attacker.example",
        )
    )

    values = settings_cache.get_domain_settings(
        db_session, SettingDomain.automation, organization_id=org_id
    )
    assert values.get("webhook_allowed_hosts") == "selfcare.dotmac.io", (
        "the bulk read path applied plain override semantics to a platform-owned key"
    )


def test_the_bulk_path_still_prefers_an_organization_row_for_a_tenant_key(
    db_session, org_id, cleanup
):
    """Sensitivity. A blanket "global always wins" would pass the test above.

    Override semantics are correct for every key that is not platform-owned,
    and `webhook_tenant_allowed_hosts` — same domain, adjacent name, the
    organization's OWN narrowing — must keep them.
    """
    platform_row = DomainSetting(
        domain=SettingDomain.automation,
        key="webhook_tenant_allowed_hosts",
        organization_id=None,
        scope=SettingScope.GLOBAL,
        value_type=SettingValueType.string,
        value_text="platform.example",
    )
    db_session.add(platform_row)
    db_session.commit()
    cleanup.append(platform_row.id)

    org_row = DomainSetting(
        domain=SettingDomain.automation,
        key="webhook_tenant_allowed_hosts",
        organization_id=org_id,
        scope=SettingScope.ORG_SPECIFIC,
        value_type=SettingValueType.string,
        value_text="mine.example",
    )
    db_session.add(org_row)
    db_session.commit()
    cleanup.append(org_row.id)

    values = settings_cache.get_domain_settings(
        db_session, SettingDomain.automation, organization_id=org_id
    )
    assert values.get("webhook_tenant_allowed_hosts") == "mine.example"


def test_the_bulk_path_omits_a_platform_key_with_no_platform_row(
    db_session, org_id, cleanup
):
    """An organization value must not stand in for a ceiling nobody set.

    Skipping the row rather than reordering it is what makes this true: the key
    is simply absent, which is what "unconfigured" means on every other path,
    and `read_platform_webhook_ceiling` then falls back to the environment or
    to deny-all rather than to a tenant's value.
    """
    cleanup.append(
        _insert_outside_the_orm(
            db_session,
            domain=SettingDomain.automation,
            key="webhook_allowed_hosts",
            organization_id=org_id,
            scope=SettingScope.ORG_SPECIFIC,
            value_type=SettingValueType.string,
            value_text="attacker.example",
        )
    )

    values = settings_cache.get_domain_settings(
        db_session, SettingDomain.automation, organization_id=org_id
    )
    assert "webhook_allowed_hosts" not in values
