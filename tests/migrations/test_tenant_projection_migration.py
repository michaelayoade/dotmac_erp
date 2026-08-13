"""Static contract for ERP's kernel-compatible tenant projection migration."""

from __future__ import annotations

import ast
from pathlib import Path

from app.services.tenant_projection import (
    MAX_TENANT_NAME_LENGTH,
    TENANT_SLUG_PREFIX,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = PROJECT_ROOT / "alembic" / "versions" / "20260813_tenant_projection.py"


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


def test_migration_is_the_erp_head_and_copies_projection_constants() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260813_tenant_projection"' in source
    assert 'down_revision = "20260812_merge_expand_withdrawal"' in source
    assert _literal("TENANT_SLUG_PREFIX") == TENANT_SLUG_PREFIX
    assert _literal("MAX_TENANT_NAME_LENGTH") == MAX_TENANT_NAME_LENGTH


def test_migration_hosts_only_the_kernel_tenant_catalog() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert '"tenants"' in source
    assert '"tenant_domains"' in source
    for prohibited in (
        '"people"',
        '"roles"',
        '"person_roles"',
        '"user_credentials"',
        '"auth_sessions"',
        '"audit_events"',
    ):
        assert prohibited not in source
    assert "dotmac_kernel" not in source
    assert "from app.services" not in source
    assert "import app.services" not in source


def test_platform_catalog_is_not_rls_scoped_or_publicly_writable() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" not in source
    assert "FORCE ROW LEVEL SECURITY" not in source
    assert "REVOKE ALL ON public.tenants FROM PUBLIC" in source
    assert "REVOKE ALL ON public.tenant_domains FROM PUBLIC" in source
    assert "REVOKE ALL ON FUNCTION public.app_current_tenant_id() FROM PUBLIC" in source


def test_migration_hosts_the_exact_module_rls_scope_function() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "_create_or_adopt_tenant_function" in source
    assert "CREATE FUNCTION public.app_current_tenant_id()" in source
    assert "current_setting('app.current_tenant', true)" in source
    assert "invalid_text_representation" in source


def test_migration_refuses_drift_instead_of_overwriting_unknown_rows() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "_assert_existing_projection_is_truthful" in source
    assert "_assert_column_contracts" in source
    assert "_index_column_sets" in source
    assert "ON CONFLICT" not in source
    assert "UPDATE public.tenants" not in source


def test_downgrade_is_an_explicit_forward_fix_boundary() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "forward-fix only" in source
    assert 'op.drop_table("tenants"' not in source
    assert 'op.drop_table("tenant_domains"' not in source
