"""Static contract for ERP's ``party_person_catalog.v1`` provider migration."""

from __future__ import annotations

import ast
from pathlib import Path

from app.services.party_projection import (
    MAX_DISPLAY_NAME_LENGTH,
    MAX_PERSON_NAME_LENGTH,
    ORGANIZATION_PARTY_TYPE,
    PERSON_PARTY_TYPE,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    PROJECT_ROOT / "alembic" / "versions" / "20260824_party_person_projection.py"
)


def _literal(name: str) -> str | int:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            assert isinstance(node.value, ast.Constant)
            assert isinstance(node.value.value, (str, int))
            return node.value.value
    raise AssertionError(f"{name} is not a literal in {MIGRATION.name}")


def test_migration_extends_the_erp_head_and_copies_projection_constants() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260824_party_person_projection"' in source
    assert 'down_revision = "20260822_bank_fee_source_identity"' in source
    assert _literal("PERSON_PARTY_TYPE") == PERSON_PARTY_TYPE
    assert _literal("ORGANIZATION_PARTY_TYPE") == ORGANIZATION_PARTY_TYPE
    assert _literal("MAX_DISPLAY_NAME_LENGTH") == MAX_DISPLAY_NAME_LENGTH
    assert _literal("MAX_PERSON_NAME_LENGTH") == MAX_PERSON_NAME_LENGTH


def test_migration_imports_no_mutable_runtime_code() -> None:
    """A migration is a snapshot; importing app services would let it change."""
    source = MIGRATION.read_text(encoding="utf-8")
    assert "from app.services" not in source
    assert "import app.services" not in source
    assert "from app.models" not in source


def test_migration_hosts_only_the_kernel_identity_reference() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert '"parties"' in source
    assert '"party_persons"' in source
    for prohibited in (
        '"roles"',
        '"person_roles"',
        '"user_credentials"',
        '"auth_sessions"',
        '"employees"',
    ):
        assert prohibited not in source, prohibited


def test_both_projection_tables_are_rls_forced_and_tenant_bound() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in ("public.parties", "public.party_persons"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in source
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in source
    assert source.count("app_current_tenant_id()") >= 4
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO app_user" in source
    assert "for table in PROJECTED_TABLES:" in source


def test_the_grant_loop_covers_both_projection_tables() -> None:
    """A loop is only as good as what it loops over."""
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PROJECTED_TABLES"
            for target in node.targets
        ):
            assert isinstance(node.value, ast.Tuple)
            values = [
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant)
            ]
            assert values == ["public.parties", "public.party_persons"]
            return
    raise AssertionError("PROJECTED_TABLES is not a literal tuple")


def test_party_persons_carries_no_tenant_column_of_its_own() -> None:
    """A second tenant column would be a second answer to the same question."""
    source = MIGRATION.read_text(encoding="utf-8")
    start = source.index('if not _has_table("party_persons"):')
    persons_block = source[start : source.index("def _protect_catalog")]
    assert "party_persons" in persons_block
    assert "tenant_id" not in persons_block


def test_the_projection_refuses_drift_rather_than_overwriting_it() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "refusing to overwrite unknown projection drift" in source
    assert "_assert_existing_projection_is_truthful" in source
    assert "_assert_projection_is_representable" in source


def test_the_migration_verifies_the_effect_it_supplies() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'REQUIRES = ("party_person_catalog.v1",)' in source
    assert "require_prerequisites(op.get_bind(), REQUIRES)" in source


def test_the_binding_names_this_revision() -> None:
    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    bindings = {
        binding.prerequisite: binding.provider_revision
        for binding in ASSEMBLY_PREREQUISITE_BINDINGS
    }
    assert bindings["party_person_catalog.v1"] == "20260824_party_person_projection"
