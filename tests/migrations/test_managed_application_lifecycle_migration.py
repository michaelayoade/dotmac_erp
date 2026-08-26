from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "20260817_managed_application_lifecycle.py"


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_operation_receipt_is_tenant_scoped_and_rls_forced() -> None:
    source = _source()

    assert 'sa.Column("organization_id"' in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "organization_id = get_current_organization_id()" in source
    assert "should_bypass_rls" not in source
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in source


def test_operation_receipt_enforces_person_organization_parity() -> None:
    source = _source()

    assert '["organization_id", "person_id"]' in source
    assert '["public.people.organization_id", "public.people.id"]' in source
    assert "uq_people_organization_id_id" in source


def test_external_binding_and_oidc_state_are_tenant_scoped() -> None:
    source = _source()

    assert "uq_federated_identity_org_provider_subject" in source
    assert "federated_identities_tenant_isolation" in source
    assert '"external_identity_binding_id"' in source
    assert "fk_sessions_external_identity_person" in source
    assert '"oidc_login_states"' in source
    assert "oidc_login_states_tenant_isolation" in source


def test_plan_identity_is_immutable_in_the_database() -> None:
    spec = importlib.util.spec_from_file_location("lifecycle_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert {
        "organization_id",
        "idempotency_key",
        "person_id",
        "desired_state",
        "provider_binding",
        "issuer",
        "subject",
        "target",
        "target_digest",
        "expected_state",
        "expected_state_digest",
        "plan_digest",
    }.issubset(set(module.IMMUTABLE_COLUMNS))
    assert "reject_application_lifecycle_plan_mutation" in _source()
