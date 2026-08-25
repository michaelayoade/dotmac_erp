"""The first ``dotmac-imports`` adopter stays composed and product-owned.

This is an assembly contract, not a module test.  Starter proves the reusable
run ledger; ERP proves that it pins that ledger exactly, supplies its migration
prerequisites, and keeps customer meaning in product code.
"""

from __future__ import annotations

import ast
import configparser
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER = REPO_ROOT / "app/services/finance/import_export/durable_customers.py"
TASK = REPO_ROOT / "app/tasks/imports.py"
ROUTE = REPO_ROOT / "app/api/finance/import_export.py"

_LOW_LEVEL_IMPORT_WORKFLOW_CALLS = {
    "prepare_tenant_import_csv",
    "prepare_customer_import",
    "record_customer_dry_run",
    "get_customer_import",
    "customer_import_outcomes",
    "promote_customer_import",
}


def _storage_reads_inside_sessions(tree: ast.AST) -> list[int]:
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        if not any(
            isinstance(item.context_expr, ast.Call)
            and getattr(item.context_expr.func, "id", None) == "session_for_org"
            for item in node.items
        ):
            continue
        for nested in ast.walk(node):
            if isinstance(nested, ast.Call) and (
                getattr(nested.func, "id", None)
                in {"read_claimed_partition", "read_customer_partition"}
            ):
                offenders.append(nested.lineno)
    return offenders


def _module_level_service_imports(tree: ast.Module) -> list[int]:
    return [
        node.lineno
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and any(
            name.startswith("app.services")
            for name in (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
            )
        )
    ]


def _low_level_route_calls(tree: ast.Module) -> list[tuple[str, int]]:
    return [
        (node.func.id, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _LOW_LEVEL_IMPORT_WORKFLOW_CALLS
    ]


def _version_locations() -> list[str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(REPO_ROOT / "alembic.ini")
    return parser["alembic"]["version_locations"].split()


def test_imports_is_an_exact_forgejo_pin() -> None:
    dependency = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["poetry"]["dependencies"]["dotmac-imports"]
    assert dependency == {"version": "0.1.0a2", "source": "forgejo"}


def test_imports_lineage_is_composed_at_its_pinned_head() -> None:
    from app.migration_bindings import COMPOSED_MODULE_LINEAGES

    assert "dotmac_imports.migrations:versions" in _version_locations()
    assert COMPOSED_MODULE_LINEAGES["imports"] == "im_0001_import_runs"


def test_imports_prerequisites_resolve_to_erp_owned_revisions() -> None:
    pytest.importorskip("dotmac_imports")
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        resolve_depends_on,
    )

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    edges = set(
        resolve_depends_on(("tenant_scope_catalog.v1", "module_database_roles.v1"))
    )
    assert edges == {"20260813_tenant_projection", "20260814_database_roles"}
    assert "0001_initial_tenant_schema" not in edges


def test_customer_adapter_owns_no_transaction_or_session_factory() -> None:
    tree = ast.parse(ADAPTER.read_text(encoding="utf-8"))
    calls = {
        getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert not calls & {"commit", "rollback", "SessionLocal", "sessionmaker"}


def test_storage_read_is_outside_every_worker_session() -> None:
    """The storage phase must remain syntactically outside session_for_org.

    This is a sensitivity-oriented shape check: a call nested beneath either
    worker's session context would put provider latency inside a DB transaction.
    """
    tree = ast.parse(TASK.read_text(encoding="utf-8"))
    offenders = _storage_reads_inside_sessions(tree)
    assert not offenders, f"storage reads occur inside DB sessions: {offenders}"


def test_storage_phase_detector_is_sensitive() -> None:
    planted = ast.parse(
        "with session_for_org(org_id) as db:\n"
        "    prepared = read_claimed_partition(claim, open_partition=open_file)\n"
    )
    assert _storage_reads_inside_sessions(planted) == [2]


def test_import_task_loads_application_services_inside_the_task() -> None:
    tree = ast.parse(TASK.read_text(encoding="utf-8"))
    assert not _module_level_service_imports(tree)


def test_task_service_import_detector_is_sensitive() -> None:
    planted = ast.parse("from app.services.storage import get_storage\n")
    assert _module_level_service_imports(planted) == [1]


def test_http_adapter_delegates_the_durable_workflow_as_one_service() -> None:
    tree = ast.parse(ROUTE.read_text(encoding="utf-8"))
    assert not _low_level_route_calls(tree)


def test_http_workflow_detector_is_sensitive() -> None:
    planted = ast.parse(
        "def route(db, prepared):\n"
        "    return record_customer_dry_run(db, prepared=prepared)\n"
    )
    assert _low_level_route_calls(planted) == [("record_customer_dry_run", 2)]
