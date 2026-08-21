"""Exact ratchets for ERP's general-ledger authority ahead of `dotmac-accounting`.

`dotmac-accounting` is the accepted tenant owner for chart of accounts, fiscal
calendar, accounting dimensions, journal lifecycle, balanced posting, linked
reversals, period close/reopen/lock, and immutable posted-ledger evidence.  ERP
remains the live authority for every one of those decisions until its own sealed
cutover, so this module does not enforce a boundary that does not exist yet.  It
freezes the one thing a cutover needs and nobody has written down: exactly which
code writes GL storage, and exactly which code depends on a GL decision service.

Two ledgers, two directions each:

``docs/inventories/accounting-gl-writers.tsv``
    Every site that MUTATES a `gl.*` relation.  These are the writers a cutover
    must seal.  A new one is new authority ERP will have to retire later; a
    disappearing one is retired authority whose row must come out in the same
    reviewed change.

``docs/inventories/accounting-gl-callers.tsv``
    Every site that DEPENDS on a GL decision service (`gl.poster`,
    `gl.journal`, `gl.period_guard`, the posting adapters, …).  A writer is
    sealed by cutting it over; a caller is migrated by repointing it at the
    module contract.  They are different work, so they are different ledgers.

Equality in both directions is the whole mechanism.  A one-directional
"must not exceed" check silently accepts a writer being renamed into invisibility;
equality makes both halves of a move reviewable.

## Scope, stated as an enforceable premise (ADR-0018)

Scanned: `app`, `scripts`, `tools` — the online, task/worker and operator
entry-point families.  Not one directory; the families that can reach GL storage
outside a migration.

Excluded, each for a premise that can be checked rather than assumed:

- `alembic/` defines storage and repairs history.  A data migration that rewrote
  2025 journals is not an online or operator writer, and it cannot be "cut over"
  — it has already run.  Migration-time DML is inventoried in the adoption
  document instead, where its provenance belongs.
- `scripts/archive/` is out of scope by the same premise `check_session_context.py`
  states: an archived one-off has already run and is kept for provenance, never
  executed again.  Moving a retired script there is the intended way to drop its
  row.
- `tests/` exercises the writers; it is not one.

The premise is checked, not asserted: every scan root must still exist, and the
detector carries a sensitivity proof for every evidence shape it claims to find.
"""

from __future__ import annotations

import ast
import csv
import dataclasses
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WRITER_INVENTORY = REPO_ROOT / "docs" / "inventories" / "accounting-gl-writers.tsv"
CALLER_INVENTORY = REPO_ROOT / "docs" / "inventories" / "accounting-gl-callers.tsv"
RUNTIME_ROOTS = ("app", "scripts", "tools")
SKIPPED_DIR_NAMES = frozenset({"__pycache__", "archive"})

WRITER_FIELDS = (
    "path",
    "symbol",
    "relations",
    "evidence",
    "disposition",
    "final_state",
)
CALLER_FIELDS = ("path", "symbol", "services", "disposition", "final_state")

#: Every row carries BOTH a disposition (what the cutover does to it) and a
#: final state (what must be true of it when gate G is complete).  Two columns
#: rather than one, because "operator_tool" and "keep_local" describe what a row
#: IS, not where it ENDS — and a bucket with no stated end state is where work
#: goes to be forgotten.  The pairs below are the only legal combinations, and
#: each carries an invariant checked against the code rather than the label.
#:
#: Writers:
#:
#: ``retire_with_accounting_cutover`` -> ``writer_removed``
#:     The module becomes the writer and this path is deleted.  Invariant: the
#:     row must touch at least one MIGRATING relation, or it is a kept-relation
#:     writer mis-filed as retiring.
#: ``keep_local`` -> ``retained_erp_writer``
#:     ERP keeps writing it.  Invariant: EVERY relation the row touches must be
#:     in `RETAINED_ERP_RELATIONS` — the relations ERP still writes afterwards,
#:     which is a strictly smaller set than "has no module counterpart".  This
#:     is the invariant that stops a migrating relation being parked in
#:     `keep_local`, and it is what caught `gl.posting_batch`: no module table
#:     AND no surviving ERP writer, so it retires with the poster rather than
#:     being kept.
#: ``operator_tool`` -> ``tool_archived`` | ``tool_repointed``
#:     An operator entry point.  ``tool_archived`` retires it by moving the file
#:     to `scripts/archive/`, at which point the two-directional ratchet forces
#:     its row out of the ledger.  ``tool_repointed`` keeps the tool and gives it
#:     the module contract instead — so its invariant is that the file is Python:
#:     a raw `.sql` one-off cannot call a module and must be archived, not
#:     repointed.
WRITER_FINAL_STATES: dict[str, frozenset[str]] = {
    "retire_with_accounting_cutover": frozenset({"writer_removed"}),
    "keep_local": frozenset({"retained_erp_writer"}),
    "operator_tool": frozenset({"tool_archived", "tool_repointed"}),
}
#: Callers:
#:
#: ``repoint_to_module`` -> ``caller_repointed``
#:     Calls the module contract instead.  Invariant: it must depend on at least
#:     one decision Accounting takes over, or there is nothing to repoint.
#: ``gl_internal`` -> ``retired_with_gl_owner``
#:     Part of the GL owner itself; it leaves with the owner.  Invariant: the
#:     path must actually be inside the GL owner.
#: ``keep_local`` -> ``retained_erp_caller``
#:     Consumes a decision ERP keeps.  Invariant: EVERY service it depends on
#:     must be in `RETAINED_GL_DECISIONS`.
CALLER_FINAL_STATES: dict[str, frozenset[str]] = {
    "repoint_to_module": frozenset({"caller_repointed"}),
    "gl_internal": frozenset({"retired_with_gl_owner"}),
    "keep_local": frozenset({"retained_erp_caller"}),
}
WRITER_DISPOSITIONS = frozenset(WRITER_FINAL_STATES)
CALLER_DISPOSITIONS = frozenset(CALLER_FINAL_STATES)

#: The directories that ARE the GL decision owner.  A `gl_internal` caller must
#: live in one of them; anything else claiming that disposition is a migrating
#: caller hiding behind the owner's name.
GL_OWNER_PREFIXES = ("app/services/finance/gl/", "app/services/finance/posting/")

#: The twelve `gl.*` relations, keyed by the model class that maps them.  A name
#: only counts when it was imported from `app.models.finance.gl` — `Account` and
#: `Budget` are ordinary words elsewhere in a finance codebase.
GL_MODELS = {
    "Account": "gl.account",
    "AccountBalance": "gl.account_balance",
    "AccountCategory": "gl.account_category",
    "BalanceRefreshQueue": "gl.balance_refresh_queue",
    "Budget": "gl.budget",
    "BudgetLine": "gl.budget_line",
    "FiscalPeriod": "gl.fiscal_period",
    "FiscalYear": "gl.fiscal_year",
    "JournalEntry": "gl.journal_entry",
    "JournalEntryLine": "gl.journal_entry_line",
    "PostedLedgerLine": "gl.posted_ledger_line",
    "PostingBatch": "gl.posting_batch",
}
GL_MODEL_PACKAGE = "app.models.finance.gl"

#: The GL decision services, grouped by the decision each owns.  Grouping keeps
#: the caller ledger about DECISIONS rather than about module paths: splitting
#: `balance_refresh` from `account_balance` would double-count one dependency.
GL_SERVICES = {
    "app.services.finance.gl.ledger_posting": "gl.poster",
    "app.services.finance.gl.journal": "gl.journal",
    "app.services.finance.gl.reversal": "gl.reversal",
    "app.services.finance.gl.period_guard": "gl.period_guard",
    "app.services.finance.gl.period_close": "gl.period_close",
    "app.services.finance.gl.chart_of_accounts": "gl.coa",
    "app.services.finance.gl.category": "gl.coa",
    "app.services.finance.gl.account_query": "gl.coa",
    "app.services.finance.gl.fiscal_year": "gl.calendar",
    "app.services.finance.gl.fiscal_period": "gl.calendar",
    "app.services.finance.gl.account_balance": "gl.balances",
    "app.services.finance.gl.balance_refresh": "gl.balances",
    "app.services.finance.gl.balance_invalidation": "gl.balances",
    "app.services.finance.gl.journal_query": "gl.journal_query",
    "app.services.finance.gl.gl_posting_adapter": "gl.posting_adapter",
    "app.services.finance.gl.bulk": "gl.bulk_posting",
    "app.services.finance.gl.fx_revaluation": "gl.fx_revaluation",
    "app.services.finance.gl.stranded_fee_posting": "gl.stranded_fee_posting",
    "app.services.finance.gl.posting_backlog": "gl.posting_backlog",
    "app.services.finance.posting.base": "gl.posting_adapter",
    "app.services.finance.posting.accounts": "gl.posting_accounts",
    "app.services.finance.posting.idempotency": "gl.posting_idempotency",
}

_RELATION_ALTERNATION = "|".join(
    sorted(
        (relation.split(".", 1)[1] for relation in GL_MODELS.values()),
        key=len,
        reverse=True,
    )
)
_RAW_DML = re.compile(
    rf"\b(?:insert\s+into|delete\s+from|update|truncate(?:\s+table)?)\s+"
    rf"(?:\"?gl\"?\.)(?P<relation>\"?(?:{_RELATION_ALTERNATION})\"?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, order=True)
class Writer:
    path: str
    symbol: str
    relations: str
    evidence: str


@dataclass(frozen=True, order=True)
class Caller:
    path: str
    symbol: str
    services: str


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _base_name(node: ast.expr) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _receiver_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _sql_template(node: ast.expr) -> str | None:
    """Reconstruct a literal SQL string; `<dynamic>` where interpolation hides it.

    A dynamic fragment is deliberately NOT treated as unknown-and-therefore-clean:
    the literal parts around it are still matched, so
    ``f"UPDATE gl.journal_entry SET status = {value}"`` is caught.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
            else "<dynamic>"
            for part in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (_sql_template(node.left) or "<dynamic>") + (
            _sql_template(node.right) or "<dynamic>"
        )
    return None


_QUOTE = '"'


def _raw_dml_relations(source: str) -> set[str]:
    return {
        ("gl." + match.group("relation").strip(_QUOTE)).lower()
        for match in _RAW_DML.finditer(source)
    }


class _WriterVisitor(ast.NodeVisitor):
    """Find GL mutations, tracking which locals hold a GL row."""

    def __init__(self) -> None:
        self.scope: list[str] = []
        self.aliases: dict[str, str] = {}
        self.local_types: list[dict[str, str]] = []
        self.found: dict[tuple[str, str], set[str]] = defaultdict(set)

    @property
    def symbol(self) -> str:
        return ".".join(self.scope) if self.scope else "<module>"

    def _record(self, relation: str, evidence: str) -> None:
        self.found[(self.symbol, relation)].add(evidence)

    def _local_relation(self, node: ast.expr) -> str | None:
        name = _base_name(node)
        if name is None or not self.local_types:
            return None
        return self.local_types[-1].get(name)

    def _relation_of_value(self, node: ast.expr) -> str | None:
        """The GL relation a value expression yields, if it is knowably one."""
        if isinstance(node, ast.Name):
            return self._local_relation(node)
        if not isinstance(node, ast.Call):
            return None
        called = _call_name(node)
        if called in self.aliases:
            return self.aliases[called]
        # `db.get(JournalEntry, pk)` and `select(JournalEntry)` chains.
        if called in {"get", "scalar", "scalars", "one", "one_or_none", "first"}:
            for part in ast.walk(node):
                if isinstance(part, ast.Name) and part.id in self.aliases:
                    return self.aliases[part.id]
        return None

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        if module == GL_MODEL_PACKAGE or module.startswith(f"{GL_MODEL_PACKAGE}."):
            for imported in node.names:
                if imported.name in GL_MODELS:
                    self.aliases[imported.asname or imported.name] = GL_MODELS[
                        imported.name
                    ]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.local_types.append({})
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            if isinstance(argument.annotation, ast.Name):
                relation = self.aliases.get(argument.annotation.id)
                if relation is not None:
                    self.local_types[-1][argument.arg] = relation
        self.generic_visit(node)
        self.local_types.pop()
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        called = _call_name(node)

        if called in self.aliases:
            self._record(self.aliases[called], "constructor")

        # Core `insert(Model)` / `delete(Model)` / `update(Model)`.
        if called in {"insert", "delete", "update"} and node.args:
            target = node.args[0]
            if isinstance(target, ast.Name) and target.id in self.aliases:
                self._record(self.aliases[target.id], "sql_dml")

        # `session.add(...)` / `add_all` / `merge` / `delete` / bulk helpers.
        if (
            called in {"add", "add_all", "merge", "delete", "bulk_save_objects"}
            and isinstance(node.func, ast.Attribute)
            and _receiver_name(node.func.value) in {"db", "session", "self"}
        ):
            for argument in node.args:
                if isinstance(argument, (ast.List, ast.Tuple, ast.Set)):
                    items = list(argument.elts)
                else:
                    items = [argument]
                for item in items:
                    relation = self._relation_of_value(item)
                    if relation is not None:
                        self._record(relation, f"session_{called}")

        if called == "setattr" and node.args:
            relation = self._local_relation(node.args[0])
            if relation is not None:
                self._record(relation, "setattr")

        if called in {"text", "execute", "exec_driver_sql"} and node.args:
            sql = _sql_template(node.args[0])
            if sql is not None:
                for relation in _raw_dml_relations(sql):
                    self._record(relation, "raw_sql_dml")

        self.generic_visit(node)

    def _record_attribute_target(self, target: ast.expr) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._record_attribute_target(item)
            return
        if not isinstance(target, ast.Attribute) or target.attr.startswith("_"):
            return
        relation = self._local_relation(target)
        if relation is not None:
            self._record(relation, "attribute_assignment")

    def _bind(self, name: str, value: ast.expr | None) -> None:
        if not self.local_types:
            return
        relation = self._relation_of_value(value) if value is not None else None
        if relation is None:
            self.local_types[-1].pop(name, None)
        else:
            self.local_types[-1][name] = relation

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for target in node.targets:
            self._record_attribute_target(target)
            if isinstance(target, ast.Name):
                self._bind(target.id, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        self._record_attribute_target(node.target)
        if isinstance(node.target, ast.Name):
            annotated = (
                self.aliases.get(node.annotation.id)
                if isinstance(node.annotation, ast.Name)
                else None
            )
            if annotated is not None and self.local_types:
                self.local_types[-1][node.target.id] = annotated
            else:
                self._bind(node.target.id, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        self._record_attribute_target(node.target)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        if isinstance(node.target, ast.Name):
            self._bind(node.target.id, node.iter)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:  # noqa: N802
        self.generic_visit(node)


class _CallerVisitor(ast.NodeVisitor):
    """Find dependencies on a GL decision service, per enclosing symbol.

    A module-level import is attributed to the symbols that USE the imported
    name, not to the file — otherwise a 2,000-line service that touches the
    poster in one method reads as wholly GL-dependent, and the caller ledger
    stops being a migration work-list.
    """

    def __init__(self) -> None:
        self.scope: list[str] = []
        self.aliases: dict[str, str] = {}
        self.found: dict[str, set[str]] = defaultdict(set)

    @property
    def symbol(self) -> str:
        return ".".join(self.scope) if self.scope else "<module>"

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        service = GL_SERVICES.get(node.module or "")
        if service is None:
            return
        for imported in node.names:
            self.aliases[imported.asname or imported.name] = service
        # An import inside a function body is itself the dependency site.
        if self.scope:
            self.found[self.symbol].add(service)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for imported in node.names:
            service = GL_SERVICES.get(imported.name)
            if service is None:
                continue
            self.aliases[imported.asname or imported.name.rsplit(".", 1)[-1]] = service
            if self.scope:
                self.found[self.symbol].add(service)

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

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        service = self.aliases.get(node.id)
        if service is not None:
            self.found[self.symbol].add(service)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        base = _base_name(node)
        service = self.aliases.get(base) if base else None
        if service is not None:
            self.found[self.symbol].add(service)
        self.generic_visit(node)


def _scan_python_writers(path: str, source: str) -> set[Writer]:
    visitor = _WriterVisitor()
    visitor.visit(ast.parse(source, filename=path))
    by_symbol: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for (symbol, relation), evidence in visitor.found.items():
        by_symbol[symbol][relation].update(evidence)
    return {
        Writer(
            path=path,
            symbol=symbol,
            relations="|".join(sorted(relations)),
            evidence="|".join(
                sorted({kind for kinds in relations.values() for kind in kinds})
            ),
        )
        for symbol, relations in by_symbol.items()
    }


def _scan_python_callers(path: str, source: str) -> set[Caller]:
    visitor = _CallerVisitor()
    visitor.visit(ast.parse(source, filename=path))
    return {
        Caller(path=path, symbol=symbol, services="|".join(sorted(services)))
        for symbol, services in visitor.found.items()
        if services
    }


def _scan_text_writers(path: str, source: str) -> set[Writer]:
    relations = _raw_dml_relations(source)
    if not relations:
        return set()
    return {
        Writer(
            path=path,
            symbol="<file>",
            relations="|".join(sorted(relations)),
            evidence="raw_sql_dml",
        )
    }


def _source_files() -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for root_name in RUNTIME_ROOTS:
        root = REPO_ROOT / root_name
        assert root.is_dir(), f"GL entry-point family disappeared: {root_name}"
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in {".py", ".sh", ".sql"}:
                continue
            if SKIPPED_DIR_NAMES.intersection(path.relative_to(REPO_ROOT).parts):
                continue
            files.append(
                (
                    path.relative_to(REPO_ROOT).as_posix(),
                    path.read_text(encoding="utf-8"),
                )
            )
    return files


def _scan_writers() -> set[Writer]:
    found: set[Writer] = set()
    for relative, source in _source_files():
        if relative.endswith(".py"):
            found.update(_scan_python_writers(relative, source))
        else:
            found.update(_scan_text_writers(relative, source))
    return found


def _scan_callers() -> set[Caller]:
    found: set[Caller] = set()
    for relative, source in _source_files():
        if relative.endswith(".py"):
            found.update(_scan_python_callers(relative, source))
    return found


def _load(inventory: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    with inventory.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert reader.fieldnames == list(fields), (
            f"{inventory.name} header drifted: {reader.fieldnames}"
        )
        rows = list(reader)
    keys = [(row["path"], row["symbol"]) for row in rows]
    assert len(set(keys)) == len(keys), f"{inventory.name} has duplicate rows"
    assert keys == sorted(keys), f"{inventory.name} must stay sorted by path, symbol"
    return rows


def _check_final_state(
    row: dict[str, str], legal: dict[str, frozenset[str]], kind: str
) -> None:
    disposition = row["disposition"]
    assert disposition in legal, (
        f"unknown {kind} disposition {disposition!r} for {row['path']}"
    )
    assert row["final_state"] in legal[disposition], (
        f"{row['path']}::{row['symbol']} is {disposition!r} with final state "
        f"{row['final_state']!r}; legal final states for that disposition are "
        f"{sorted(legal[disposition])}. Every row must state where it ENDS, not "
        "only what it is."
    )


def _recorded_writers() -> set[Writer]:
    rows = _load(WRITER_INVENTORY, WRITER_FIELDS)
    for row in rows:
        _check_final_state(row, WRITER_FINAL_STATES, "writer")
    return {
        Writer(
            path=row["path"],
            symbol=row["symbol"],
            relations=row["relations"],
            evidence=row["evidence"],
        )
        for row in rows
    }


def _recorded_callers() -> set[Caller]:
    rows = _load(CALLER_INVENTORY, CALLER_FIELDS)
    for row in rows:
        _check_final_state(row, CALLER_FINAL_STATES, "caller")
    return {
        Caller(path=row["path"], symbol=row["symbol"], services=row["services"])
        for row in rows
    }


def _render(rows: set[Writer] | set[Caller]) -> str:
    return "\n".join("\t".join(dataclasses.astuple(row)) for row in sorted(rows)) or "-"


def test_gl_writer_inventory_is_exact() -> None:
    discovered = _scan_writers()
    recorded = _recorded_writers()
    assert discovered == recorded, (
        "ERP's general-ledger writer set moved. An addition is new GL authority "
        "the Accounting cutover will have to seal; a removal is retired authority "
        "whose row must come out of docs/inventories/accounting-gl-writers.tsv in "
        "the same change."
        f"\n\nUnrecorded:\n{_render(discovered - recorded)}"
        f"\n\nNo longer detected:\n{_render(recorded - discovered)}"
    )


def test_gl_caller_inventory_is_exact() -> None:
    discovered = _scan_callers()
    recorded = _recorded_callers()
    assert discovered == recorded, (
        "ERP's general-ledger caller set moved. Every row is a site that must be "
        "repointed at the module contract (or explicitly kept) before the "
        "Accounting cutover can seal a single writer."
        f"\n\nUnrecorded:\n{_render(discovered - recorded)}"
        f"\n\nNo longer detected:\n{_render(recorded - discovered)}"
    )


def test_every_recorded_relation_is_a_real_gl_relation() -> None:
    """A ledger row naming a relation ERP does not have is stale evidence."""
    known = set(GL_MODELS.values())
    for row in _load(WRITER_INVENTORY, WRITER_FIELDS):
        for relation in row["relations"].split("|"):
            assert relation in known, f"{row['path']}: unknown relation {relation!r}"


def test_a_kept_writer_touches_only_relations_erp_actually_keeps() -> None:
    """The invariant that makes `keep_local` a decision instead of a parking bay.

    `keep_local` says ERP goes on writing this relation after the cutover.  That
    is true only for `RETAINED_ERP_RELATIONS`.  A row claiming `keep_local` while
    writing `gl.journal_entry` is a migrating writer that will still be posting
    after Accounting owns posting — two writers, one ledger, which is the failure
    this whole slice exists to prevent.

    It bites on the subtler case too: `LedgerPostingService._retire_superseded_batch_key`
    writes only `gl.posting_batch`, which has no module counterpart.  Under a
    "no counterpart therefore kept" rule it would have been filed `keep_local` —
    promising an ERP writer inside a service that is being sealed.
    """
    from app.accounting_adoption import RETAINED_ERP_RELATIONS

    kept = set(RETAINED_ERP_RELATIONS)
    offenders = [
        (row["path"], row["symbol"], sorted(set(row["relations"].split("|")) - kept))
        for row in _load(WRITER_INVENTORY, WRITER_FIELDS)
        if row["disposition"] == "keep_local"
        and not set(row["relations"].split("|")) <= kept
    ]
    assert not offenders, (
        "keep_local writers touching relations Accounting takes over: "
        f"{offenders}. Either the disposition is wrong, or RELATION_OWNERSHIP is."
    )


def test_a_retiring_writer_actually_touches_a_migrating_relation() -> None:
    """The mirror invariant.

    A row marked `retire_with_accounting_cutover` that only writes relations ERP
    retains would be retired for nothing — work booked against a cutover that
    does not need it.  Note the asymmetry with the check above: retiring requires
    touching something NOT retained, which admits both migrating relations and
    relations that end with their writer.
    """
    from app.accounting_adoption import RETAINED_ERP_RELATIONS

    offenders = [
        (row["path"], row["symbol"], row["relations"])
        for row in _load(WRITER_INVENTORY, WRITER_FIELDS)
        if row["disposition"] == "retire_with_accounting_cutover"
        and set(row["relations"].split("|")) <= set(RETAINED_ERP_RELATIONS)
    ]
    assert not offenders, (
        f"writers marked retiring that touch only retained relations: {offenders}"
    )


def test_a_repointed_operator_tool_is_something_that_can_be_repointed() -> None:
    """`tool_repointed` means "give it the module contract instead".

    A raw `.sql` file cannot call a module contract; its only honest final state
    is `tool_archived`.  Without this check, `tool_repointed` would be a way to
    mark twelve historical SQL one-offs as handled and never look at them again.
    """
    offenders = [
        row["path"]
        for row in _load(WRITER_INVENTORY, WRITER_FIELDS)
        if row["final_state"] == "tool_repointed" and not row["path"].endswith(".py")
    ]
    assert not offenders, (
        f"non-Python entry points cannot be repointed at a module: {offenders}. "
        "A raw SQL one-off is retired by archiving it."
    )


def test_an_archived_tool_is_not_already_archived() -> None:
    """`scripts/archive/` is outside the scan, so a row for a file already there
    is impossible — and a row that claims `tool_archived` while still being
    scanned is precisely the outstanding work this state describes."""
    offenders = [
        row["path"]
        for row in _load(WRITER_INVENTORY, WRITER_FIELDS)
        if row["final_state"] == "tool_archived" and "/archive/" in row["path"]
    ]
    assert not offenders, (
        f"already-archived files must not be in the ledger: {offenders}"
    )


def test_a_kept_caller_depends_only_on_decisions_erp_keeps() -> None:
    """`keep_local` for a caller means the decision it consumes does not move."""
    from app.accounting_adoption import RETAINED_GL_DECISIONS

    offenders = [
        (
            row["path"],
            row["symbol"],
            sorted(set(row["services"].split("|")) - RETAINED_GL_DECISIONS),
        )
        for row in _load(CALLER_INVENTORY, CALLER_FIELDS)
        if row["disposition"] == "keep_local"
        and not set(row["services"].split("|")) <= RETAINED_GL_DECISIONS
    ]
    assert not offenders, (
        f"keep_local callers depending on migrating decisions: {offenders}"
    )


def test_a_repointed_caller_depends_on_a_decision_that_actually_moves() -> None:
    from app.accounting_adoption import RETAINED_GL_DECISIONS

    offenders = [
        (row["path"], row["symbol"], row["services"])
        for row in _load(CALLER_INVENTORY, CALLER_FIELDS)
        if row["disposition"] == "repoint_to_module"
        and set(row["services"].split("|")) <= RETAINED_GL_DECISIONS
    ]
    assert not offenders, (
        f"callers marked for repointing that depend on nothing moving: {offenders}"
    )


def test_a_gl_internal_caller_really_lives_inside_the_gl_owner() -> None:
    """`gl_internal` says "this leaves with the owner".  A caller outside the
    owner's own packages does not leave with it; it is a migrating caller
    borrowing the owner's disposition."""
    offenders = [
        row["path"]
        for row in _load(CALLER_INVENTORY, CALLER_FIELDS)
        if row["disposition"] == "gl_internal"
        and not row["path"].startswith(GL_OWNER_PREFIXES)
    ]
    assert not offenders, (
        f"gl_internal rows outside the GL owner: {sorted(set(offenders))}"
    )


def test_every_row_carries_a_final_state_and_the_check_bites() -> None:
    """Sensitivity proof for the pairing rule itself (ADR-0018).

    The checks above pass over ledgers that happen to be well-formed.  These
    plant each way a row can be mis-filed and require a refusal, so the pairing
    rule cannot quietly become a spelling check.
    """
    import pytest

    for bad in (
        {"path": "p", "symbol": "s", "disposition": "keep_local", "final_state": ""},
        {
            "path": "p",
            "symbol": "s",
            "disposition": "keep_local",
            "final_state": "writer_removed",
        },
        {"path": "p", "symbol": "s", "disposition": "invented", "final_state": "x"},
        {
            "path": "p",
            "symbol": "s",
            "disposition": "operator_tool",
            "final_state": "retained_erp_writer",
        },
    ):
        with pytest.raises(AssertionError):
            _check_final_state(bad, WRITER_FINAL_STATES, "writer")

    _check_final_state(
        {
            "path": "p",
            "symbol": "s",
            "disposition": "operator_tool",
            "final_state": "tool_archived",
        },
        WRITER_FINAL_STATES,
        "writer",
    )


def test_every_recorded_service_is_a_real_gl_service() -> None:
    known = set(GL_SERVICES.values())
    for row in _load(CALLER_INVENTORY, CALLER_FIELDS):
        for service in row["services"].split("|"):
            assert service in known, f"{row['path']}: unknown service {service!r}"


def test_gl_service_modules_all_exist() -> None:
    """The caller detector keys on module paths; a renamed module would make it
    silently find nothing, which reads as "no callers" — the exact failure a
    ratchet is supposed to make impossible."""
    missing = [
        module
        for module in GL_SERVICES
        if not (REPO_ROOT / Path(*module.split("."))).with_suffix(".py").is_file()
    ]
    assert not missing, f"GL service modules moved or were renamed: {missing}"


def test_gl_model_modules_all_exist() -> None:
    package = REPO_ROOT / Path(*GL_MODEL_PACKAGE.split("."))
    assert package.is_dir(), "the gl model package moved"
    declared = {
        name
        for path in package.glob("*.py")
        for name in re.findall(
            r"^class (\w+)\(", path.read_text(encoding="utf-8"), re.M
        )
    }
    missing = sorted(set(GL_MODELS) - declared)
    assert not missing, f"GL model classes moved or were renamed: {missing}"


def test_writer_detector_is_sensitive_to_every_evidence_shape() -> None:
    """The ratchet above passes over a tree that happens to match; prove the
    detector bites on each mutation shape it claims to cover (ADR-0018)."""
    planted = _scan_python_writers(
        "app/tasks/planted.py",
        """
from app.models.finance.gl.journal_entry import JournalEntry
from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
from sqlalchemy import delete, text


def constructs(db):
    entry = JournalEntry(reference="x")
    db.add(entry)
    entry.status = "POSTED"
    setattr(entry, "posted_at", None)


def bulk(db, lines):
    db.add_all([PostedLedgerLine(amount=1)])
    db.execute(delete(PostedLedgerLine))
    db.execute(text("UPDATE gl.journal_entry SET status = 'VOID'"))
""",
    )
    by_symbol = {writer.symbol: writer for writer in planted}
    assert by_symbol["constructs"].relations == "gl.journal_entry"
    assert set(by_symbol["constructs"].evidence.split("|")) == {
        "attribute_assignment",
        "constructor",
        "session_add",
        "setattr",
    }
    assert set(by_symbol["bulk"].relations.split("|")) == {
        "gl.journal_entry",
        "gl.posted_ledger_line",
    }
    assert set(by_symbol["bulk"].evidence.split("|")) == {
        "constructor",
        "raw_sql_dml",
        "session_add_all",
        "sql_dml",
    }


def test_writer_detector_does_not_fire_on_reads_or_same_named_imports() -> None:
    """Two false positives worth proving absent: a pure read, and a class called
    `Account` that came from somewhere other than the GL model package."""
    assert not _scan_python_writers(
        "app/services/planted_read.py",
        """
from app.models.finance.gl.account import Account


def reads(db):
    return db.query(Account).filter(Account.code == "1000").all()
""",
    )
    assert not _scan_python_writers(
        "app/services/planted_other_account.py",
        """
from app.models.finance.banking.bank_account import Account


def creates(db):
    db.add(Account(name="operating"))
""",
    )


def test_raw_dml_detector_requires_the_gl_schema() -> None:
    """`journal_entry` unqualified is a different table in a different schema;
    the ledger is about `gl.*` and must not inherit someone else's rows."""
    assert _raw_dml_relations("INSERT INTO gl.posted_ledger_line (id) VALUES (1)") == {
        "gl.posted_ledger_line"
    }
    assert _raw_dml_relations('UPDATE "gl"."account" SET code = 1') == {"gl.account"}
    assert _raw_dml_relations("UPDATE journal_entry SET status = 'X'") == set()
    assert _raw_dml_relations("SELECT * FROM gl.journal_entry") == set()


def test_caller_detector_attributes_use_sites_not_files() -> None:
    planted = _scan_python_callers(
        "app/services/planted_caller.py",
        """
from app.services.finance.gl.ledger_posting import LedgerPostingService
from app.services.finance.gl.period_guard import assert_period_open


def posts(db, request):
    assert_period_open(db, request.date)
    return LedgerPostingService(db).post(request)


def unrelated(db):
    return db.query(object).all()
""",
    )
    by_symbol = {caller.symbol: caller.services for caller in planted}
    assert by_symbol == {"posts": "gl.period_guard|gl.poster"}
