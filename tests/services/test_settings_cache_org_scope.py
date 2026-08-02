"""
Cross-organization canaries for the settings cache.

These pin the tenant-isolation contract of ``app.services.settings_cache``:
a cached settings read is always scoped to exactly one organization (or,
explicitly, to the platform-wide rows), and no cache entry is ever shared
between two organizations.

The defect these guard against: ``get_setting_value`` took no organization,
queried ``domain_settings`` without an organization predicate, and cached the
arbitrary row it got back under a key with no tenant component — so one
organization's value was served to another.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.domain_settings import (
    DomainSetting,
    SettingDomain,
    SettingScope,
    SettingValueType,
)
from app.schemas.settings import DomainSettingCreate, DomainSettingUpdate
from app.services.domain_settings import DomainSettings
from app.services.settings_cache import (
    get_global_cached_setting,
    invalidate_setting_cache,
    settings_cache,
)

DOMAIN = SettingDomain.inventory
UTC = timezone.utc


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    """Every canary starts and ends with an empty process cache."""
    settings_cache.clear_inmemory()
    yield
    settings_cache.clear_inmemory()


@pytest.fixture()
def org_a():
    return uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000a")


@pytest.fixture()
def org_b():
    return uuid.UUID("bbbbbbbb-0000-0000-0000-00000000000b")


@pytest.fixture()
def setting_key():
    """A key unique per test, so rows from other tests can never satisfy a read."""
    return f"canary_{uuid.uuid4().hex[:10]}"


def _add_setting(
    db,
    key: str,
    value: str,
    organization_id: uuid.UUID | None,
    *,
    domain: SettingDomain = DOMAIN,
    updated_at: datetime | None = None,
) -> DomainSetting:
    setting = DomainSetting(
        domain=domain,
        key=key,
        organization_id=organization_id,
        scope=(
            SettingScope.GLOBAL
            if organization_id is None
            else SettingScope.ORG_SPECIFIC
        ),
        value_type=SettingValueType.string,
        value_text=value,
        is_active=True,
    )
    db.add(setting)
    db.commit()
    db.refresh(setting)
    if updated_at is not None:
        setting.updated_at = updated_at
        db.commit()
        db.refresh(setting)
    return setting


# ---------------------------------------------------------------------------
# Isolation between organizations
# ---------------------------------------------------------------------------


def test_each_organization_reads_its_own_value(db_session, org_a, org_b, setting_key):
    _add_setting(db_session, setting_key, "value-A", org_a)
    _add_setting(db_session, setting_key, "value-B", org_b)

    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, organization_id=org_a
        )
        == "value-A"
    )
    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, organization_id=org_b
        )
        == "value-B"
    )


def test_reading_org_a_first_does_not_change_what_org_b_sees(
    db_session, org_a, org_b, setting_key
):
    """Order must not matter: a warm cache for A cannot answer B's read."""
    _add_setting(db_session, setting_key, "value-A", org_a)
    _add_setting(db_session, setting_key, "value-B", org_b)

    # Warm A first, then B.
    settings_cache.get_setting_value(
        db_session, DOMAIN, setting_key, organization_id=org_a
    )
    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, organization_id=org_b
        )
        == "value-B"
    )

    # And the reverse order, from cold.
    settings_cache.clear_inmemory()
    settings_cache.get_setting_value(
        db_session, DOMAIN, setting_key, organization_id=org_b
    )
    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, organization_id=org_a
        )
        == "value-A"
    )


def test_org_with_no_row_gets_the_declared_default_not_another_orgs_value(
    db_session, org_a, org_b, setting_key
):
    """The sharpest leak canary: B has no row anywhere and must not see A's."""
    _add_setting(db_session, setting_key, "value-A", org_a)

    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, organization_id=org_a
        )
        == "value-A"
    )
    assert (
        settings_cache.get_setting_value(
            db_session,
            DOMAIN,
            setting_key,
            "declared-default",
            organization_id=org_b,
        )
        == "declared-default"
    )


def test_two_organizations_never_share_a_cache_entry(
    db_session, org_a, org_b, setting_key
):
    _add_setting(db_session, setting_key, "value-A", org_a)
    _add_setting(db_session, setting_key, "value-B", org_b)

    settings_cache.get_setting_value(
        db_session, DOMAIN, setting_key, organization_id=org_a
    )
    settings_cache.get_setting_value(
        db_session, DOMAIN, setting_key, organization_id=org_b
    )

    key_a = settings_cache._make_key(DOMAIN, org_a, setting_key)
    key_b = settings_cache._make_key(DOMAIN, org_b, setting_key)
    key_global = settings_cache._make_key(DOMAIN, None, setting_key)

    assert len({key_a, key_b, key_global}) == 3
    assert settings_cache._inmemory.get(key_a) == "value-A"
    assert settings_cache._inmemory.get(key_b) == "value-B"
    # Nothing was written to the platform-wide entry by either tenant read.
    assert settings_cache._inmemory.get(key_global) is None


# ---------------------------------------------------------------------------
# Resolution order: org row -> global row -> declared default
# ---------------------------------------------------------------------------


def test_org_row_outranks_the_global_row(db_session, org_a, setting_key):
    # Give the global row the newer timestamp so only scope precedence,
    # not recency, can explain the result.
    _add_setting(db_session, setting_key, "value-A", org_a)
    _add_setting(
        db_session,
        setting_key,
        "value-global",
        None,
        updated_at=datetime.now(UTC) + timedelta(days=1),
    )

    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, "declared-default", organization_id=org_a
        )
        == "value-A"
    )


def test_org_without_a_row_falls_back_to_global_then_to_the_declared_default(
    db_session, org_a, org_b, setting_key
):
    _add_setting(db_session, setting_key, "value-A", org_a)
    _add_setting(db_session, setting_key, "value-global", None)

    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, "declared-default", organization_id=org_b
        )
        == "value-global"
    )

    missing_key = f"{setting_key}_absent"
    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, missing_key, "declared-default", organization_id=org_b
        )
        == "declared-default"
    )


def test_inactive_rows_are_ignored(db_session, org_a, setting_key):
    setting = _add_setting(db_session, setting_key, "value-A", org_a)
    setting.is_active = False
    db_session.commit()

    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, "declared-default", organization_id=org_a
        )
        == "declared-default"
    )


# ---------------------------------------------------------------------------
# The platform-wide scope is a separate, deliberately named path
# ---------------------------------------------------------------------------


def test_global_read_is_a_separate_named_path(db_session, org_a, setting_key):
    _add_setting(db_session, setting_key, "value-A", org_a)
    _add_setting(db_session, setting_key, "value-global", None)

    assert (
        get_global_cached_setting(db_session, DOMAIN, setting_key, "declared-default")
        == "value-global"
    )


def test_global_read_never_sees_an_org_row(db_session, org_a, setting_key):
    _add_setting(db_session, setting_key, "value-A", org_a)

    assert (
        get_global_cached_setting(db_session, DOMAIN, setting_key, "declared-default")
        == "declared-default"
    )


def test_a_missing_organization_is_refused_not_treated_as_global(
    db_session, setting_key
):
    with pytest.raises(ValueError):
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, organization_id=None
        )
    with pytest.raises(ValueError):
        settings_cache.get_domain_settings(db_session, DOMAIN, organization_id=None)
    with pytest.raises(TypeError):
        settings_cache.get_setting_value(db_session, DOMAIN, setting_key)


def test_a_tenant_cannot_occupy_the_global_cache_scope(db_session, setting_key):
    """A tenant identifier of "global" must not land on the platform-wide key."""
    with pytest.raises(ValueError):
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, organization_id="global"
        )

    global_key = settings_cache._make_key(DOMAIN, None, setting_key)
    org_key = settings_cache._make_key(DOMAIN, uuid.uuid4(), setting_key)
    assert global_key != org_key
    assert global_key.endswith(":global")
    assert ":org=" in org_key


# ---------------------------------------------------------------------------
# Invalidation is scope-correct
# ---------------------------------------------------------------------------


def test_writing_org_a_refreshes_a_without_touching_b(
    db_session, org_a, org_b, setting_key
):
    setting_a = _add_setting(db_session, setting_key, "value-A", org_a)
    _add_setting(db_session, setting_key, "value-B", org_b)

    # Warm both.
    settings_cache.get_setting_value(
        db_session, DOMAIN, setting_key, organization_id=org_a
    )
    settings_cache.get_setting_value(
        db_session, DOMAIN, setting_key, organization_id=org_b
    )

    DomainSettings(DOMAIN).update(
        db_session,
        str(setting_a.id),
        DomainSettingUpdate(value_text="value-A2"),
    )

    # A must not be served stale data...
    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, organization_id=org_a
        )
        == "value-A2"
    )
    # ...and B's entry must be neither dropped nor corrupted.
    assert (
        settings_cache._inmemory.get(
            settings_cache._make_key(DOMAIN, org_b, setting_key)
        )
        == "value-B"
    )
    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, organization_id=org_b
        )
        == "value-B"
    )


def test_writing_the_global_row_refreshes_every_orgs_fallback(
    db_session, org_a, org_b, setting_key
):
    global_setting = _add_setting(db_session, setting_key, "value-global", None)

    # Both orgs resolve the global row and cache it under their own key.
    for org in (org_a, org_b):
        assert (
            settings_cache.get_setting_value(
                db_session, DOMAIN, setting_key, organization_id=org
            )
            == "value-global"
        )

    DomainSettings(DOMAIN).update(
        db_session,
        str(global_setting.id),
        DomainSettingUpdate(value_text="value-global-2"),
    )

    for org in (org_a, org_b):
        assert (
            settings_cache.get_setting_value(
                db_session, DOMAIN, setting_key, organization_id=org
            )
            == "value-global-2"
        )


def test_a_cached_fallback_does_not_mask_a_later_org_specific_write(
    db_session, org_b, setting_key
):
    _add_setting(db_session, setting_key, "value-global", None)

    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, organization_id=org_b
        )
        == "value-global"
    )

    # Create B's own row through the real write path, which is what performs
    # the scoped invalidation.
    db_session.info["organization_id"] = org_b
    try:
        DomainSettings(DOMAIN).create(
            db_session,
            DomainSettingCreate(
                domain=DOMAIN,
                key=setting_key,
                value_type=SettingValueType.string,
                value_text="value-B",
            ),
        )
    finally:
        db_session.info.pop("organization_id", None)

    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, organization_id=org_b
        )
        == "value-B"
    )


def test_a_cached_not_found_does_not_mask_a_later_org_specific_write(
    db_session, org_a, org_b, setting_key
):
    # B resolves nothing and caches the "not found" sentinel.
    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, "declared-default", organization_id=org_b
        )
        == "declared-default"
    )

    setting_b = _add_setting(db_session, setting_key, "value-B", org_b)
    invalidate_setting_cache(
        DOMAIN, setting_b.key, organization_id=setting_b.organization_id
    )

    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, "declared-default", organization_id=org_b
        )
        == "value-B"
    )
    # A still has nothing of its own and still gets its declared default.
    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, "declared-default", organization_id=org_a
        )
        == "declared-default"
    )


# ---------------------------------------------------------------------------
# The bulk domain read carries the same contract
# ---------------------------------------------------------------------------


def test_get_domain_settings_is_org_scoped(db_session, org_a, org_b, setting_key):
    shared_key = setting_key
    a_only_key = f"{setting_key}_a_only"

    _add_setting(db_session, shared_key, "value-A", org_a)
    _add_setting(db_session, shared_key, "value-B", org_b)
    _add_setting(db_session, a_only_key, "a-only", org_a)

    values_a = settings_cache.get_domain_settings(
        db_session, DOMAIN, organization_id=org_a
    )
    values_b = settings_cache.get_domain_settings(
        db_session, DOMAIN, organization_id=org_b
    )

    assert values_a[shared_key] == "value-A"
    assert values_b[shared_key] == "value-B"
    assert values_a[a_only_key] == "a-only"
    assert a_only_key not in values_b


def test_get_domain_settings_prefers_the_org_row_over_the_global_row(
    db_session, org_a, org_b, setting_key
):
    _add_setting(db_session, setting_key, "value-global", None)
    _add_setting(db_session, setting_key, "value-A", org_a)

    values_a = settings_cache.get_domain_settings(
        db_session, DOMAIN, organization_id=org_a
    )
    values_b = settings_cache.get_domain_settings(
        db_session, DOMAIN, organization_id=org_b
    )

    assert values_a[setting_key] == "value-A"
    assert values_b[setting_key] == "value-global"


def test_get_domain_settings_entries_are_not_shared_between_orgs(
    db_session, org_a, org_b, setting_key
):
    _add_setting(db_session, setting_key, "value-A", org_a)
    _add_setting(db_session, setting_key, "value-B", org_b)

    settings_cache.get_domain_settings(db_session, DOMAIN, organization_id=org_a)
    settings_cache.get_domain_settings(db_session, DOMAIN, organization_id=org_b)

    bulk_a = settings_cache._inmemory.get(settings_cache._make_key(DOMAIN, org_a))
    bulk_b = settings_cache._inmemory.get(settings_cache._make_key(DOMAIN, org_b))

    assert bulk_a[setting_key] == "value-A"
    assert bulk_b[setting_key] == "value-B"
