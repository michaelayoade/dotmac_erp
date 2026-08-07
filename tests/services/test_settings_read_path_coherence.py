"""
The two settings read paths must answer the same question the same way.

A setting can be read either through ``settings_spec.resolve_value`` (which
loads the row via ``DomainSettings.get_by_key``) or through the cached path in
``settings_cache``. They disagreed twice:

1. Value extraction. ``settings_cache._extract_value`` preferred ``value_json``
   and fell back to ``value_text``; ``settings_spec.extract_db_value`` does the
   opposite. A row carrying both — which the storage CHECK constraint permits
   for booleans — resolved differently depending on which path read it.

2. Spec enforcement. ``resolve_value`` applies the spec's ``value_type``,
   ``allowed`` set and ``min_value``/``max_value`` bounds and falls back to
   ``spec.default``; the cached path applied none of them, so it could serve an
   out-of-range or wrongly-typed value for a key the other path would have
   corrected.

These canaries pin both paths to a single answer per key.
"""

import uuid

import pytest

from app.models.domain_settings import (
    DomainSetting,
    SettingDomain,
    SettingScope,
    SettingValueType,
)
from app.services.settings_cache import settings_cache
from app.services.settings_spec import get_spec, resolve_value


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    settings_cache.clear_inmemory()
    yield
    settings_cache.clear_inmemory()


@pytest.fixture()
def org_id():
    """A fresh organization per test, so no other test's rows are in scope."""
    return uuid.uuid4()


@pytest.fixture()
def store(db_session, org_id):
    """Write org-scoped settings rows, and take them away again afterwards."""
    created: list[DomainSetting] = []

    def _store(
        domain: SettingDomain,
        key: str,
        value_type: SettingValueType,
        value_text: str | None = None,
        value_json: object | None = None,
    ) -> DomainSetting:
        kwargs: dict = {
            "domain": domain,
            "key": key,
            "organization_id": org_id,
            "scope": SettingScope.ORG_SPECIFIC,
            "value_type": value_type,
            "is_active": True,
        }
        if value_text is not None:
            kwargs["value_text"] = value_text
        if value_json is not None:
            kwargs["value_json"] = value_json
        setting = DomainSetting(**kwargs)
        db_session.add(setting)
        db_session.commit()
        db_session.refresh(setting)
        created.append(setting)
        return setting

    # ``resolve_value`` reads the organization off the session, the way a
    # request-scoped session is primed in production.
    db_session.info["organization_id"] = org_id
    yield _store
    db_session.info.pop("organization_id", None)
    for setting in created:
        db_session.delete(setting)
    db_session.commit()


def _both_paths(db_session, org_id, domain: SettingDomain, key: str):
    """(cached value, uncached value) for the same key."""
    cached = settings_cache.get_setting_value(
        db_session, domain, key, organization_id=org_id
    )
    uncached = resolve_value(db_session, domain, key)
    return cached, uncached


# ---------------------------------------------------------------------------
# 1. Value extraction
# ---------------------------------------------------------------------------


def test_a_row_with_both_columns_populated_reads_the_same_either_way(
    db_session, store, org_id
):
    """``value_text`` is the value; the cached path must not prefer value_json.

    Booleans are stored in both columns by ``_normalize_setting_values``, so a
    row whose two columns disagree is reachable — and used to resolve to True on
    one path and False on the other.
    """
    domain, key = SettingDomain.audit, "enabled"
    assert get_spec(domain, key) is not None, "test relies on a registered spec"

    store(domain, key, SettingValueType.boolean, value_text="false", value_json=True)

    cached, uncached = _both_paths(db_session, org_id, domain, key)
    assert cached == uncached
    assert cached is False


# ---------------------------------------------------------------------------
# 2. Spec enforcement on the cached path
# ---------------------------------------------------------------------------


def test_a_value_outside_the_spec_range_is_rejected_on_both_paths(
    db_session, store, org_id
):
    domain, key = SettingDomain.automation, "recurring_lookback_days"
    spec = get_spec(domain, key)
    assert spec is not None and spec.max_value is not None

    store(
        domain,
        key,
        SettingValueType.integer,
        value_text=str(spec.max_value + 1000),
    )

    cached, uncached = _both_paths(db_session, org_id, domain, key)
    assert cached == uncached
    assert cached == spec.default


def test_a_value_outside_the_spec_allowed_set_is_rejected_on_both_paths(
    db_session, store, org_id
):
    domain, key = SettingDomain.reporting, "default_export_format"
    spec = get_spec(domain, key)
    assert spec is not None and spec.allowed

    store(domain, key, SettingValueType.string, value_text="XML")

    cached, uncached = _both_paths(db_session, org_id, domain, key)
    assert cached == uncached
    assert cached == spec.default


def test_a_wrongly_typed_value_is_rejected_on_both_paths(db_session, store, org_id):
    domain, key = SettingDomain.automation, "workflow_max_actions_per_event"
    spec = get_spec(domain, key)
    assert spec is not None and spec.value_type == SettingValueType.integer

    store(domain, key, SettingValueType.string, value_text="not-a-number")

    cached, uncached = _both_paths(db_session, org_id, domain, key)
    assert cached == uncached
    assert cached == spec.default


def test_a_valid_value_survives_both_paths_unchanged(db_session, store, org_id):
    """The convergence must not swallow legitimate stored values."""
    domain, key = SettingDomain.automation, "recurring_lookback_days"
    spec = get_spec(domain, key)
    assert spec is not None

    store(domain, key, SettingValueType.integer, value_text="30")

    cached, uncached = _both_paths(db_session, org_id, domain, key)
    assert cached == uncached == 30
    assert cached != spec.default


def test_the_bulk_domain_read_agrees_with_the_single_key_read(
    db_session, store, org_id
):
    """``get_domain_settings`` must not be a third answer for the same key."""
    domain, key = SettingDomain.automation, "recurring_lookback_days"
    spec = get_spec(domain, key)
    assert spec is not None and spec.max_value is not None

    store(domain, key, SettingValueType.integer, value_text=str(spec.max_value + 1000))

    bulk = settings_cache.get_domain_settings(
        db_session, domain, organization_id=org_id
    )
    single = settings_cache.get_setting_value(
        db_session, domain, key, organization_id=org_id
    )

    assert bulk[key] == single == spec.default


def test_a_key_with_no_spec_still_gets_its_declared_type(db_session, store, org_id):
    """Unspecced keys keep the row's own ``value_type`` coercion.

    The cache is not limited to keys with a registered spec — feature-flag style
    keys are read through it too — so converging on the spec path must not turn
    a stored boolean into the truthy string ``"false"``.
    """
    key = f"unspecced_{uuid.uuid4().hex[:10]}"
    domain = SettingDomain.inventory
    assert get_spec(domain, key) is None

    store(domain, key, SettingValueType.boolean, value_text="false", value_json=False)

    value = settings_cache.get_setting_value(
        db_session, domain, key, organization_id=org_id
    )
    assert value is False

    store(domain, f"{key}_int", SettingValueType.integer, value_text="42")
    assert (
        settings_cache.get_setting_value(
            db_session, domain, f"{key}_int", organization_id=org_id
        )
        == 42
    )
