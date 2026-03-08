"""Tests for the unified feature flag service."""

import uuid

import pytest

from app.models.domain_settings import (
    DomainSetting,
    SettingDomain,
    SettingScope,
    SettingValueType,
)
from app.models.feature_flag import (
    FeatureFlagCategory,
    FeatureFlagRegistry,
    FeatureFlagStatus,
)
from app.services.feature_flag_service import FeatureFlagService, _cache_delete_pattern

ORG_A = uuid.UUID("00000000-0000-0000-0000-aaaaaaaa0001")
ORG_B = uuid.UUID("00000000-0000-0000-0000-bbbbbbbb0001")


@pytest.fixture(autouse=True)
def _clear_ff_cache():
    """Clear feature flag cache before each test."""
    _cache_delete_pattern("ff:*")
    yield
    _cache_delete_pattern("ff:*")


@pytest.fixture()
def service(db_session):
    return FeatureFlagService(db_session)


def _seed_registry(
    db, flag_key, default_enabled=False, category=FeatureFlagCategory.MODULE
):
    """Seed a registry entry."""
    entry = FeatureFlagRegistry(
        flag_key=flag_key,
        label=flag_key.replace("_", " ").title(),
        description="Test flag",
        category=category,
        default_enabled=default_enabled,
    )
    db.add(entry)
    db.flush()
    return entry


def _seed_setting(db, flag_key, enabled, org_id=None):
    """Seed a domain_settings row for a feature flag."""
    setting = DomainSetting(
        domain=SettingDomain.features,
        key=flag_key,
        organization_id=org_id,
        scope=SettingScope.ORG_SPECIFIC if org_id else SettingScope.GLOBAL,
        value_type=SettingValueType.boolean,
        value_text="true" if enabled else "false",
        is_active=True,
    )
    db.add(setting)
    db.flush()
    return setting


class TestIsEnabled:
    """Test the flag resolution chain."""

    def test_unknown_flag_returns_false(self, service):
        assert service.is_enabled(ORG_A, "nonexistent_flag") is False

    def test_registry_default_true(self, db_session, service):
        _seed_registry(db_session, "enable_test", default_enabled=True)
        assert service.is_enabled(ORG_A, "enable_test") is True

    def test_registry_default_false(self, db_session, service):
        _seed_registry(db_session, "enable_test", default_enabled=False)
        assert service.is_enabled(ORG_A, "enable_test") is False

    def test_global_setting_overrides_registry(self, db_session, service):
        _seed_registry(db_session, "enable_test", default_enabled=True)
        _seed_setting(db_session, "enable_test", enabled=False, org_id=None)
        assert service.is_enabled(ORG_A, "enable_test") is False

    def test_org_setting_overrides_global(self, db_session, service):
        _seed_registry(db_session, "enable_test", default_enabled=False)
        _seed_setting(db_session, "enable_test", enabled=False, org_id=None)
        _seed_setting(db_session, "enable_test", enabled=True, org_id=ORG_A)
        assert service.is_enabled(ORG_A, "enable_test") is True

    def test_org_override_isolated(self, db_session, service):
        """Org A override should not affect Org B."""
        _seed_registry(db_session, "enable_test", default_enabled=False)
        _seed_setting(db_session, "enable_test", enabled=True, org_id=ORG_A)
        assert service.is_enabled(ORG_A, "enable_test") is True
        assert service.is_enabled(ORG_B, "enable_test") is False

    def test_archived_flag_always_false(self, db_session, service):
        entry = _seed_registry(db_session, "enable_test", default_enabled=True)
        entry.status = FeatureFlagStatus.ARCHIVED
        db_session.flush()
        assert service.is_enabled(ORG_A, "enable_test") is False


class TestToggle:
    """Test toggling flags."""

    def test_toggle_creates_org_setting(self, db_session, service):
        _seed_registry(db_session, "enable_test", default_enabled=False)
        service.toggle(ORG_A, "enable_test", True)
        assert service.is_enabled(ORG_A, "enable_test") is True

    def test_toggle_updates_existing(self, db_session, service):
        _seed_registry(db_session, "enable_test", default_enabled=False)
        service.toggle(ORG_A, "enable_test", True)
        assert service.is_enabled(ORG_A, "enable_test") is True
        service.toggle(ORG_A, "enable_test", False)
        # Clear cache to see updated value
        _cache_delete_pattern("ff:*")
        assert service.is_enabled(ORG_A, "enable_test") is False

    def test_toggle_unknown_flag_raises(self, service):
        with pytest.raises(ValueError, match="Unknown feature flag"):
            service.toggle(ORG_A, "nonexistent", True)

    def test_toggle_archived_flag_raises(self, db_session, service):
        entry = _seed_registry(db_session, "enable_test")
        entry.status = FeatureFlagStatus.ARCHIVED
        db_session.flush()
        with pytest.raises(ValueError, match="Cannot toggle archived"):
            service.toggle(ORG_A, "enable_test", True)

    def test_toggle_global_scope(self, db_session, service):
        _seed_registry(db_session, "enable_test", default_enabled=False)
        service.toggle(ORG_A, "enable_test", True, scope="global")
        _cache_delete_pattern("ff:*")
        # Both orgs should see it
        assert service.is_enabled(ORG_A, "enable_test") is True
        _cache_delete_pattern("ff:*")
        assert service.is_enabled(ORG_B, "enable_test") is True


class TestRegister:
    """Test flag registration."""

    def test_register_new_flag(self, db_session, service):
        flag = service.register_flag(
            "enable_new",
            "New Feature",
            "A new feature",
            FeatureFlagCategory.EXPERIMENTAL,
        )
        assert flag.flag_key == "enable_new"
        assert flag.label == "New Feature"
        assert flag.category == FeatureFlagCategory.EXPERIMENTAL

    def test_register_updates_existing(self, db_session, service):
        service.register_flag("enable_new", "Old Label")
        flag = service.register_flag("enable_new", "New Label")
        assert flag.label == "New Label"

    def test_register_reactivates_archived(self, db_session, service):
        flag = service.register_flag("enable_new", "Test")
        service.archive_flag("enable_new")
        db_session.flush()
        flag = service.register_flag("enable_new", "Test Updated")
        assert flag.status == FeatureFlagStatus.ACTIVE


class TestGetAllFlags:
    """Test listing all flags."""

    def test_returns_flags_with_resolved_state(self, db_session, service):
        _seed_registry(db_session, "enable_a", default_enabled=True)
        _seed_registry(db_session, "enable_b", default_enabled=False)
        _seed_setting(db_session, "enable_b", enabled=True, org_id=ORG_A)

        flags = service.get_all_flags(ORG_A)
        by_key = {f.flag_key: f for f in flags}

        assert by_key["enable_a"].enabled is True
        assert by_key["enable_a"].is_org_override is False

        assert by_key["enable_b"].enabled is True
        assert by_key["enable_b"].is_org_override is True

    def test_excludes_archived_by_default(self, db_session, service):
        _seed_registry(db_session, "enable_active")
        entry = _seed_registry(db_session, "enable_archived")
        entry.status = FeatureFlagStatus.ARCHIVED
        db_session.flush()

        flags = service.get_all_flags(ORG_A)
        keys = {f.flag_key for f in flags}
        assert "enable_active" in keys
        assert "enable_archived" not in keys


class TestArchive:
    """Test archiving."""

    def test_archive_sets_status(self, db_session, service):
        _seed_registry(db_session, "enable_test", default_enabled=True)
        service.archive_flag("enable_test")
        db_session.flush()

        entry = service.get_registry_entry("enable_test")
        assert entry is not None
        assert entry.status == FeatureFlagStatus.ARCHIVED


class TestModuleLevelHelpers:
    """Test module-level convenience functions."""

    def test_is_feature_enabled_delegates_to_service(self, db_session):
        from app.services.feature_flags import is_feature_enabled

        _seed_registry(db_session, "enable_test", default_enabled=True)
        _seed_setting(db_session, "enable_test", enabled=False, org_id=ORG_A)
        assert is_feature_enabled(db_session, ORG_A, "enable_test") is False

    def test_is_feature_enabled_uses_registry_default(self, db_session):
        from app.services.feature_flags import is_feature_enabled

        _seed_registry(db_session, "enable_test", default_enabled=True)
        assert is_feature_enabled(db_session, ORG_A, "enable_test") is True
