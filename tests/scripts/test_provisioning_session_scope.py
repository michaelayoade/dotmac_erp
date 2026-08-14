from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.models.domain_settings import SettingDomain, SettingValueType
from app.services.settings_spec import SettingSpec


@contextmanager
def _session(db):
    yield db


def test_create_org_commits_org_and_tax_seed_in_separate_sessions(monkeypatch):
    from scripts import create_org

    org_id = UUID("00000000-0000-0000-0000-000000000042")
    cross_db = MagicMock()
    org_db = MagicMock()
    captured_org_ids: list[UUID] = []
    cross_db.query.return_value.filter.return_value.first.return_value = None
    cross_db.add.side_effect = lambda org: setattr(org, "organization_id", org_id)

    monkeypatch.setattr(
        create_org.sys,
        "argv",
        ["create_org.py", "--code", "DOT", "--name", "Dotmac"],
    )
    monkeypatch.setattr(create_org, "cross_org_session", lambda: _session(cross_db))

    def session_for_org(target_org_id):
        captured_org_ids.append(target_org_id)
        return _session(org_db)

    monkeypatch.setattr(create_org, "session_for_org", session_for_org)
    monkeypatch.setattr(
        create_org,
        "get_country_config",
        lambda country: SimpleNamespace(country_name="Nigeria"),
    )
    seed = MagicMock(
        return_value=SimpleNamespace(
            categories_created=1,
            accounts_created=2,
            jurisdictions_created=3,
            tax_codes_created=4,
            default_jurisdiction_id=None,
        )
    )
    monkeypatch.setattr(create_org, "seed_default_tax_data", seed)
    reconcile = MagicMock()
    monkeypatch.setattr(create_org, "reconcile_organization_tenant", reconcile)

    create_org.main()

    cross_db.commit.assert_called_once_with()
    reconcile.assert_called_once_with(cross_db, cross_db.add.call_args.args[0])
    assert captured_org_ids == [org_id]
    seed.assert_called_once_with(org_db, org_id, country_code="NG")
    org_db.commit.assert_called_once_with()


def test_seed_nigeria_resolves_cross_org_then_commits_per_org(monkeypatch):
    from scripts import seed_nigeria

    org_id = UUID("00000000-0000-0000-0000-000000000042")
    org = SimpleNamespace(organization_id=org_id, organization_code="DOT")
    cross_db = MagicMock()
    org_db = MagicMock()
    captured_org_ids: list[UUID] = []

    monkeypatch.setattr(seed_nigeria, "parse_args", lambda: SimpleNamespace())
    monkeypatch.setattr(seed_nigeria, "cross_org_session", lambda: _session(cross_db))
    monkeypatch.setattr(seed_nigeria, "resolve_orgs", lambda db, args: [org])

    def session_for_org(target_org_id):
        captured_org_ids.append(target_org_id)
        return _session(org_db)

    monkeypatch.setattr(seed_nigeria, "session_for_org", session_for_org)
    monkeypatch.setattr(
        seed_nigeria,
        "seed_nigeria_tax_data",
        lambda db, target_org_id: SimpleNamespace(
            currency_created=1,
            categories_created=2,
            accounts_created=3,
            jurisdictions_created=4,
            tax_codes_created=5,
        ),
    )

    seed_nigeria.main()

    assert captured_org_ids == [org_id]
    org_db.commit.assert_called_once_with()
    cross_db.commit.assert_not_called()


def test_settings_sync_upserts_inside_the_requested_tenant_session(monkeypatch):
    from scripts import settings_sync

    org_id = UUID("00000000-0000-0000-0000-000000000042")
    db = MagicMock()
    service = MagicMock()
    captured_org_ids: list[UUID] = []
    spec = SettingSpec(
        domain=SettingDomain.auth,
        key="jwt_algorithm",
        env_var="JWT_ALGORITHM",
        value_type=SettingValueType.string,
        default="HS256",
    )

    monkeypatch.setattr(
        settings_sync,
        "parse_args",
        lambda: SimpleNamespace(
            org_id=str(org_id), dry_run=False, allow_plaintext=False
        ),
    )
    monkeypatch.setattr(settings_sync, "load_dotenv", lambda: None)
    monkeypatch.setattr(settings_sync, "SETTINGS_SPECS", [spec])
    monkeypatch.setattr(
        settings_sync, "DOMAIN_SETTINGS_SERVICE", {SettingDomain.auth: service}
    )
    monkeypatch.setenv("JWT_ALGORITHM", "HS512")

    def session_for_org(target_org_id):
        captured_org_ids.append(target_org_id)
        return _session(db)

    monkeypatch.setattr(settings_sync, "session_for_org", session_for_org)

    settings_sync.main()

    assert captured_org_ids == [org_id]
    service.upsert_by_key.assert_called_once()
    assert service.upsert_by_key.call_args.args[:2] == (db, "jwt_algorithm")
    assert service.upsert_by_key.call_args.kwargs == {"organization_id": org_id}


def test_settings_sync_dry_run_redacts_plaintext_secrets(monkeypatch, capsys):
    from scripts import settings_sync

    org_id = UUID("00000000-0000-0000-0000-000000000042")
    plaintext = "test-only-plaintext-secret"
    spec = SettingSpec(
        domain=SettingDomain.auth,
        key="jwt_secret",
        env_var="JWT_SECRET",
        value_type=SettingValueType.string,
        default=None,
        is_secret=True,
    )

    monkeypatch.setattr(
        settings_sync,
        "parse_args",
        lambda: SimpleNamespace(org_id=str(org_id), dry_run=True, allow_plaintext=True),
    )
    monkeypatch.setattr(settings_sync, "load_dotenv", lambda: None)
    monkeypatch.setattr(settings_sync, "SETTINGS_SPECS", [spec])
    monkeypatch.setattr(
        settings_sync, "session_for_org", lambda target_org_id: _session(MagicMock())
    )
    monkeypatch.setenv("JWT_SECRET", plaintext)

    settings_sync.main()

    output = capsys.readouterr().out
    assert plaintext not in output
    assert "JWT_SECRET=<redacted>" in output


def test_seed_rbac_splits_global_catalog_from_tenant_admin_assignment(monkeypatch):
    from scripts import seed_rbac

    org_id = UUID("00000000-0000-0000-0000-000000000042")
    admin_role_id = UUID("00000000-0000-0000-0000-000000000043")
    person_id = UUID("00000000-0000-0000-0000-000000000044")
    person = SimpleNamespace(id=person_id)
    cross_db = MagicMock()
    org_db = MagicMock()
    events: list[str] = []

    monkeypatch.setattr(
        seed_rbac,
        "parse_args",
        lambda: SimpleNamespace(
            admin_email="admin@example.com",
            admin_person_id=None,
            org_id=str(org_id),
            dry_run=False,
        ),
    )
    monkeypatch.setattr(seed_rbac, "load_dotenv", lambda: None)

    @contextmanager
    def cross_org_session():
        events.append("catalog-session")
        yield cross_db

    @contextmanager
    def session_for_org(target_org_id):
        events.append(f"tenant-session:{target_org_id}")
        yield org_db

    monkeypatch.setattr(seed_rbac, "cross_org_session", cross_org_session)
    monkeypatch.setattr(seed_rbac, "session_for_org", session_for_org)
    seed_catalog = MagicMock(return_value=admin_role_id)
    resolve_person = MagicMock(return_value=person)
    ensure_person_role = MagicMock()
    monkeypatch.setattr(seed_rbac, "_seed_catalog", seed_catalog)
    monkeypatch.setattr(seed_rbac, "_resolve_admin_person", resolve_person)
    monkeypatch.setattr(seed_rbac, "_ensure_person_role", ensure_person_role)

    seed_rbac.main()

    assert events == ["catalog-session", f"tenant-session:{org_id}"]
    seed_catalog.assert_called_once_with(cross_db)
    cross_db.commit.assert_called_once_with()
    resolve_person.assert_called_once_with(
        org_db,
        organization_id=org_id,
        person_id=None,
        email="admin@example.com",
    )
    ensure_person_role.assert_called_once_with(org_db, person_id, admin_role_id)
    org_db.commit.assert_called_once_with()


def test_seed_rbac_requires_org_before_admin_assignment(monkeypatch):
    from scripts import seed_rbac

    cross_org_session = MagicMock()
    monkeypatch.setattr(
        seed_rbac,
        "parse_args",
        lambda: SimpleNamespace(
            admin_email="admin@example.com",
            admin_person_id=None,
            org_id=None,
            dry_run=False,
        ),
    )
    monkeypatch.setattr(seed_rbac, "load_dotenv", lambda: None)
    monkeypatch.setattr(seed_rbac, "cross_org_session", cross_org_session)

    with pytest.raises(SystemExit, match="--org-id is required"):
        seed_rbac.main()

    cross_org_session.assert_not_called()


def test_seed_rbac_admin_lookup_explicitly_predicates_organization():
    from scripts import seed_rbac

    org_id = UUID("00000000-0000-0000-0000-000000000042")
    person_id = UUID("00000000-0000-0000-0000-000000000044")
    db = MagicMock()
    person = SimpleNamespace(id=person_id)
    db.scalar.side_effect = [None, person]

    result = seed_rbac._resolve_admin_person(
        db,
        organization_id=org_id,
        person_id=str(person_id),
        email="admin@example.com",
    )

    assert result is person
    statements = [str(call.args[0]) for call in db.scalar.call_args_list]
    assert len(statements) == 2
    assert all("people.organization_id" in statement for statement in statements)


def test_settings_validation_keeps_organization_overrides_distinct(monkeypatch):
    from scripts import settings_validate

    org_a = UUID("00000000-0000-0000-0000-00000000000a")
    org_b = UUID("00000000-0000-0000-0000-00000000000b")
    spec = SettingSpec(
        domain=SettingDomain("test"),
        key="limit",
        env_var=None,
        value_type=SettingValueType.integer,
        default=10,
        min_value=1,
    )
    rows = [
        SimpleNamespace(
            organization_id=None,
            domain=spec.domain,
            key=spec.key,
            value_text="5",
            value_json=None,
        ),
        SimpleNamespace(
            organization_id=org_a,
            domain=spec.domain,
            key=spec.key,
            value_text="0",
            value_json=None,
        ),
        SimpleNamespace(
            organization_id=org_b,
            domain=spec.domain,
            key=spec.key,
            value_text="7",
            value_json=None,
        ),
    ]
    monkeypatch.setattr(settings_validate, "SETTINGS_SPECS", [spec])

    errors = settings_validate.validate_rows(rows)

    assert errors == [f"test.limit [org={org_a}]: value must be >= 1"]


def test_settings_validation_applies_global_fallback_per_org(monkeypatch):
    from scripts import settings_validate

    org_id = UUID("00000000-0000-0000-0000-00000000000a")
    spec = SettingSpec(
        domain=SettingDomain("test"),
        key="required",
        env_var=None,
        value_type=SettingValueType.string,
        default=None,
        required=True,
    )
    rows = [
        SimpleNamespace(
            organization_id=org_id,
            domain=SettingDomain("test"),
            key="another_key",
            value_text="present",
            value_json=None,
        )
    ]
    monkeypatch.setattr(settings_validate, "SETTINGS_SPECS", [spec])

    errors = settings_validate.validate_rows(rows)

    assert errors == [
        "test.required [global]: required value missing",
        f"test.required [org={org_id}]: required value missing",
    ]
