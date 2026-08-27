"""`dotmac-tax` a3 is composed for storage and contracts, but not authoritative.

C2 is one atomic fact: the exact released wheel is pinned and locked, its
independent ``tx`` lineage is reachable at the reviewed head, ERP consumes only
the public contract, and the runtime authority flag remains off. C3 shadow and
C4 writer cutover are deliberately outside this slice.
"""

from __future__ import annotations

import ast
import configparser
import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest

from app.migration_bindings import (
    ASSEMBLY_PREREQUISITE_BINDINGS,
    COMPOSED_MODULE_LINEAGES,
)
from app.services.finance.tax.adoption import composition

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCK = REPO_ROOT / "poetry.lock"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
ADOPTION_ROOT = REPO_ROOT / "app" / "services" / "finance" / "tax" / "adoption"
RUNTIME_ENTRY_POINT_ROOTS = (
    REPO_ROOT / "app",
    REPO_ROOT / "scripts",
    REPO_ROOT / "tools",
)
RELEASE_FILES = {
    (
        "dotmac_tax-0.1.0a3-py3-none-any.whl",
        "sha256:a058df0e57c808e0014da0a8e1a98a887f25d7ff2695e884e48bbf714eacb2c4",
    ),
    (
        "dotmac_tax-0.1.0a3.tar.gz",
        "sha256:63842b89962331e1cdb3351679616f0d93e057186744f63bd04c01a4371340ca",
    ),
}


def _version_locations() -> list[str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ALEMBIC_INI)
    return parser["alembic"]["version_locations"].split()


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _qualified_name(expression: ast.expr) -> str | None:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        owner = _qualified_name(expression.value)
        return f"{owner}.{expression.attr}" if owner else None
    return None


def _tax_determination_calls(source: str, *, filename: str) -> list[int]:
    tree = ast.parse(source, filename=filename)
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", maxsplit=1)[0]
                bindings[local] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                bindings[local] = f"{node.module}.{alias.name}"

    calls: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        qualified = _qualified_name(node.func)
        if qualified is None:
            continue
        first, *rest = qualified.split(".")
        imported = bindings.get(first)
        if imported is None:
            continue
        canonical = ".".join((imported, *rest))
        if canonical.startswith("dotmac_tax.") and canonical.endswith(
            ".determine_tax_set"
        ):
            calls.append(node.lineno)
    return calls


def test_the_released_distribution_is_pinned_and_locked_exactly() -> None:
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["poetry"][
        "dependencies"
    ][composition.DISTRIBUTION]
    assert declared == {"version": composition.CONTRACT_VERSION, "source": "forgejo"}

    locked = [
        package
        for package in tomllib.loads(LOCK.read_text(encoding="utf-8"))["package"]
        if package["name"] == composition.DISTRIBUTION
    ]
    assert len(locked) == 1
    assert locked[0]["version"] == composition.CONTRACT_VERSION
    assert locked[0]["source"]["type"] == "legacy"
    assert {(item["file"], item["hash"]) for item in locked[0]["files"]} == (
        RELEASE_FILES
    )
    assert version(composition.DISTRIBUTION) == composition.CONTRACT_VERSION


def test_alembic_resolves_the_reviewed_tax_lineage() -> None:
    from alembic.util.pyfiles import coerce_resource_to_filename

    assert composition.MIGRATION_VERSION_LOCATION in _version_locations()
    resolved = Path(coerce_resource_to_filename(composition.MIGRATION_VERSION_LOCATION))
    assert resolved.is_dir()
    assert (resolved / f"{composition.LINEAGE_HEAD}.py").is_file()
    assert COMPOSED_MODULE_LINEAGES[composition.MODULE_CODE] == (
        composition.LINEAGE_HEAD
    )


def test_the_installed_manifest_is_the_reviewed_tenant_module() -> None:
    from dotmac_tax import module

    assert module.code == composition.MODULE_CODE
    assert module.version == composition.CONTRACT_VERSION
    assert module.short_code == "tax"
    assert module.migration_prefix == "tx"
    assert module.migration_branch == "tax"
    assert module.tables
    assert module.platform_tables == ()
    assert set(module.requires) == {
        "tenant_scope_catalog.v1",
        "module_database_roles.v1",
    }


def test_tax_prerequisites_resolve_only_to_erp_provider_revisions() -> None:
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        resolve_depends_on,
    )
    from dotmac_tax import module

    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    assert set(resolve_depends_on(module.requires)) == {
        "20260813_tenant_projection",
        "20260814_database_roles",
    }


def test_composition_is_installed_but_authority_stays_disabled() -> None:
    state = composition.composition_state()
    assert state == {
        "distribution": composition.DISTRIBUTION,
        "contract_version": composition.CONTRACT_VERSION,
        "enabled": False,
        "installed_version": composition.CONTRACT_VERSION,
        "ready": False,
    }
    with pytest.raises(composition.TaxCompositionNotReady, match="is false"):
        composition.require_composition_ready()


def test_erp_imports_only_the_module_public_surface() -> None:
    forbidden: list[str] = []
    for path in ADOPTION_ROOT.rglob("*.py"):
        for imported in _imports(path):
            if imported.startswith("dotmac_tax."):
                forbidden.append(f"{path.relative_to(REPO_ROOT)}: {imported}")
    assert not forbidden, f"ERP imports dotmac-tax internals: {forbidden}"


def test_no_runtime_caller_has_cut_over_to_the_module_calculator() -> None:
    offenders: list[str] = []
    for root in RUNTIME_ENTRY_POINT_ROOTS:
        for path in root.rglob("*.py"):
            calls = _tax_determination_calls(
                path.read_text(encoding="utf-8"), filename=str(path)
            )
            offenders.extend(
                f"{path.relative_to(REPO_ROOT).as_posix()}:{line}" for line in calls
            )
    assert not offenders, f"C2 must not repoint a tax writer: {offenders}"


def test_no_cutover_detector_catches_aliases_and_multiline_calls() -> None:
    examples = (
        "from dotmac_tax import determine_tax_set as decide\ndecide(None,\n  None)\n",
        "import dotmac_tax as tax\ntax.determine_tax_set(None, None)\n",
        "import dotmac_tax.service as service\nservice.determine_tax_set(\n  None, None\n)\n",
    )
    assert all(
        _tax_determination_calls(source, filename="sensitivity.py")
        for source in examples
    )
