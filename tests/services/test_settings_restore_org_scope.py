"""
Cross-organization canaries for restoring a setting from history.

``restore_from_history`` re-creates a setting that was deleted. It used to build
the replacement row with no ``organization_id`` at all, so restoring one
organization's deleted setting produced a platform-wide row that every *other*
organization then inherited as its fallback — the same tenancy class as the
settings-cache leak, reached by a different mechanism.

The lookup that decides between "update the existing row" and "re-create it" was
unscoped for the same reason. The restore route runs on an RLS-bypass session,
so that lookup could return another tenant's live row and overwrite it.

Both depend on the history row knowing which organization it belongs to, which
``_record_setting_history`` never recorded even though the column exists.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.domain_settings import (
    DomainSetting,
    DomainSettingHistory,
    SettingChangeAction,
    SettingDomain,
    SettingScope,
    SettingValueType,
)
from app.schemas.settings import DomainSettingCreate
from app.services.domain_settings import DomainSettings, restore_from_history
from app.services.settings_cache import settings_cache

DOMAIN = SettingDomain.inventory


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    settings_cache.clear_inmemory()
    yield
    settings_cache.clear_inmemory()


@pytest.fixture()
def org_a():
    return uuid.UUID("aaaaaaaa-1111-1111-1111-11111111111a")


@pytest.fixture()
def org_b():
    return uuid.UUID("bbbbbbbb-1111-1111-1111-11111111111b")


@pytest.fixture()
def setting_key():
    """A key unique per test, so rows from other tests can never satisfy a read."""
    return f"restore_canary_{uuid.uuid4().hex[:10]}"


@pytest.fixture()
def service():
    return DomainSettings(DOMAIN)


def _create(db, service, key: str, value: str, organization_id: uuid.UUID | None):
    """Create a setting the way production does — organization from the session."""
    previous = db.info.get("organization_id")
    if organization_id is None:
        db.info.pop("organization_id", None)
    else:
        db.info["organization_id"] = organization_id
    try:
        return service.create(
            db,
            DomainSettingCreate(
                domain=DOMAIN,
                key=key,
                value_type=SettingValueType.string,
                value_text=value,
            ),
        )
    finally:
        if previous is None:
            db.info.pop("organization_id", None)
        else:
            db.info["organization_id"] = previous


def _delete_history_id(db, setting_id) -> str:
    entry = db.scalar(
        select(DomainSettingHistory)
        .where(DomainSettingHistory.setting_id == setting_id)
        .where(DomainSettingHistory.action == SettingChangeAction.DELETE)
    )
    assert entry is not None, "expected a DELETE history entry"
    return str(entry.id)


def _purge_row(db, setting: DomainSetting) -> None:
    """Hard-remove the setting row, leaving its history behind.

    This is what a restore has to cope with: the history entry outlives the row
    (organization teardown, a manual purge, a data migration), so the restore
    takes the re-create branch instead of updating a row in place.
    """
    db.delete(setting)
    db.commit()


def _rows_for(db, key: str) -> list[DomainSetting]:
    return list(
        db.scalars(
            select(DomainSetting)
            .where(DomainSetting.domain == DOMAIN)
            .where(DomainSetting.key == key)
        ).all()
    )


# ---------------------------------------------------------------------------
# The headline defect: restore drops the organization
# ---------------------------------------------------------------------------


def test_restoring_a_deleted_org_setting_recreates_it_for_that_org(
    db_session, service, org_a, org_b, setting_key
):
    """A restored org-specific setting belongs to that org, not to everyone."""
    setting = _create(db_session, service, setting_key, "A-value", org_a)
    assert setting.organization_id == org_a

    service.delete(db_session, str(setting.id))
    history_id = _delete_history_id(db_session, setting.id)
    _purge_row(db_session, setting)

    restored = restore_from_history(db_session, history_id)

    assert restored.organization_id == org_a
    assert restored.scope == SettingScope.ORG_SPECIFIC
    assert restored.value_text == "A-value"

    # And the decisive consequence: no platform-wide row was created, so another
    # organization does not inherit the restored value as its fallback.
    assert [row.organization_id for row in _rows_for(db_session, setting_key)] == [
        org_a
    ]
    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, "B-default", organization_id=org_b
        )
        == "B-default"
    )


def test_restore_does_not_overwrite_another_organizations_live_setting(
    db_session, service, org_a, org_b, setting_key
):
    """Restoring org A's history must never write org B's row."""
    setting_a = _create(db_session, service, setting_key, "A-value", org_a)
    service.delete(db_session, str(setting_a.id))
    history_id = _delete_history_id(db_session, setting_a.id)
    _purge_row(db_session, setting_a)

    setting_b = _create(db_session, service, setting_key, "B-value", org_b)

    restore_from_history(db_session, history_id)

    db_session.refresh(setting_b)
    assert setting_b.organization_id == org_b
    assert setting_b.value_text == "B-value", "org B's live setting was overwritten"

    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, organization_id=org_b
        )
        == "B-value"
    )
    assert (
        settings_cache.get_setting_value(
            db_session, DOMAIN, setting_key, organization_id=org_a
        )
        == "A-value"
    )


def test_restore_keeps_each_scope_where_it_belongs(
    db_session, service, org_a, setting_key
):
    """An org restore stays org-specific; a platform restore stays platform-wide."""
    global_key = f"{setting_key}_global"

    org_setting = _create(db_session, service, setting_key, "A-value", org_a)
    service.delete(db_session, str(org_setting.id))
    org_history_id = _delete_history_id(db_session, org_setting.id)
    _purge_row(db_session, org_setting)

    global_setting = _create(db_session, service, global_key, "platform-value", None)
    assert global_setting.organization_id is None
    service.delete(db_session, str(global_setting.id))
    global_history_id = _delete_history_id(db_session, global_setting.id)
    _purge_row(db_session, global_setting)

    assert restore_from_history(db_session, org_history_id).organization_id == org_a
    assert restore_from_history(db_session, global_history_id).organization_id is None


# ---------------------------------------------------------------------------
# The history row has to carry the organization for any of the above to work
# ---------------------------------------------------------------------------


def test_history_records_the_owning_organization(
    db_session, service, org_a, setting_key
):
    """Every history row denormalizes the org, exactly as it does domain/key."""
    setting = _create(db_session, service, setting_key, "A-value", org_a)
    service.delete(db_session, str(setting.id))

    entries = list(
        db_session.scalars(
            select(DomainSettingHistory).where(
                DomainSettingHistory.setting_id == setting.id
            )
        ).all()
    )
    assert entries, "expected CREATE and DELETE history entries"
    assert {entry.organization_id for entry in entries} == {org_a}


def test_history_written_before_the_org_was_recorded_still_restores_in_scope(
    db_session, service, org_a, setting_key
):
    """A legacy NULL-org history row falls back to the setting it points at.

    Rows written before ``_record_setting_history`` recorded the organization
    are indistinguishable from genuine platform-wide entries. Reading the
    organization off the linked setting keeps those restores from landing on the
    platform row — which every organization would then inherit.
    """
    global_setting = _create(db_session, service, setting_key, "platform-value", None)

    org_setting = _create(db_session, service, setting_key, "A-value", org_a)
    service.delete(db_session, str(org_setting.id))
    history_id = _delete_history_id(db_session, org_setting.id)

    # Simulate history written before the organization was recorded.
    entry = db_session.get(DomainSettingHistory, uuid.UUID(history_id))
    entry.organization_id = None
    db_session.commit()

    restored = restore_from_history(db_session, history_id)

    assert restored.organization_id == org_a
    assert restored.is_active is True

    db_session.refresh(global_setting)
    assert global_setting.value_text == "platform-value", (
        "the platform-wide row was rewritten with one organization's value"
    )
