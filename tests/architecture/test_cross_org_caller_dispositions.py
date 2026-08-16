"""Every runtime cross-organization caller has one reviewed database contract.

``allow_cross_org`` and ``cross_org_session`` bypass only ERP's SQLAlchemy
listener.  They do not bypass PostgreSQL RLS.  A caller that works while the
runtime login is the ``postgres`` superuser can therefore fail closed when the
application moves to ``app_user``.

This inventory is generated from runtime syntax, then dispositioned by a
human.  It is deliberately separate from the 418-table catalog: the catalog
states what PostgreSQL protects; this file states how each application entry
point will reach (or avoid) those protected rows.

``target_relations`` is the column that makes a row checkable.  Before it
existed, a row could describe its own reach in prose and name a relation the
caller never queries; nothing compared the claim to anything.  The column
carries the schema-qualified relations the bypass region actually reaches,
traced from source one region at a time.  A caller whose reachable set is not
fixed by its own body is not given an invented one: it goes in
:data:`UNBOUNDED_REACH`, a named two-directional ratchet, and its recorded
relations are read as a LOWER bound.
"""

from __future__ import annotations

import ast
import csv
import re
from collections import Counter
from collections.abc import Mapping
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
    "target_relations",
    "protected_access",
    "contract",
    "cutover_state",
    "owner",
    "evidence",
)

# `|` cannot appear in the TSV's field separator and cannot appear inside an
# unquoted PostgreSQL identifier. If ERP ever grows a quoted identifier that
# contains one, the format check fails the build rather than mis-splitting it.
RELATION = re.compile(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*")
TARGET_RELATIONS = re.compile(rf"^{RELATION.pattern}(\|{RELATION.pattern})*$")
NO_TARGETS = "-"
"""The bypass region issues no statement at all. A legal value, never a legal
disposition: the last such caller (``crm_inventory_health_check``) was deleted
when its bypass turned out to guard nothing, and the sentinel stays so the
guard bites on the next one rather than on the last one."""


def row_relations(row: Mapping[str, str]) -> tuple[str, ...]:
    value = row["target_relations"]
    if value == NO_TARGETS:
        return ()
    return tuple(value.split("|"))


# A caller whose reachable relation set is not fixed by its own body. No claim
# of completeness is made for these rows; what they record is a LOWER bound.
# Enumerated with the evidence that makes each one unbounded; two-directional
# ratchet that shrinks by narrowing a caller, never by moving a row into it.
UNBOUNDED_REACH: Mapping[tuple[str, str, str], str] = {
    (
        "app/services/settings_seed.py",
        "_global_settings_seed.scoped_seed",
        "allow_cross_org",
    ): (
        "the bypass region is a decorator body wrapping an arbitrary callable, "
        "so its reach is fixed by the seven @_global_settings_seed call sites "
        "and not by this symbol. target_relations records what those call sites "
        "reach today; a new decorated seed widens it without touching this row."
    ),
    (
        "app/tasks/data_health.py",
        "_task_session",
        "cross_org_session",
    ): (
        "the bypass region returns a session context to its caller instead of "
        "issuing a statement, so its reach is the union of the ten data-health "
        "tasks that pass organization_id=None. target_relations records that "
        "union as it stands; a new task widens it without touching this row."
    ),
    (
        "app/tasks/finance.py",
        "refresh_analysis_cubes",
        "cross_org_session",
    ): (
        "the None-organization branch issues REFRESH MATERIALIZED VIEW on a "
        "name read out of rpt.analysis_cube.source_view and validated only by a "
        "regex, so the refreshed relation is a row VALUE that cannot be "
        "enumerated statically. It also reads pg_catalog.pg_matviews, which no "
        "tenant catalog can ever contain: the extraction query filters "
        "relkind='r' and excludes the system schemas."
    ),
}

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


def test_target_relations_is_a_sorted_deduplicated_relation_list() -> None:
    """The column is a machine-readable claim, so it is parsed, not read.

    Sorting removes the reorder-to-dodge-a-diff move and makes two rows that
    reach the same relations compare equal on sight.
    """
    malformed: list[str] = []
    for row in _inventory_rows():
        value = row["target_relations"]
        where = f"{row['path']}::{row['symbol']} ({row['mechanism']})"
        if value == NO_TARGETS:
            continue
        if not TARGET_RELATIONS.match(value):
            malformed.append(f"{where}: {value!r} is not a `|`-joined relation list")
            continue
        parts = value.split("|")
        if list(parts) != sorted(set(parts)):
            malformed.append(f"{where}: {value!r} is not sorted and deduplicated")
    assert malformed == []


def test_the_unbounded_reach_backlog_is_a_two_directional_ratchet() -> None:
    """Three callers whose relation set is not fixed by their own body.

    A row lands here instead of receiving an invented reach.  The count is a
    ratchet in both directions: a fourth unbounded caller fails until it is
    enumerated and reviewed, and narrowing one of these three fails until the
    count is lowered in the same change.
    """
    keys = {(row["path"], row["symbol"], row["mechanism"]) for row in _inventory_rows()}
    assert set(UNBOUNDED_REACH) <= keys
    assert all(evidence.strip() for evidence in UNBOUNDED_REACH.values())
    # Each still records the relations it is known to reach: unbounded means
    # the set is a LOWER bound, not that it is unknown.
    recorded = {
        (row["path"], row["symbol"], row["mechanism"]): row_relations(row)
        for row in _inventory_rows()
    }
    assert all(recorded[key] for key in UNBOUNDED_REACH)
    assert len(UNBOUNDED_REACH) == 3, (
        "the unbounded-reach backlog is enumerated and shrink-only; a new entry "
        "is new debt and a removed one must lower this count in the same change"
    )


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
    # This change deletes four rows outright: 152 rows -> 148, 61 -> 57. None
    # of the four is a fan-out; each bypass turned out to be protecting
    # nothing. crm_inventory_health_check opened a cross-org session only to
    # satisfy InventoryPushService.__init__ — the probe it runs is an HTTP POST
    # to the configured webhook and queries no table, so `db` became optional
    # behind a property that still raises on any inventory read attempted
    # without a session. DisciplineWebService._can_view_department_case
    # suppressed the ORM listener around two statements that already spell out
    # Employee.organization_id == org_id. execute_async_hook opened one purely
    # to read back an organization both enqueue sites already hold, so the org
    # now travels on the message. verify_audit_hash_chain discovered its
    # tenants with SELECT DISTINCT over audit.audit_log, which is genuinely
    # RLS-protected: under app_user that returned zero rows — a tamper check
    # that verified nothing and exited 0.
    #
    # Three candidates examined in the same pass were REFUTED and are recorded
    # here because a refutation is a finding, not an omission.
    # cleanup_old_hook_executions keeps its row: platform.service_hook_execution
    # is a known RLS gap with a nullable organization_id, so a fan-out would
    # permanently orphan every NULL-org row. app/main.py::lifespan keeps its
    # row: it is process startup with no request and no authenticated actor.
    # The three app/api/audit.py rows keep theirs: a cross-tenant operator API
    # has no tenant to iterate, because the cross-tenant query IS the question.
    baseline = 57
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
