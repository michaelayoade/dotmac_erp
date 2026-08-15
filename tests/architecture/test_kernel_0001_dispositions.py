"""Kernel 0001 stays gated until every atomic effect has a disposition."""

from __future__ import annotations

import ast
from pathlib import Path

import dotmac_kernel
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DISPOSITIONS = PROJECT_ROOT / "docs" / "architecture" / "kernel-0001-dispositions.md"
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
LINEAGE_REHEARSAL = (
    PROJECT_ROOT / "tests" / "integration" / "test_kernel_lineage_rehearsal.py"
)

EXPECTED_TABLES = {
    "tenants",
    "tenant_domains",
    "people",
    "user_credentials",
    "auth_sessions",
    "roles",
    "person_roles",
    "audit_events",
}
EXPECTED_DB_ROLES = {"app_admin", "app_user", "platform_api"}


def _kernel_0001() -> Path:
    return (
        Path(dotmac_kernel.__file__).parent
        / "migrations"
        / "versions"
        / "20260504_0001_initial_tenant_schema.py"
    )


def _created_tables(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tables: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        if not (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "op"
            and function.attr == "create_table"
        ):
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            tables.add(first.value)
    return tables


def test_disposition_matrix_covers_the_pinned_revision_exactly() -> None:
    assert _created_tables(_kernel_0001()) == EXPECTED_TABLES
    document = DISPOSITIONS.read_text(encoding="utf-8")
    for table in EXPECTED_TABLES:
        assert f"`{table}`" in document
    for role in EXPECTED_DB_ROLES:
        assert f"`{role}`" in document
    assert "`app_current_tenant_id()`" in document
    assert "RLS for the six identity/audit tables" in document
    assert "Grants on identity/audit tables" in document


def test_the_kernel_lineage_is_still_not_composed() -> None:
    """`dotmac-files` IS composed as of the adoption; kernel `0001` never will be.

    This test used to assert that `version_locations` was absent entirely, which
    conflated two very different facts. Composing a MODULE lineage is the goal;
    composing the KERNEL lineage is the thing that can never happen, because
    kernel `0001` creates `public.tenants` and ERP owns that table.
    """
    import configparser

    # Parsed, not substring-matched. `alembic.ini` explains in a COMMENT why the
    # kernel lineage is absent, and a naive `"dotmac_kernel.migrations" not in
    # text` check matches that explanation — failing because the reason is
    # documented. Interpolation is off because the value contains `%(here)s`,
    # which Alembic injects and ConfigParser knows nothing about.
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ALEMBIC_INI)
    locations = parser["alembic"]["version_locations"].split()
    assert "dotmac_files.migrations:versions" in locations
    assert not [item for item in locations if "dotmac_kernel.migrations" in item]
    document = DISPOSITIONS.read_text(encoding="utf-8")
    assert "cannot stamp" in document.lower()
    assert "`fi_0001_stored_files`" in document
    assert "`0001_initial_tenant_schema`" in document


def _assert_lineage_rehearsal_contract(source: str) -> None:
    assert 'EXPECTED_FIRST_FAILURE = "0001_initial_tenant_schema"' in source
    assert 'EXPECTED_FAILED_OBJECT = "tenants"' in source
    assert 'KERNEL_VERSION_TABLE = "dotmac_kernel_alembic_version"' in source
    assert 'ERP_PREDECESSOR = "20260812_merge_expand_withdrawal"' in source
    assert "Path(dotmac_kernel.__file__)" in source
    assert "version_table=KERNEL_VERSION_TABLE" in source
    assert "command.upgrade(config, ERP_PREDECESSOR)" in source
    assert "_assert_no_kernel_stamp(database_url)" in source
    assert "_database_roles(database_url) == roles_before" in source
    assert "command.stamp" not in source


def test_lineage_gate_executes_the_installed_kernel_on_fresh_and_upgrade_paths() -> (
    None
):
    _assert_lineage_rehearsal_contract(LINEAGE_REHEARSAL.read_text(encoding="utf-8"))


def test_lineage_gate_guard_is_sensitive_to_a_weakened_failure_ratchet() -> None:
    source = LINEAGE_REHEARSAL.read_text(encoding="utf-8")
    weakened = source.replace(
        'EXPECTED_FIRST_FAILURE = "0001_initial_tenant_schema"',
        'EXPECTED_FIRST_FAILURE = "heads"',
    )
    with pytest.raises(AssertionError):
        _assert_lineage_rehearsal_contract(weakened)
