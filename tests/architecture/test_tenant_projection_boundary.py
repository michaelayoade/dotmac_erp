"""The E8 tenant projection has one writer and one exact kernel-model import."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture.test_kernel_import_boundary import (
    ADOPTED_KERNEL_IMPORTS,
    kernel_import_violations,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECTION = PROJECT_ROOT / "app" / "services" / "tenant_projection.py"
ADMIN_ORGANIZATIONS = (
    PROJECT_ROOT / "app" / "services" / "admin" / "web" / "organization_settings.py"
)
ADMIN_SETTINGS = PROJECT_ROOT / "app" / "services" / "admin" / "settings_web.py"
ADMIN_FACADE = PROJECT_ROOT / "app" / "services" / "admin" / "web" / "__init__.py"
ACTIVE_ADMIN_MIXINS = (
    PROJECT_ROOT / "app" / "services" / "admin" / "web" / "common.py",
    PROJECT_ROOT / "app" / "services" / "admin" / "web" / "identity.py",
    PROJECT_ROOT / "app" / "services" / "admin" / "web" / "operations.py",
)
ORGANIZATION_METHODS = {
    "create_organization",
    "update_organization",
    "delete_organization",
}
DIRECT_ORGANIZATION_WRITERS = {
    Path("app/services/admin/web.py"),
    Path("app/services/admin/web/organization_settings.py"),
    Path("scripts/archive/update_org_name.py"),
    Path("scripts/create_org.py"),
    Path("scripts/seed_e2e_data.py"),
}
LEGACY_ADMIN = Path("app/services/admin/web.py")
ARCHIVED_RENAME = Path("scripts/archive/update_org_name.py")


def _methods(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _direct_organization_writer_paths() -> set[Path]:
    writers: set[Path] = set()
    for root in (PROJECT_ROOT / "app", PROJECT_ROOT / "scripts"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                constructs_organization = (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "Organization"
                )
                deletes_organization = (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "delete"
                    and any(
                        isinstance(argument, ast.Name)
                        and argument.id in {"org", "organization"}
                        for argument in node.args
                    )
                )
                writes_projection_source = isinstance(
                    node, (ast.Assign, ast.AnnAssign, ast.AugAssign)
                ) and any(
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in {"org", "organization"}
                    and target.attr in {"legal_name", "is_active"}
                    for target in (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                )
                if (
                    constructs_organization
                    or deletes_organization
                    or writes_projection_source
                ):
                    writers.add(path.relative_to(PROJECT_ROOT))
                    break
    return writers


def test_only_the_projection_service_may_import_kernel_tenant() -> None:
    assert {
        Path("tenancy.py"): {
            ("dotmac_kernel.cache", "TenantScope"),
        },
        Path("services/tenant_projection.py"): {
            ("dotmac_kernel.models", "Tenant"),
        },
    } == ADOPTED_KERNEL_IMPORTS
    assert kernel_import_violations(PROJECT_ROOT / "app") == []


def test_symbol_level_guard_rejects_another_kernel_model(tmp_path: Path) -> None:
    offender = tmp_path / "services" / "tenant_projection.py"
    offender.parent.mkdir(parents=True)
    offender.write_text(
        "from dotmac_kernel.models import Party, Tenant\n",
        encoding="utf-8",
    )

    violations = kernel_import_violations(tmp_path)

    assert len(violations) == 1
    assert "Party" in violations[0]


def test_every_live_organization_writer_requests_projection_reconciliation() -> None:
    for path in (ADMIN_ORGANIZATIONS, ADMIN_SETTINGS):
        source = path.read_text(encoding="utf-8")
        assert "reconcile_organization_tenant" in source, path
    source = ADMIN_ORGANIZATIONS.read_text(encoding="utf-8")
    assert "retire_organization_tenant" in source

    for relative_path in DIRECT_ORGANIZATION_WRITERS - {
        LEGACY_ADMIN,
        ARCHIVED_RENAME,
    }:
        source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert "reconcile_organization_tenant" in source, relative_path


def test_direct_organization_writer_inventory_is_closed() -> None:
    assert _direct_organization_writer_paths() == DIRECT_ORGANIZATION_WRITERS


def test_generated_instance_bootstrap_reconciles_before_its_commit() -> None:
    from scripts.bootstrap_instance import generate_bootstrap_db_script

    generated = generate_bootstrap_db_script()
    assert "reconcile_organization_tenant(db, org)" in generated
    assert generated.index("reconcile_organization_tenant(db, org)") < generated.index(
        "db.commit()"
    )


def test_non_projecting_writer_exemptions_have_machine_checked_premises() -> None:
    archived_source = (PROJECT_ROOT / ARCHIVED_RENAME).read_text(encoding="utf-8")
    assert ARCHIVED_RENAME.parts[:2] == ("scripts", "archive")
    assert any("ARCHIVED" in line for line in archived_source.splitlines()[1:8])


def test_modular_organization_writer_shadows_the_legacy_fallback() -> None:
    """The old monolith still exists only behind ``__getattr__`` fallback.

    These three names must remain real mixin methods, and no earlier active
    mixin may claim them. That makes the projection-aware implementation the
    facade's resolved path rather than a comment-level assertion.
    """

    facade = ADMIN_FACADE.read_text(encoding="utf-8")
    assert "AdminOrganizationSettingsMixin" in facade
    assert "def __getattr__" in facade
    assert _methods(ADMIN_ORGANIZATIONS) >= ORGANIZATION_METHODS
    for path in ACTIVE_ADMIN_MIXINS:
        assert ORGANIZATION_METHODS.isdisjoint(_methods(path)), path


def test_projection_service_never_owns_the_transaction() -> None:
    source = PROJECTION.read_text(encoding="utf-8")
    assert ".commit(" not in source
    assert ".rollback(" not in source
