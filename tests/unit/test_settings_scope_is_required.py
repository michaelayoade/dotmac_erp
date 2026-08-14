"""Settings list/write operations always select one exact organization row."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.domain_settings import SettingDomain, SettingValueType
from app.schemas.settings import DomainSettingUpdate
from app.services.domain_settings import (
    AMBIENT,
    DomainSettings,
    SettingsScopeRequired,
    _resolve_operation_scope,
)
from app.services.settings_seed import seed_audit_settings


def _session(**info):
    """A stand-in whose only relevant surface is `.info`."""
    return SimpleNamespace(info=dict(info))


def test_an_unscoped_session_is_refused():
    with pytest.raises(SettingsScopeRequired):
        _resolve_operation_scope(
            _session(), AMBIENT, "DomainSettingService.upsert_by_key"
        )


def test_the_refusal_names_the_operation_and_the_two_valid_scopes():
    with pytest.raises(SettingsScopeRequired) as caught:
        _resolve_operation_scope(
            _session(), AMBIENT, "DomainSettingService.ensure_by_key"
        )

    message = str(caught.value)
    assert "DomainSettingService.ensure_by_key" in message
    assert "session_for_org" in message
    assert "organization_id=None" in message


def test_an_empty_organization_id_does_not_count_as_ambient_scope():
    with pytest.raises(SettingsScopeRequired):
        _resolve_operation_scope(_session(organization_id=None), AMBIENT, "operation")


def test_a_tenant_scoped_session_selects_that_tenant():
    organization_id = uuid4()
    assert (
        _resolve_operation_scope(
            _session(organization_id=organization_id), AMBIENT, "operation"
        )
        == organization_id
    )


def test_an_explicit_global_scope_selects_only_the_global_row():
    assert _resolve_operation_scope(_session(), None, "operation") is None


def test_a_cross_org_seed_context_maps_to_the_global_row():
    assert (
        _resolve_operation_scope(_session(allow_cross_org=True), AMBIENT, "operation")
        is None
    )


def test_a_cross_org_seed_context_overrides_a_primed_tenant():
    """Startup primes the default org before entering its global seed context."""
    assert (
        _resolve_operation_scope(
            _session(organization_id=uuid4(), allow_cross_org=True),
            AMBIENT,
            "operation",
        )
        is None
    )


def test_explicit_tenant_scope_must_match_the_session():
    with pytest.raises(SettingsScopeRequired):
        _resolve_operation_scope(
            _session(organization_id=uuid4()), uuid4(), "operation"
        )


def test_global_upsert_query_has_an_exact_null_organization_predicate(monkeypatch):
    service = DomainSettings(domain=SettingDomain.auth)
    db = MagicMock()
    db.info = {}
    existing = MagicMock(
        value_text="old",
        value_json=None,
        value_type=SettingValueType.string,
        is_secret=False,
        is_active=True,
        organization_id=None,
        id=uuid4(),
    )
    db.scalar.return_value = existing
    monkeypatch.setattr(
        "app.services.domain_settings._record_setting_history", MagicMock()
    )
    monkeypatch.setattr(
        "app.services.domain_settings.invalidate_setting_cache", MagicMock()
    )
    monkeypatch.setattr("app.services.domain_settings._log_setting_change", MagicMock())

    service.upsert_by_key(
        db,
        "jwt_algorithm",
        DomainSettingUpdate(value_text="new"),
        organization_id=None,
    )

    statement = db.scalar.call_args.args[0]
    assert "domain_settings.organization_id IS NULL" in str(statement)


def test_tenant_ensure_query_has_an_exact_organization_predicate():
    organization_id = uuid4()
    service = DomainSettings(domain=SettingDomain.auth)
    db = MagicMock()
    db.info = {"organization_id": organization_id}
    db.scalar.return_value = MagicMock()

    service.ensure_by_key(
        db,
        key="jwt_algorithm",
        value_type=SettingValueType.string,
    )

    statement = db.scalar.call_args.args[0]
    compiled = statement.compile()
    assert "domain_settings.organization_id =" in str(statement)
    assert organization_id in compiled.params.values()


def test_postgres_ensure_locks_the_complete_setting_identity_before_querying():
    organization_id = uuid4()
    service = DomainSettings(domain=SettingDomain.auth)
    db = MagicMock()
    db.info = {"organization_id": organization_id}
    db.get_bind.return_value.dialect.name = "postgresql"
    db.scalar.return_value = MagicMock()

    service.ensure_by_key(
        db,
        key="jwt_algorithm",
        value_type=SettingValueType.string,
    )

    lock_call = db.execute.call_args
    assert "pg_advisory_xact_lock" in str(lock_call.args[0])
    assert lock_call.args[1] == {
        "setting_identity": f"auth:jwt_algorithm:{organization_id}"
    }
    method_names = [call[0] for call in db.method_calls]
    assert method_names.index("execute") < method_names.index("scalar")


def test_global_settings_seed_states_and_restores_its_scope(monkeypatch):
    ensure_by_key = MagicMock()
    monkeypatch.setattr(
        "app.services.settings_seed.audit_settings.ensure_by_key", ensure_by_key
    )
    db = _session()

    seed_audit_settings(db)

    assert ensure_by_key.call_count == 5
    assert db.info.get("allow_cross_org") is False


def test_all_three_operations_use_the_exact_scope_predicate():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent.parent
        / "app"
        / "services"
        / "domain_settings.py"
    ).read_text(encoding="utf-8")

    assert source.count("_resolve_operation_scope(") == 4  # definition + 3 users
    assert source.count("_organization_predicate(org_id)") == 3


def test_a_real_service_call_refuses_before_querying():
    service = DomainSettings(domain=SettingDomain.auth)
    db = MagicMock()
    db.info = {}

    with pytest.raises(SettingsScopeRequired):
        service.ensure_by_key(db, key="anything", value_type=SettingValueType.string)

    db.scalar.assert_not_called()
