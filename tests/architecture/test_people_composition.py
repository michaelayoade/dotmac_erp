"""`dotmac-people` storage is composed without moving People authority.

PostgreSQL rehearsal covers the live schema.  This static half pins the exact
artifact and lineage, proves that the module is an atomic tenant-only store,
and refuses any runtime import under ``app/`` in this composition slice. The
release assembly may import the package's declarative manifest and nothing
else from the package.
"""

from __future__ import annotations

import ast
import configparser
import tomllib
from pathlib import Path

from app.migration_bindings import COMPOSED_MODULE_LINEAGES
from app.migration_planes import ASSEMBLY_MODULE_PLANES

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"
PRODUCT_ASSEMBLY = APP_ROOT / "product_assembly.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCK = REPO_ROOT / "poetry.lock"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

DISTRIBUTION = "dotmac-people"
IMPORT_PACKAGE = "dotmac_people"
EXPECTED_VERSION = "0.1.0a1"
EXPECTED_LOCATION = "dotmac_people.migrations:versions"
EXPECTED_REVISION = "pe_0001_people_directory"
EXPECTED_TABLES = {
    "departments",
    "designations",
    "employees",
    "employment_types",
    "position_assignments",
    "positions",
}
EXPECTED_REQUIRES = {
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "party_person_catalog.v1",
}


def _version_locations() -> list[str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ALEMBIC_INI)
    return parser["alembic"]["version_locations"].split()


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _is_people_runtime_import(path: Path, module: str) -> bool:
    """Only the product assembly may bind the exact declarative manifest."""
    imports_package = module == IMPORT_PACKAGE or module.startswith(
        f"{IMPORT_PACKAGE}."
    )
    release_manifest_seam = (
        path == PRODUCT_ASSEMBLY and module == f"{IMPORT_PACKAGE}.manifest"
    )
    return imports_package and not release_manifest_seam


def test_the_distribution_is_pinned_exactly_from_the_private_source() -> None:
    dependencies = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"][
        "poetry"
    ]["dependencies"]
    assert dependencies[DISTRIBUTION] == {
        "version": EXPECTED_VERSION,
        "source": "forgejo",
    }


def test_the_lock_resolves_the_reviewed_artifact() -> None:
    locked = [
        package
        for package in tomllib.loads(LOCK.read_text(encoding="utf-8"))["package"]
        if package["name"] == DISTRIBUTION
    ]
    assert len(locked) == 1
    assert locked[0]["version"] == EXPECTED_VERSION
    assert locked[0]["source"]["type"] == "legacy"


def test_the_environment_resolves_the_reviewed_artifact() -> None:
    from importlib.metadata import version

    assert version(DISTRIBUTION) == EXPECTED_VERSION


def test_alembic_resolves_the_reviewed_people_revision() -> None:
    from alembic.util.pyfiles import coerce_resource_to_filename

    assert EXPECTED_LOCATION in _version_locations()
    resolved = Path(coerce_resource_to_filename(EXPECTED_LOCATION))
    assert resolved.is_dir()
    assert (resolved / f"{EXPECTED_REVISION}.py").is_file()
    assert COMPOSED_MODULE_LINEAGES["people"] == EXPECTED_REVISION


def test_people_is_an_atomic_tenant_only_store() -> None:
    from dotmac_people.manifest import module

    assert set(module.tables) == EXPECTED_TABLES
    assert not module.platform_tables
    assert set(module.requires) == EXPECTED_REQUIRES
    assert all(selection.module != "people" for selection in ASSEMBLY_MODULE_PLANES)


def test_nothing_under_app_imports_the_people_runtime() -> None:
    """Storage composition must not silently repoint a business caller.

    `app.product_assembly` may bind the declarative manifest to immutable
    release identity. Model, service and manifest-submodule imports remain
    runtime dependencies and are rejected by the same scan.
    """
    offenders = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in APP_ROOT.rglob("*.py")
        if any(
            _is_people_runtime_import(path, module)
            for module in _imported_modules(path)
        )
    )
    assert not offenders, f"app/ imports {IMPORT_PACKAGE}: {offenders}"


def test_people_manifest_seam_is_narrow_and_runtime_sensitive() -> None:
    other_app_file = APP_ROOT / "other.py"
    assert not _is_people_runtime_import(PRODUCT_ASSEMBLY, "dotmac_people.manifest")
    assert _is_people_runtime_import(other_app_file, "dotmac_people.manifest")
    assert _is_people_runtime_import(PRODUCT_ASSEMBLY, "dotmac_people")
    assert _is_people_runtime_import(PRODUCT_ASSEMBLY, "dotmac_people.service")
    assert _is_people_runtime_import(PRODUCT_ASSEMBLY, "dotmac_people.manifest.private")
