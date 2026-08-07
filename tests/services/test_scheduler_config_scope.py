"""
The Celery bootstrap settings read is platform-scoped and spec-checked.

``scheduler_config._get_setting_value`` is a third direct reader of
``domain_settings``, alongside ``settings_spec.resolve_value`` and
``settings_cache``. It is legitimately separate — it runs while the Celery app
is being configured, before any tenant exists, and must not depend on the Redis
cache whose URL it is reading — but it was not legitimately *different*:

- it carried no organization predicate, so one tenant's row could reconfigure
  every worker's broker, result backend or timezone; and
- it applied none of the spec rules the other paths apply, so a value the
  settings screen rejects (``beat_max_loop_interval = 0``, below the spec
  minimum of 1) reached the Celery config from here.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.domain_settings import (
    DomainSetting,
    SettingDomain,
    SettingScope,
    SettingValueType,
)
from app.services.scheduler_config import _effective_int, _get_setting_value
from app.services.settings_spec import get_spec, resolve_value

DOMAIN = SettingDomain.scheduler


@pytest.fixture()
def store(db_session):
    """Write settings rows and remove them again afterwards.

    These tests must use the real spec keys (`broker_url`,
    `beat_max_loop_interval`) because the point is what the registered spec does
    to the stored value — so, unlike a canary that can pick a random key, they
    have to cope with rows other tests left in the shared test database. Any
    pre-existing row for a key under test is deactivated for the duration and
    reinstated afterwards, which every reader honours (`is_active` is in all
    three queries) and which nothing else in the file has to know about.
    """
    created: list[DomainSetting] = []
    suppressed: list[DomainSetting] = []

    def _store(
        key: str,
        value_text: str,
        organization_id: uuid.UUID | None,
        value_type: SettingValueType = SettingValueType.string,
    ) -> DomainSetting:
        for existing in db_session.scalars(
            select(DomainSetting)
            .where(DomainSetting.domain == DOMAIN)
            .where(DomainSetting.key == key)
            .where(DomainSetting.is_active.is_(True))
        ).all():
            existing.is_active = False
            suppressed.append(existing)
        setting = DomainSetting(
            domain=DOMAIN,
            key=key,
            organization_id=organization_id,
            scope=(
                SettingScope.GLOBAL
                if organization_id is None
                else SettingScope.ORG_SPECIFIC
            ),
            value_type=value_type,
            value_text=value_text,
            is_active=True,
        )
        db_session.add(setting)
        db_session.commit()
        db_session.refresh(setting)
        created.append(setting)
        return setting

    yield _store
    for setting in created:
        db_session.delete(setting)
    for setting in suppressed:
        setting.is_active = True
    db_session.commit()


def test_a_tenants_row_cannot_configure_the_worker_fleet(db_session, store):
    """Only the platform row is eligible for process-wide Celery config."""
    store("broker_url", "redis://tenant-a-broker:6379/0", uuid.uuid4())

    assert _get_setting_value(db_session, DOMAIN, "broker_url") is None


def test_the_platform_row_is_the_one_that_is_read(db_session, store):
    """Scoping the read must not disable it."""
    store("broker_url", "redis://tenant-a-broker:6379/0", uuid.uuid4())
    store("broker_url", "redis://platform-broker:6379/0", None)

    assert (
        _get_setting_value(db_session, DOMAIN, "broker_url")
        == "redis://platform-broker:6379/0"
    )


def test_a_value_the_spec_rejects_does_not_reach_celery(db_session, store, monkeypatch):
    """``beat_max_loop_interval = 0`` is below the spec minimum; 0 busy-loops beat."""
    key = "beat_max_loop_interval"
    spec = get_spec(DOMAIN, key)
    assert spec is not None and spec.min_value == 1

    monkeypatch.delenv("CELERY_BEAT_MAX_LOOP_INTERVAL", raising=False)
    store(key, "0", None, value_type=SettingValueType.integer)

    assert _get_setting_value(db_session, DOMAIN, key) == spec.default
    assert (
        _effective_int(db_session, DOMAIN, key, "CELERY_BEAT_MAX_LOOP_INTERVAL", 5) == 5
    )

    # The same answer the other read path gives for the same row.
    assert resolve_value(db_session, DOMAIN, key) == spec.default


def test_a_valid_platform_value_still_wins_over_the_default(
    db_session, store, monkeypatch
):
    key = "beat_max_loop_interval"
    monkeypatch.delenv("CELERY_BEAT_MAX_LOOP_INTERVAL", raising=False)
    store(key, "17", None, value_type=SettingValueType.integer)

    assert (
        _effective_int(db_session, DOMAIN, key, "CELERY_BEAT_MAX_LOOP_INTERVAL", 5)
        == 17
    )
