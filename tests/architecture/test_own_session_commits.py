"""A ratchet on code that opens its OWN database session and commits on it.

Why this matters: a function that opens a fresh session mid-call-stack and
commits it completes a whole transaction invisibly to whatever caller is
already holding an ambient transaction open. That write is never rolled back
with the caller's failure, and it can deadlock against row locks the caller
already holds.

SCOPE. Scanned roots: :data:`SCANNED_ROOTS` -- ``app/``, ``scripts/`` and
``tools/`` -- every Python entry-point family found in this repository
(routes/deps, Celery tasks, one-off/maintenance scripts, CLI tools).
UNMONITORED, named explicitly per ADR-0018 rather than left implicit:
``alembic/`` (442 files) -- Alembic owns migration transactions through its
own `op`/`context` machinery, a wholly different lifecycle this ratchet does
not model, and it contains no `SessionLocal`/session-factory usage today
(verified by grep); and ``tests/`` -- test fixtures are not a production
entry-point family and legitimately construct throwaway sessions per test.

WHAT IS FLAGGED. Each function is evaluated over its OWN body only (code
inside a nested ``def``/``async def``/``lambda`` is a separate scan unit).
A hit is a function that:

    (a) constructs its own session -- via ``SessionLocal()``/
        ``AsyncSessionLocal()`` (or an import alias of either), a call to
        ``session_for_org``/``cross_org_session`` (the reviewed
        session_context.py openers -- entering them and committing inside
        the block is exactly the escape those helpers document as the
        caller's responsibility), or a call to any name/attribute ending in
        ``session_factory`` (an injected factory, e.g. ``self._session_factory()``
        -- see ``app/services/people/payroll/event_handlers.py``) -- bound by
        plain assignment, annotated assignment, an attribute target
        (``self.db = ...``), or ``with``/``async with ... as x`` -- AND
        commits it: an explicit ``x.commit()``/``await x.commit()`` call on
        that bound name, OR entering ``x.begin()`` (or
        ``SessionLocal.begin()``/``SessionLocal().begin()`` directly) as a
        context manager, which commits automatically on normal exit with no
        separate ``.commit()`` call.
    (b) enters (``with``/``async with``) a call to a helper named in
        :data:`COMMIT_ON_EXIT_HELPER_NAMES` -- manually reviewed and
        confirmed to open its own session and commit it on its own exit.
        Entering such a helper delegates the same escape to the caller, so
        the CALLER is the hit, not the (already-reviewed) helper.

Per-helper review of every session-opening helper in ``app/db/`` (the
session authority) and ``app/db/session_context.py`` -- this is what decides
membership in :data:`COMMIT_ON_EXIT_HELPER_NAMES`, and it is empty today:

- ``app/db/session_context.py:176`` ``tenant_scope_for_session`` -- binds an
  EXISTING session passed in as a parameter (never calls ``SessionLocal()``
  itself); the docstring at line 188 states "The caller remains responsible
  for commit/rollback and close." No commit anywhere in its body.
- ``app/db/session_context.py:205`` ``session_for_org`` -- opens its own
  session (line 241), yields it (line 245), only closes it in ``finally``
  (line 247). Every documented call pattern (lines 216-220, 225-228) has the
  CALLER commit inside the ``with`` block -- which is exactly why this
  ratchet treats *calling* ``session_for_org`` as opening-your-own-session
  under shape (a) above, rather than exempting it.
- ``app/db/session_context.py:251`` ``cross_org_session`` -- opens its own
  session (line 284), yields it (line 289), only restores the
  ``allow_cross_org`` marker and closes (lines 291-292). No commit.
- ``app/db/__init__.py:142`` ``get_db`` / ``:184`` ``get_db_session`` -- open
  their own session and only ``close()`` in ``finally`` (146-148, 195-197).
  No commit. (Also excluded wholesale as ``app/db/`` itself.)
- ``app/db/__init__.py:201`` ``transaction(db)`` / ``:241``
  ``atomic_operation(db)`` -- DO commit on exit (line 234 / the
  nested-savepoint branch's line 226; line 259), but on the PASSED-IN ``db``
  parameter, never a session either opened itself -- the "commit on a
  passed-in db parameter" near-miss this ratchet must not flag. Also live in
  ``app/db/``, excluded on that ground too.

Because none of the real helpers commit a self-opened session on exit,
:data:`COMMIT_ON_EXIT_HELPER_NAMES` is empty today; the sensitivity proof
plants a synthetic helper that DOES, so shape (b) is proven live rather than
dead code, without depending on ERP ever growing a real one.

EXCLUSIONS, each with its enforceable premise:

1. ``app/db/`` itself is the session authority; excluded wholesale
   (:data:`EXCLUDED_PREFIXES`).
2. FastAPI request-scoped dependency plumbing. Exempted ONLY when ALL of:
   the function is a generator (has ``yield`` in its own body), it is NOT
   decorated ``@contextmanager``/``@asynccontextmanager``, AND its bare name
   is referenced as ``Depends(<name>)`` somewhere in a scanned root
   (:data:`DEPENDS_REFERENCED_NAMES`, computed once from a real repo-wide
   scan -- not assumed from shape alone). Enforceable premise: FastAPI drives
   such a generator itself, running the caller's business logic during the
   ``yield`` in the FRAMEWORK's own calling frame; this function establishes
   the top-level request transaction rather than escaping an ambient one --
   but only a name FastAPI actually wires up via ``Depends`` gets that
   benefit of the doubt. :func:`test_depends_exempt_generators_are_pinned`
   pins the exact current exempt set so a name silently joining or leaving
   it is a reviewed diff, not a silent scope change.
3. A helper that only ever calls ``.rollback()`` is naturally excluded --
   shape (a) requires a ``.commit()``/``.begin()``-as-context on the opened
   name.
4. A commit on a parameter the function did not itself open (e.g.
   ``def f(db): ... db.commit()``) is naturally excluded -- only names bound
   by an in-body session-opening construction are ever tracked as "opened".

LIMITATION (stated, not implemented): a closure where an INNER nested
function commits the OUTER function's session variable is not tracked --
``_own_body_nodes`` deliberately does not descend into a nested
``def``/``lambda``, so such a split would currently evade this ratchet. This
shape was not found in a scan of the codebase and is called out here rather
than silently assumed away.

CLASSIFICATION (structural, not hand-reviewed -- :func:`_classify`):

- ``adapter-owned: entry point (Celery task decorator)`` -- the function
  itself carries a ``@...task``/``@shared_task`` decorator.
- ``adapter-owned: entry point (CLI command decorator)`` -- the function
  itself carries a ``@...command`` decorator (click/typer).
- ``adapter-owned: entry point (called from \\`if __name__ == "__main__":\\`)``
  -- the function's bare name is called from that module's own
  ``__name__ == "__main__"`` guard.
- ``grandfathered: unreviewed`` -- everything else. This is expected to be
  the overwhelming majority: being called only by one of the above is NOT
  sufficient to earn ``adapter-owned`` (a function must ITSELF be the
  entry point), so most flagged service helpers stay grandfathered.

BASELINE lives in the sidecar file :data:`BASELINE_PATH` (``path::Qualname``
followed by a tab and its classified reason) because the current hit count
is large -- this is characterization of existing debt, not a claim it is
contained to one row. The ratchet is two-directional: a hit outside the
baseline, or a stale baseline row no longer produced by the live scan, both
fail.
"""

from __future__ import annotations

import ast
import functools
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every Python entry-point family this ratchet audits.
SCANNED_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "app",
    REPO_ROOT / "scripts",
    REPO_ROOT / "tools",
)

#: Path prefixes (relative to REPO_ROOT, POSIX-separated) excluded wholesale.
#: See exclusion 1 in the module docstring.
EXCLUDED_PREFIXES: tuple[str, ...] = ("app/db/",)

BASELINE_PATH = Path(__file__).with_name("own_session_commit_baseline.txt")

#: Names bound to the two known session factories in app/db/__init__.py.
SESSION_FACTORY_NAMES = frozenset({"SessionLocal", "AsyncSessionLocal"})

#: session_context.py openers that construct their OWN session and hand it to
#: the caller to commit -- calling one of these IS opening your own session.
SPECIAL_SESSION_OPENERS = frozenset({"session_for_org", "cross_org_session"})

#: Suffix that marks an injected session-factory call (e.g.
#: ``self._session_factory()``), matched case-sensitively against the bare
#: attribute/name text.
INJECTED_FACTORY_SUFFIX = "session_factory"

#: app/db request-scoped generator dependencies that, when advanced by hand
#: with the builtin ``next(...)`` outside FastAPI's own machinery, yield a
#: real session to the caller -- the real shape at
#: ``scripts/import_data.py:463`` (``db = next(get_db_session())``, commits
#: at :495). Only counted when resolved through an actual import of the name
#: from ``app.db`` (see ``_collect_alias_map``), never from bare-name
#: fallback -- both names are common local FastAPI-dependency names too.
NEXT_WRAPPED_OPENER_NAMES = frozenset({"get_db", "get_db_session"})

#: Helpers manually reviewed (module docstring above) and confirmed to open
#: their OWN session and commit it on their own exit. Empty today.
COMMIT_ON_EXIT_HELPER_NAMES: frozenset[str] = frozenset()

#: Decorator names that mark a generator as a genuine context manager.
CONTEXTMANAGER_DECORATOR_NAMES = frozenset({"contextmanager", "asynccontextmanager"})

#: Decorator names that make a function itself a Celery task entry point.
TASK_DECORATOR_NAMES = frozenset({"task", "shared_task"})

#: Decorator names that make a function itself a CLI command entry point.
CLI_COMMAND_DECORATOR_NAMES = frozenset({"command"})


def _decorator_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return None


def _is_contextmanager_decorated(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    return any(
        _decorator_name(d) in CONTEXTMANAGER_DECORATOR_NAMES
        for d in func.decorator_list
    )


def _call_target_name(node: ast.expr) -> str | None:
    """The bare name a Call targets, for ``Name(...)`` or ``x.attr(...)``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _target_key(node: ast.expr | None) -> str | None:
    """A trackable "opened session" key: ``"db"`` for a bare name, or
    ``"self.db"`` for a ``self.`` attribute target. Anything else (tuple
    unpacking, subscripts, non-``self`` attributes) is not tracked."""
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return f"self.{node.attr}"
    return None


def _own_body_nodes(func: ast.FunctionDef | ast.AsyncFunctionDef):
    """Every descendant reachable from ``func`` without crossing into a
    nested function/async function/lambda -- those are separate scan units
    (see the module docstring's stated closure limitation)."""

    def _walk(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            yield from _walk(child)

    yield from _walk(func)


def _collect_alias_map(nodes: list[ast.AST]) -> dict[str, str]:
    """Map a local import name to its canonical factory/opener name, for
    every ``from ... import SessionLocal [as x]``-shaped import found among
    ``nodes`` (module-level or function-local -- both are scanned)."""

    alias: dict[str, str] = {}
    canonical_names = (
        SESSION_FACTORY_NAMES | SPECIAL_SESSION_OPENERS | NEXT_WRAPPED_OPENER_NAMES
    )
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            for name in node.names:
                if name.name in canonical_names:
                    alias[name.asname or name.name] = name.name
    return alias


def _is_session_opening_call(call: ast.Call, alias_map: dict[str, str]) -> bool:
    bare = _call_target_name(call.func)
    if bare is None:
        return False
    if (
        bare == "next"
        and isinstance(call.func, ast.Name)
        and call.args
        and isinstance(call.args[0], ast.Call)
    ):
        inner = _call_target_name(call.args[0].func)
        # Import-resolved only: a bare local ``get_db`` (a common FastAPI
        # dependency name) must not count unless it was imported from app.db.
        return inner in alias_map and alias_map[inner] in NEXT_WRAPPED_OPENER_NAMES
    canonical = alias_map.get(bare, bare)
    if canonical in SESSION_FACTORY_NAMES or canonical in SPECIAL_SESSION_OPENERS:
        return True
    return bare.endswith(INJECTED_FACTORY_SUFFIX)


def _is_begin_call(
    call: ast.Call, alias_map: dict[str, str], opened_vars: dict[str, int]
) -> bool:
    """True for ``SessionLocal.begin()``, ``SessionLocal().begin()``, or
    ``<already-opened-var>.begin()`` -- each commits automatically when
    entered as a context manager, with no separate ``.commit()`` call."""

    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "begin"):
        return False
    value = call.func.value
    if isinstance(value, ast.Name):
        canonical = alias_map.get(value.id, value.id)
        if canonical in SESSION_FACTORY_NAMES:
            return True
        if value.id in opened_vars:
            return True
    return isinstance(value, ast.Call) and _is_session_opening_call(value, alias_map)


class _QualnameCollector(ast.NodeVisitor):
    """Collect every function/method with its dotted qualified name
    (``Class.method`` for methods, plain ``name`` for a module-level or
    nested function)."""

    def __init__(self) -> None:
        self.functions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        self._stack: list[str] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._stack.append(node.name)
        self.functions.append((".".join(self._stack), node))
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()


def _is_main_guard(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def _is_called_from_main_guard(name: str, module_tree: ast.Module) -> bool:
    for node in ast.walk(module_tree):
        if isinstance(node, ast.If) and _is_main_guard(node.test):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and _call_target_name(sub.func) == name:
                    return True
    return False


def _classify(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    module_tree: ast.Module,
    qualname: str,
) -> str:
    if any(_decorator_name(d) in TASK_DECORATOR_NAMES for d in func.decorator_list):
        return "adapter-owned: entry point (Celery task decorator)"
    if any(
        _decorator_name(d) in CLI_COMMAND_DECORATOR_NAMES for d in func.decorator_list
    ):
        return "adapter-owned: entry point (CLI command decorator)"
    # Only a MODULE-LEVEL function called by that exact name inside the
    # module's own ``__main__`` guard is its entry point -- a method or a
    # nested function that merely shares the name is not.
    is_module_level = "." not in qualname and func in module_tree.body
    if is_module_level and _is_called_from_main_guard(func.name, module_tree):
        return 'adapter-owned: entry point (called from `if __name__ == "__main__":`)'
    return "grandfathered: unreviewed"


def collect_depends_referenced_names(
    roots: tuple[Path, ...] = SCANNED_ROOTS,
) -> frozenset[str]:
    """Every bare name seen as ``Depends(<name>)`` (or ``x.Depends(<name>)``)
    anywhere under ``roots``, computed once from the real repository -- see
    exclusion 2 in the module docstring."""

    names: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if any(rel.startswith(p) for p in EXCLUDED_PREFIXES):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and _call_target_name(node.func) == "Depends"
                    and node.args
                    and isinstance(node.args[0], ast.Name)
                ):
                    names.add(node.args[0].id)
    return frozenset(names)


def own_session_commit_hits(
    tree: ast.AST,
    *,
    commit_on_exit_helpers: frozenset[str] = COMMIT_ON_EXIT_HELPER_NAMES,
    depends_referenced_names: frozenset[str] = frozenset(),
) -> list[tuple[str, str]]:
    """Return ``(qualname, shape)`` for every function in ``tree`` that opens
    its own session and commits it -- ``shape`` is ``"a"`` or ``"b"`` per the
    module docstring."""

    hits: list[tuple[str, str]] = []
    collector = _QualnameCollector()
    collector.visit(tree)
    module_alias_map = _collect_alias_map(list(ast.walk(tree)))

    for qualname, func in collector.functions:
        own_nodes = list(_own_body_nodes(func))
        alias_map = dict(module_alias_map)
        alias_map.update(_collect_alias_map(own_nodes))

        opened_vars: dict[str, int] = {}
        committed_keys: set[str] = set()
        auto_commit = False

        for node in own_nodes:
            value = None
            target: ast.expr | None = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                target, value = node.target, node.value

            if (
                target is not None
                and isinstance(value, ast.Call)
                and _is_session_opening_call(value, alias_map)
            ):
                key = _target_key(target)
                if key:
                    opened_vars[key] = node.lineno

            if isinstance(node, ast.withitem):
                ctx = node.context_expr
                if isinstance(ctx, ast.Call) and _is_session_opening_call(
                    ctx, alias_map
                ):
                    key = _target_key(node.optional_vars)
                    if key:
                        opened_vars[key] = ctx.lineno
                if isinstance(ctx, ast.Call) and _is_begin_call(
                    ctx, alias_map, opened_vars
                ):
                    auto_commit = True
                    # `SessionLocal.begin()`/`SessionLocal().begin()` entered
                    # directly opens AND auto-commits its session in one
                    # expression -- register the bound name as opened too,
                    # since no separate SessionLocal() call exists to do it.
                    key = _target_key(node.optional_vars)
                    if key:
                        opened_vars.setdefault(key, ctx.lineno)

            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "commit"
            ):
                key = _target_key(node.func.value)
                if key and key in opened_vars:
                    committed_keys.add(key)

        has_yield = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in own_nodes)
        is_dependency_shape = (
            has_yield
            and not _is_contextmanager_decorated(func)
            and func.name in depends_referenced_names
        )
        if is_dependency_shape:
            continue

        if opened_vars and (committed_keys or auto_commit):
            hits.append((qualname, "a"))

        enters_commit_on_exit_helper = any(
            isinstance(node, ast.withitem)
            and isinstance(node.context_expr, ast.Call)
            and _call_target_name(node.context_expr.func) in commit_on_exit_helpers
            for node in own_nodes
        )
        if enters_commit_on_exit_helper:
            hits.append((qualname, "b"))

    return hits


def scan_repo(
    roots: tuple[Path, ...] = SCANNED_ROOTS,
) -> dict[str, tuple[str, str]]:
    """``path::qualname`` -> ``(shape, classified reason)`` for every hit."""

    depends_referenced_names = collect_depends_referenced_names(roots)
    hits: dict[str, tuple[str, str]] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if any(rel.startswith(p) for p in EXCLUDED_PREFIXES):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            collector = _QualnameCollector()
            collector.visit(tree)
            by_qualname = dict(collector.functions)
            for qualname, shape in own_session_commit_hits(
                tree, depends_referenced_names=depends_referenced_names
            ):
                reason = _classify(by_qualname[qualname], tree, qualname)
                hits[f"{rel}::{qualname}"] = (shape, reason)
    return hits


@functools.cache
def _scan() -> dict[str, tuple[str, str]]:
    """One repository scan (~13s) shared by every test in this module."""

    return scan_repo()


def _load_baseline() -> dict[str, str]:
    baseline: dict[str, str] = {}
    for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, _, reason = line.partition("\t")
        baseline[key] = reason
    return baseline


def test_no_hit_outside_baseline() -> None:
    current = _scan()
    baseline = _load_baseline()
    new = sorted(set(current) - set(baseline))
    assert not new, (
        "These functions open their own database session and commit it, "
        "escaping any caller's ambient transaction, and are not in the "
        f"reviewed baseline: {new}. Route through app.db's session "
        f"authority, or add the row to {BASELINE_PATH.name}."
    )


def test_baseline_has_no_stale_entries() -> None:
    current = _scan()
    baseline = _load_baseline()
    stale = sorted(set(baseline) - set(current))
    assert not stale, (
        "These baseline rows no longer match a real hit -- remove them so "
        f"the ratchet keeps its grip: {stale}"
    )


def test_baseline_reasons_match_the_structural_classifier() -> None:
    """The baseline's stored reason must equal what the structural
    classifier produces fresh, today -- a hand-edited reason cannot drift
    from the rule that generates it."""

    current = _scan()
    baseline = _load_baseline()
    mismatched = sorted(
        key for key in set(current) & set(baseline) if current[key][1] != baseline[key]
    )
    assert not mismatched, (
        f"These baseline reasons no longer match the classifier: {mismatched}"
    )
    for reason in baseline.values():
        assert reason.startswith(("grandfathered:", "adapter-owned:"))


def test_depends_exempt_generators_are_pinned() -> None:
    """Exact-set proof for exclusion 2: which real, undecorated generators
    are exempted because their name is ``Depends``-referenced somewhere in a
    scanned root. A name silently joining or leaving this set is a reviewed
    diff, never a silent scope change."""

    depends_referenced_names = collect_depends_referenced_names()
    exempted: set[str] = set()
    for root in SCANNED_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if any(rel.startswith(p) for p in EXCLUDED_PREFIXES):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            collector = _QualnameCollector()
            collector.visit(tree)
            for qualname, func in collector.functions:
                own_nodes = list(_own_body_nodes(func))
                has_yield = any(
                    isinstance(n, (ast.Yield, ast.YieldFrom)) for n in own_nodes
                )
                if (
                    has_yield
                    and not _is_contextmanager_decorated(func)
                    and func.name in depends_referenced_names
                ):
                    exempted.add(f"{rel}::{qualname}")

    # app/db/__init__.py::get_db is a real Depends-referenced generator too,
    # but app/db/ is excluded wholesale (exclusion 1) before this rule ever
    # applies, so it never reaches this set.
    # app/web/deps.py::get_async_db and app/api/deps.py::_yield_bypass_session
    # are NOT in this set even though they look like the same shape:
    # get_async_db is never actually referenced via Depends(get_async_db)
    # anywhere (only get_async_db_for_org is used in real routes), and
    # _yield_bypass_session is only ever reached via `yield from
    # _yield_bypass_session()` inside another Depends-referenced function --
    # never `Depends(_yield_bypass_session)` directly. The rule is
    # deliberately this narrow: a name earns the exemption only by being the
    # literal argument to a real `Depends(...)` call.
    expected = {
        "app/api/careers.py::get_db",
        "app/api/deps.py::_get_db",
        "app/api/deps.py::get_db_admin_bypass",
        "app/api/deps.py::get_db_auth_bypass",
        "app/api/deps.py::get_db_with_org",
        "app/api/dotmac_academy.py::get_db",
        "app/api/dotmac_sub.py::get_db",
        "app/api/finance/banking.py::get_db",
        "app/api/finance/payments.py::get_db",
        "app/api/service_principal.py::_get_db",
        "app/api/service_principal.py::get_db_with_service_org",
        "app/services/auth_dependencies.py::_get_db",
        "app/web/careers.py::get_db",
        "app/web/deps.py::get_async_db_for_org",
        "app/web/deps.py::get_db",
        "app/web/deps.py::get_db_for_org",
        "app/web/onboarding_portal.py::get_db",
    }
    missing = sorted(expected - exempted)
    extra = sorted(exempted - expected)
    assert not missing, f"Expected exempt generators no longer exempted: {missing}"
    assert not extra, (
        f"New generators exempted that were not reviewed: {extra}. If this "
        "growth is deliberate, update `expected` in this test with the "
        "review that justified it."
    )


def _tree(source: str) -> ast.AST:
    return ast.parse(textwrap.dedent(source))


def test_sensitivity_proof_plants_and_near_misses() -> None:
    """Plant every flagged shape and every near-miss side by side; the
    detected set must equal the planted set exactly."""

    planted = """
        from contextlib import contextmanager

        from app.db import SessionLocal as _SL
        from app.db import get_db_session
        from app.db.session_context import session_for_org

        # --- shape (a): direct SessionLocal() + .commit() ---
        def opens_and_commits():
            db = _SL()
            db.add(1)
            db.commit()

        # --- shape (a): annotated assignment ---
        def annotated_open_and_commit():
            db: object = _SL()
            db.commit()

        # --- shape (a): self.<attr> attribute target ---
        class Reconciler:
            def run(self):
                self.db = _SL()
                self.db.commit()

        # --- shape (a): with ... as db, db.begin(): (auto-commit, no .commit()) ---
        def with_and_begin():
            with _SL() as db, db.begin():
                db.add(1)

        # --- shape (a): SessionLocal.begin() directly, entered as a context ---
        def factory_begin_direct():
            with _SL.begin() as db:
                db.add(1)

        # --- shape (a): calling session_for_org and committing inside ---
        def calls_session_for_org():
            with session_for_org(1) as db:
                db.add(1)
                db.commit()

        # --- shape (a): injected factory attribute ---
        class Handler:
            def __init__(self, session_factory):
                self._session_factory = session_factory

            def handle(self):
                with self._session_factory() as db:
                    db.add(1)
                    db.commit()

        # --- shape (a): an app.db dependency generator advanced by hand ---
        def advances_get_db_session():
            db = next(get_db_session())
            db.commit()

        # --- a reviewed commit-on-exit helper (also its own shape-(a) hit) ---
        @contextmanager
        def committing_scope():
            db = _SL()
            try:
                yield db
                db.commit()
            finally:
                db.close()

        # --- shape (b): enters that helper, delegating the same escape ---
        def caller_uses_committing_scope():
            with committing_scope() as db:
                db.add(1)

        # --- near-miss: session opened, never committed ---
        def opens_without_commit():
            db = _SL()
            db.add(1)

        # --- near-miss: rollback-only helper ---
        def rollback_only():
            db = _SL()
            try:
                db.add(1)
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        # --- near-miss: commit on a passed-in parameter, not self-opened ---
        def commits_on_parameter(db):
            db.add(1)
            db.commit()

        # --- near-miss: begin() on a passed-in parameter, not self-opened ---
        def begins_on_parameter(db):
            with db.begin():
                db.add(1)

        # --- near-miss: undecorated FastAPI dependency shape, Depends-referenced ---
        def fastapi_dependency_shape():
            db = _SL()
            try:
                yield db
                db.commit()
            finally:
                db.close()

        _ = Depends(fastapi_dependency_shape)
    """

    tree = _tree(planted)
    depends_names = frozenset({"fastapi_dependency_shape"})

    no_helper = set(
        own_session_commit_hits(
            tree,
            commit_on_exit_helpers=frozenset(),
            depends_referenced_names=depends_names,
        )
    )
    assert no_helper == {
        ("opens_and_commits", "a"),
        ("annotated_open_and_commit", "a"),
        ("Reconciler.run", "a"),
        ("with_and_begin", "a"),
        ("factory_begin_direct", "a"),
        ("calls_session_for_org", "a"),
        ("Handler.handle", "a"),
        ("advances_get_db_session", "a"),
        ("committing_scope", "a"),
    }

    with_helper = set(
        own_session_commit_hits(
            tree,
            commit_on_exit_helpers=frozenset({"committing_scope"}),
            depends_referenced_names=depends_names,
        )
    )
    assert with_helper == no_helper | {("caller_uses_committing_scope", "b")}

    # Without the Depends-reference, the same generator IS flagged -- proves
    # exclusion 2 is not vacuously true either.
    unexempted = set(
        own_session_commit_hits(
            tree,
            commit_on_exit_helpers=frozenset(),
            depends_referenced_names=frozenset(),
        )
    )
    assert ("fastapi_dependency_shape", "a") in unexempted


def test_a_reviewed_helper_that_never_commits_is_not_flagged() -> None:
    """Real-shape proof: the actual ``session_for_org`` DEFINITION -- opens
    its own session, yields it, only closes on exit -- must not be flagged,
    even though its caller (per the test above) is."""

    planted = """
        from app.db import SessionLocal

        def session_for_org():
            session = SessionLocal()
            try:
                yield session
            finally:
                session.close()
    """
    assert own_session_commit_hits(_tree(planted)) == []


def test_a_local_get_db_advanced_by_hand_is_not_an_app_db_session() -> None:
    """Near-miss: ``next(get_db())`` on a LOCALLY defined ``get_db`` (a
    common FastAPI dependency name) is not an app.db session opener."""

    planted = """
        def get_db():
            yield object()

        def uses_local():
            db = next(get_db())
            db.commit()
    """
    assert own_session_commit_hits(_tree(planted)) == []


def test_app_db_itself_is_excluded_from_the_repo_scan() -> None:
    hits = _scan()
    assert not any(path.startswith("app/db/") for path in hits), (
        "app/db/ is the session authority and must be excluded wholesale"
    )


def test_alembic_and_tests_are_declared_unmonitored_not_silently_skipped() -> None:
    assert not any(root.name in {"alembic", "tests"} for root in SCANNED_ROOTS), (
        "alembic/ and tests/ must stay named as unmonitored in the module "
        "docstring, not silently added to SCANNED_ROOTS without a decision"
    )
