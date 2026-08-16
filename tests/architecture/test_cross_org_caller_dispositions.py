"""Every runtime cross-organization caller has one reviewed database contract.

``allow_cross_org`` and ``cross_org_session`` bypass only ERP's SQLAlchemy
listener.  They do not bypass PostgreSQL RLS.  A caller that works while the
runtime login is the ``postgres`` superuser can therefore fail closed when the
application moves to ``app_user``.

This inventory is generated from runtime syntax, then dispositioned by a
human.  It is deliberately separate from the 418-table catalog: the catalog
states what PostgreSQL protects; this file states how each application entry
point will reach (or avoid) those protected rows.
"""

from __future__ import annotations

import ast
import csv
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY = REPO_ROOT / "docs" / "inventories" / "rls-cross-org-callers.tsv"
RUNTIME_ROOTS = ("app", "scripts")
SKIPPED_PARTS = frozenset({"__pycache__", "archive"})

BOUNDARY_CALLS = frozenset({"allow_cross_org", "cross_org_session"})
DEPENDENCY_CALLS = frozenset({"get_db_admin_bypass", "get_db_auth_bypass"})
MECHANISMS = BOUNDARY_CALLS | DEPENDENCY_CALLS
CONTRACTS = frozenset(
    {
        "isolated_cross_tenant_service",
        "ordinary_app_user",
        "tenant_catalog_definer",
        "tenant_resolution_definer",
        "tenant_session",
    }
)
CUTOVER_STATES = frozenset({"blocked", "ready"})
INVENTORY_FIELDS = (
    "path",
    "symbol",
    "mechanism",
    "occurrences",
    "protected_access",
    "contract",
    "cutover_state",
    "owner",
    "evidence",
)

DIRECT_MARKER_WRITERS = frozenset(
    {
        ("app/api/deps.py", "_yield_bypass_session"),
        ("app/db/session_context.py", "allow_cross_org"),
        ("app/db/session_context.py", "cross_org_session"),
    }
)


@dataclass(frozen=True, order=True)
class Caller:
    path: str
    symbol: str
    mechanism: str
    occurrences: int


def _call_name(node: ast.Call, aliases: dict[str, str]) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return aliases.get(func.id, func.id)
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


class _CallerVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.scope: list[str] = []
        self.aliases: dict[str, str] = {}
        self.found: Counter[tuple[str, str, str]] = Counter()

    @property
    def symbol(self) -> str:
        return ".".join(self.scope) if self.scope else "<module>"

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        for alias in node.names:
            if alias.name in MECHANISMS:
                self.aliases[alias.asname or alias.name] = alias.name

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_AsyncFunctionDef(  # noqa: N802
        self, node: ast.AsyncFunctionDef
    ) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _call_name(node, self.aliases)
        mechanism: str | None = None
        if name in BOUNDARY_CALLS:
            mechanism = name
        elif name == "Depends" and node.args:
            dependency = node.args[0]
            if isinstance(dependency, ast.Name):
                dependency_name = self.aliases.get(dependency.id, dependency.id)
            elif isinstance(dependency, ast.Attribute):
                dependency_name = dependency.attr
            else:
                dependency_name = None
            if dependency_name in DEPENDENCY_CALLS:
                mechanism = dependency_name
        if mechanism is not None:
            self.found[(self.path, self.symbol, mechanism)] += 1
        self.generic_visit(node)


def _inventory_rows() -> list[dict[str, str]]:
    with INVENTORY.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source, delimiter="\t")
        assert tuple(reader.fieldnames or ()) == INVENTORY_FIELDS
        return list(reader)


def _recorded_callers() -> list[Caller]:
    return sorted(
        Caller(
            row["path"],
            row["symbol"],
            row["mechanism"],
            int(row["occurrences"]),
        )
        for row in _inventory_rows()
    )


def _direct_marker_writers(tree: ast.Module, path: str) -> set[tuple[str, str]]:
    writers: set[tuple[str, str]] = set()
    scope: list[str] = []

    class Visitor(ast.NodeVisitor):
        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            scope.append(node.name)
            self.generic_visit(node)
            scope.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            self._visit_function(node)

        def visit_AsyncFunctionDef(  # noqa: N802
            self, node: ast.AsyncFunctionDef
        ) -> None:
            self._visit_function(node)

        def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and target.value.attr == "info"
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "allow_cross_org"
                ):
                    writers.add((path, ".".join(scope) or "<module>"))
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            func = node.func
            item_write = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "info"
                and func.attr in {"__setitem__", "setdefault"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "allow_cross_org"
            )
            update_write = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "info"
                and func.attr == "update"
                and node.args
                and isinstance(node.args[0], ast.Dict)
                and any(
                    isinstance(key, ast.Constant) and key.value == "allow_cross_org"
                    for key in node.args[0].keys
                )
            )
            if item_write or update_write:
                writers.add((path, ".".join(scope) or "<module>"))
            self.generic_visit(node)

    Visitor().visit(tree)
    return writers


@cache
def _scan_runtime(
    root: Path,
) -> tuple[tuple[Caller, ...], frozenset[tuple[str, str]]]:
    found: Counter[tuple[str, str, str]] = Counter()
    writers: set[tuple[str, str]] = set()
    for relative_root in RUNTIME_ROOTS:
        source_root = root / relative_root
        if not source_root.exists():
            continue
        for path in sorted(source_root.rglob("*.py")):
            relative = path.relative_to(root)
            if any(part in SKIPPED_PARTS for part in relative.parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            visitor = _CallerVisitor(relative.as_posix())
            visitor.visit(tree)
            found.update(visitor.found)
            writers.update(_direct_marker_writers(tree, relative.as_posix()))
    callers = tuple(sorted(Caller(*key, count) for key, count in found.items()))
    return callers, frozenset(writers)


def find_runtime_callers(root: Path = REPO_ROOT) -> list[Caller]:
    return list(_scan_runtime(root)[0])


def find_direct_marker_writers(root: Path = REPO_ROOT) -> set[tuple[str, str]]:
    return set(_scan_runtime(root)[1])


def test_every_runtime_caller_has_one_reviewed_disposition() -> None:
    assert _recorded_callers() == find_runtime_callers()


def test_inventory_order_is_canonical() -> None:
    rows = _inventory_rows()
    keys = [(row["path"], row["symbol"], row["mechanism"]) for row in rows]
    assert keys == sorted(keys)


def test_every_disposition_uses_the_closed_contract_vocabulary() -> None:
    rows = _inventory_rows()
    assert rows
    assert all(row["mechanism"] in MECHANISMS for row in rows)
    assert all(row["contract"] in CONTRACTS for row in rows)
    assert all(row["cutover_state"] in CUTOVER_STATES for row in rows)
    assert all(row["protected_access"] in {"no", "yes"} for row in rows)
    assert all(row["owner"].strip() for row in rows)
    assert all(row["evidence"].strip() for row in rows)


def test_ready_callers_do_not_claim_access_to_an_rls_protected_table() -> None:
    offenders = [
        f"{row['path']}::{row['symbol']}"
        for row in _inventory_rows()
        if row["cutover_state"] == "ready" and row["protected_access"] != "no"
    ]
    assert offenders == []


def test_app_user_cutover_blocker_count_is_a_two_directional_ratchet() -> None:
    # 108 at disposition (#305) -> 75 (#306, 33 catalog enumerations) -> 72
    # (#307, three discipline domain scans) -> 69 (#302, three obsolete OIDC
    # callers). This change converts eight workforce fan-outs.
    #
    # It ALSO reclassifies five rows from tenant_catalog_definer to
    # tenant_resolution_definer. That moves nothing here on purpose:
    # reclassification corrects what a caller needs, not whether it blocks.
    # The count falls only when a caller is actually converted.
    #
    # The webhook SSRF hardening (webhook_policy becomes platform-owned) moves
    # no caller in this half: 61, unchanged. It declares `SettingSpec.scope`,
    # builds the write refusal plus the read-side scope override, closes the
    # tenant writers and clamps both outbound timeout channels. It touches
    # app/ in eight files and adds no `allow_cross_org`, `cross_org_session`
    # or bypass-session dependency of its own -- deliberately so.
    # app/services/secrets.py::_openbao_allow_insecure needed a
    # platform-scoped read, and the obvious way to get one was a local
    # `allow_cross_org` around its hand-written query; it was instead routed
    # through DomainSettingService.get_by_key, which already owns that bypass
    # and already discards a caller's organization for a platform-owned key.
    # A hardening step that grew the bypass surface to hold a scope override in
    # a second place would have been the wrong trade twice over.
    #
    # Reclassification never moves this number. It corrects what a caller
    # needs, not whether it blocks; the count falls only when a caller is
    # actually converted or deleted.
    baseline = 61
    blocked = [
        f"{row['path']}::{row['symbol']}"
        for row in _inventory_rows()
        if row["cutover_state"] == "blocked"
    ]
    assert len(blocked) == baseline, (
        f"{len(blocked)} cross-org callers block app_user cutover; baseline is "
        f"{baseline}. A rise is new debt. A fall is progress that must lower the "
        "baseline in the same reviewed change."
    )


def test_only_the_three_infrastructure_owners_write_the_orm_marker() -> None:
    assert find_direct_marker_writers() == DIRECT_MARKER_WRITERS


def test_detector_is_sensitive_to_aliases_dependencies_and_direct_writes(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    scripts = tmp_path / "scripts"
    app.mkdir()
    scripts.mkdir()
    (app / "routes.py").write_text(
        "from app.api.deps import get_db_admin_bypass as admin_db\n"
        "from app.db.session_context import allow_cross_org as global_rows\n"
        "def route(db=Depends(admin_db)):\n"
        "    with global_rows(db):\n"
        "        return db\n",
        encoding="utf-8",
    )
    (scripts / "job.py").write_text(
        "def run(db):\n    db.info.update({'allow_cross_org': True})\n",
        encoding="utf-8",
    )

    assert find_runtime_callers(tmp_path) == [
        Caller("app/routes.py", "route", "allow_cross_org", 1),
        Caller("app/routes.py", "route", "get_db_admin_bypass", 1),
    ]
    assert find_direct_marker_writers(tmp_path) == {("scripts/job.py", "run")}
