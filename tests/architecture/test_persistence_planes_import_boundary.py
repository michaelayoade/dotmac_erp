"""Who may import ``app.persistence_planes`` — a closed inventory.

The plane resolver decides which relations the tenant application role may be
granted. That is a GOVERNANCE decision, taken offline against a frozen census
and reviewed as a diff. It is not a runtime decision, and it must not become
one: a request path that can ask "is this relation control plane?" is one
refactor away from a request path that answers "…so grant it", which is the
shape ``CONTROL_PLANE_ACCESS_INVARIANT`` forbids by name.

Today the module is imported by three files — the manifest contract and two
architecture tests — and until now nothing said so. This is that statement,
and it is a **two-directional ratchet**: an importer appearing fails, and a
listed importer that stops importing fails too, because a stale inventory is
how a guard quietly stops covering anything.

**Scope, stated rather than implied** (AGENTS.md rule 25). The scan walks every
Python entry-point family in the repository — ``app/`` (API routers, web
handlers, middleware, startup), ``scripts/`` (CLI, jobs, backfills, cron),
``alembic/`` (migrations) and ``tests/`` — not one directory. It reads ``import``
and ``from … import`` statements, including relative ones resolved against the
importing file's package, and ``importlib.import_module`` with a literal name.
It does NOT see a dynamic import assembled at runtime from a computed string;
that is an unmonitored region, not an exempt one.

Both sensitivity directions are proven below, because a detector that fires on
nothing and one that fires on everything both "pass".
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: The module whose importers are governed.
PLANES_MODULE = "app.persistence_planes"

#: Every entry-point family in the repository, not one directory.
SCANNED_ROOTS: tuple[str, ...] = ("app", "scripts", "alembic", "tests")

#: The closed inventory. Each entry is a file that MAY import the resolver,
#: with the reason it may -- none of them is reachable from a request.
PERMITTED_IMPORTERS: dict[Path, str] = {
    Path("app/privilege_manifest.py"): (
        "the offline manifest contract: pure, census-driven, no session and "
        "no I/O; the only caller of the resolver in application code"
    ),
    Path("tests/architecture/test_persistence_planes.py"): ("the resolver's own guard"),
    Path("tests/architecture/test_privilege_manifest.py"): (
        "the manifest guard and the nine plane proofs"
    ),
}


def _resolved_targets(node: ast.AST, package: str) -> list[str]:
    """Every absolute module name one import node refers to."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if node.level:
            parts = package.split(".") if package else []
            base = parts[: len(parts) - node.level + 1]
            prefix = ".".join(base)
            module = f"{prefix}.{module}" if module else prefix
        if not module:
            return []
        return [module, *(f"{module}.{alias.name}" for alias in node.names)]
    if isinstance(node, ast.Call):
        called = node.func
        name = called.attr if isinstance(called, ast.Attribute) else None
        if name is None and isinstance(called, ast.Name):
            name = called.id
        if name != "import_module":
            return []
        return [
            argument.value
            for argument in node.args
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
        ]
    return []


def _package_of(relative_path: Path) -> str:
    return ".".join(relative_path.with_suffix("").parts[:-1])


def plane_importers(root: Path, *, roots: tuple[str, ...] = SCANNED_ROOTS) -> set[Path]:
    """Every file under ``roots`` that imports the plane resolver."""
    importers: set[Path] = set()
    for name in roots:
        directory = root / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            relative = path.relative_to(root)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            package = _package_of(relative)
            for node in ast.walk(tree):
                targets = _resolved_targets(node, package)
                if any(
                    target == PLANES_MODULE or target.startswith(f"{PLANES_MODULE}.")
                    for target in targets
                ):
                    importers.add(relative)
                    break
    return importers


def plane_import_violations(
    root: Path, *, roots: tuple[str, ...] = SCANNED_ROOTS
) -> list[str]:
    """Two-directional: an unlisted importer, and a listed non-importer."""
    observed = plane_importers(root, roots=roots)
    permitted = set(PERMITTED_IMPORTERS)
    violations = [
        f"UNPERMITTED PLANE IMPORT: {path} imports {PLANES_MODULE}. The plane "
        "resolver decides which relations the tenant application role may be "
        "granted -- an offline governance decision reviewed as a diff, never "
        "a runtime one. Add the file to PERMITTED_IMPORTERS with the reason "
        "it is not reachable from a request, or do not import it."
        for path in sorted(observed - permitted)
    ]
    violations.extend(
        f"STALE PLANE IMPORT ENTRY: {path} is listed in PERMITTED_IMPORTERS "
        f"but no longer imports {PLANES_MODULE}. An inventory that outlives "
        "its contents stops covering anything; remove the entry."
        for path in sorted(permitted - observed)
    )
    return violations


def test_only_the_manifest_contract_and_its_guards_import_the_resolver() -> None:
    assert plane_import_violations(PROJECT_ROOT) == []


def test_the_permitted_importers_each_state_why_they_are_not_a_request_path() -> None:
    for path, reason in PERMITTED_IMPORTERS.items():
        assert (PROJECT_ROOT / path).is_file(), path
        assert reason.strip(), path


def _plant(tree: Path, relative: str, statement: str) -> None:
    path = tree / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{statement}\n", encoding="utf-8")


def test_sensitivity_a_planted_import_from_a_runtime_entry_point_is_named(
    tmp_path: Path,
) -> None:
    """Direction one: every entry-point family, and every import spelling."""
    planted = {
        "app/api/invoices.py": f"from {PLANES_MODULE} import PLANE_CONTROL",
        "app/web/dashboard.py": f"import {PLANES_MODULE}",
        "app/tasks/nightly.py": f"from {PLANES_MODULE} import default_resolver",
        "scripts/backfill_planes.py": (
            f"import importlib\nimportlib.import_module({PLANES_MODULE!r})"
        ),
        "alembic/versions/20260904_widen.py": (
            "from app import persistence_planes as planes"
        ),
    }
    for relative, statement in planted.items():
        _plant(tmp_path, relative, statement)
    # A relative import from inside the package must not evade the scan.
    _plant(
        tmp_path, "app/services/billing.py", "from ..persistence_planes import PLANES"
    )

    violations = plane_import_violations(tmp_path)

    named = {
        violation.split(" ")[3]
        for violation in violations
        if "UNPERMITTED" in violation
    }
    assert named == {*planted, "app/services/billing.py"}, violations
    for relative in planted:
        assert any(relative in violation for violation in violations), relative


def test_sensitivity_the_three_legitimate_importers_stay_silent(
    tmp_path: Path,
) -> None:
    """Direction two, and the half that fails in practice.

    A guard that names the permitted importers too is not a boundary, it is a
    blanket refusal wearing one -- and it would be removed within the week by
    whoever needed the manifest to keep working.
    """
    for relative in PERMITTED_IMPORTERS:
        _plant(tmp_path, str(relative), f"from {PLANES_MODULE} import default_resolver")

    assert plane_import_violations(tmp_path) == []

    # And a file that merely NAMES the module in prose is not an importer.
    _plant(
        tmp_path,
        "app/api/quotes.py",
        f'"""Plane comes from {PLANES_MODULE}, which this module does not import."""',
    )
    assert plane_import_violations(tmp_path) == []
