"""A ratchet on code that opens its OWN database session and commits on it.

Why this matters: a function that opens a fresh session mid-call-stack and
commits it completes a whole transaction invisibly to whatever caller is
already holding an ambient transaction open. That write is never rolled back
with the caller's failure, and it can deadlock against row locks the caller
already holds. ERP had no guard against this class before this file.

Two shapes are flagged, each evaluated over a function's OWN body only (code
inside a nested ``def``/``async def``/``lambda`` is a separate scan unit, not
swept into the enclosing function):

    (a) the function itself constructs a session via ``SessionLocal()`` /
        ``AsyncSessionLocal()`` (bound to a local name, by assignment or by
        ``with ... as name``) and calls ``.commit()`` on that same name.
    (b) the function enters (``with``/``async with``) a call to a helper
        NAMED in :data:`COMMIT_ON_EXIT_HELPER_NAMES` below -- a helper this
        module has manually reviewed and confirmed commits a session it
        opened itself, on its own ``__exit__``. Entering such a helper
        delegates the same escape to the caller, so the caller is the hit,
        not the (already-reviewed) helper.

Per-helper review of every session-opening helper in ``app/db/`` (the
session authority) and ``app/db/session_context.py`` -- this is what decides
membership in :data:`COMMIT_ON_EXIT_HELPER_NAMES`, and it is empty today:

- ``app/db/session_context.py:176`` ``tenant_scope_for_session`` -- binds an
  EXISTING session passed in as a parameter (never calls ``SessionLocal()``
  itself); the docstring at line 188 states "The caller remains responsible
  for commit/rollback and close." No commit anywhere in its body. Not (a)
  (no self-opened session) and not (b) (no commit).
- ``app/db/session_context.py:205`` ``session_for_org`` -- opens its own
  session at line 241 (``session = SessionLocal()``), yields it at line 245,
  and only closes it in ``finally`` at line 247. Every documented call
  pattern (lines 216-220, 225-228 of that file) has the CALLER commit inside
  the ``with`` block. No commit in the helper's own body. Not (b).
- ``app/db/session_context.py:251`` ``cross_org_session`` -- opens its own
  session at line 284, yields it at line 289, and only restores the
  ``allow_cross_org`` marker and closes at lines 291-292. No commit. Not (b).
- ``app/db/__init__.py:142`` ``get_db`` and ``app/db/__init__.py:184``
  ``get_db_session`` -- both open their own session and only ``close()`` in
  ``finally`` (lines 146-148 and 195-197). No commit. Not (b). (Also excluded
  as ``app/db/`` itself -- see below.)
- ``app/db/__init__.py:201`` ``transaction(db)`` and
  ``app/db/__init__.py:241`` ``atomic_operation(db)`` -- both DO commit on
  exit (line 234's ``db.commit()`` / the nested-savepoint branch's line 226
  ``nested.commit()``; line 259's ``savepoint.commit()``), but on the
  PASSED-IN ``db`` parameter, never a session either of them opened itself.
  This is exactly the "commit on a passed-in ``db`` parameter" near-miss this
  ratchet must not flag (see the sensitivity proof below) -- and both also
  live in ``app/db/`` itself, excluded on that ground too.

Because none of the real helpers commit a self-opened session on exit,
:data:`COMMIT_ON_EXIT_HELPER_NAMES` is empty today; the sensitivity proof
below plants a synthetic helper that DOES, to prove shape (b) is not dead
code, and :func:`own_session_commit_hits` takes the helper-name set as a
parameter precisely so that proof does not depend on ERP growing a real one.

Exclusions, each with its enforceable premise:

1. ``app/db/`` itself is the session authority; excluded wholesale
   (:data:`EXCLUDED_PREFIX`) -- it is the one place a session is legitimately
   constructed, and its own transaction-boundary helpers are reviewed above.
2. FastAPI request-scoped dependency plumbing: an UNDECORATED generator
   function (one with a ``yield`` in its own body, and not decorated with
   ``@contextmanager``/``@asynccontextmanager``) is exempt from both shapes.
   Enforceable premise: for such a function, the code between construction
   and ``yield`` runs when FastAPI calls the dependency, but the business
   logic that uses the session runs in the FRAMEWORK's own calling frame
   during the ``yield`` -- this function establishes the top-level request
   transaction, an ambient transaction does not exist yet for it to escape.
   A ``@contextmanager``-decorated generator has no such framework-owned
   caller frame -- entering it via ``with`` runs its post-``yield`` code at
   the ``with`` block's own exit, in the entering function's stack -- so it
   is NOT exempted by this rule and is evaluated as an ordinary function
   (this is what lets a genuine future commit-on-exit helper still be caught
   at its own definition, as shape (a), even before anyone curates it into
   :data:`COMMIT_ON_EXIT_HELPER_NAMES` for shape-(b) caller detection).
   Verified against every real hit this file's own scan currently excludes:
   ``app/web/careers.py:35``, ``app/web/onboarding_portal.py:29``,
   ``app/api/dotmac_academy.py:38``, ``app/api/careers.py:38``,
   ``app/api/dotmac_sub.py:30``, ``app/api/finance/banking.py:63``,
   ``app/api/finance/payments.py:51``, ``app/api/deps.py:73`` (
   ``get_db_with_org``), ``app/api/deps.py:115`` (``_yield_bypass_session``),
   ``app/api/service_principal.py:151`` (``get_db_with_service_org``),
   ``app/web/deps.py:1545`` (``get_db_for_org``), and
   ``app/web/deps.py:1591`` (``get_async_db_for_org``) -- every one is a bare
   generator, undecorated, used only via ``Depends(...)``.
3. A helper that only ever calls ``.rollback()`` on its self-opened session
   is naturally excluded -- shape (a) requires an actual ``.commit()`` call
   on the opened name, so there is nothing to match.
4. A commit on a parameter the function did not itself open (e.g.
   ``def f(db): ... db.commit()``) is naturally excluded -- only names bound
   by an in-body ``SessionLocal()``/``AsyncSessionLocal()`` construction are
   ever tracked as "opened", so a pre-existing parameter is never in that set
   regardless of what is called on it.

Baseline: :data:`BASELINE` records the current, reviewed hit set. A hit
outside it, or a stale entry no longer produced by the scan, both fail --
this is the two-directional ratchet.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"

#: Directory prefix (relative to REPO_ROOT, POSIX-separated) excluded
#: wholesale: the session authority itself. See exclusion 1 above.
EXCLUDED_PREFIX = "app/db/"

#: Names bound to the two known session factories in app/db/__init__.py.
SESSION_FACTORY_NAMES = frozenset({"SessionLocal", "AsyncSessionLocal"})

#: Helpers manually reviewed (module docstring above) and confirmed to open
#: their OWN session and commit it on their own exit. Empty today -- every
#: real session_context.py/app.db helper either never commits, or commits on
#: a passed-in parameter rather than a session it opened itself.
COMMIT_ON_EXIT_HELPER_NAMES: frozenset[str] = frozenset()

#: Decorator names that mark a generator as a genuine context manager (its
#: post-yield code runs at the *entering* function's ``with``-exit, not in a
#: framework-owned caller frame) -- see exclusion 2 above.
CONTEXTMANAGER_DECORATOR_NAMES = frozenset({"contextmanager", "asynccontextmanager"})

BASELINE: dict[str, str] = {
    "app/services/infrastructure_health.py::run_infrastructure_health_checks": (
        "grandfathered: unreviewed — own-session commit; classify as "
        "adapter-owned (task/script entry point) or legacy out-of-band writer"
    ),
}


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


def _own_body_nodes(func: ast.FunctionDef | ast.AsyncFunctionDef):
    """Every descendant reachable from ``func`` without crossing into a
    nested function/async function/lambda -- those are separate scan units."""

    def _walk(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            yield from _walk(child)

    yield from _walk(func)


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


def own_session_commit_hits(
    tree: ast.AST,
    *,
    commit_on_exit_helpers: frozenset[str] = COMMIT_ON_EXIT_HELPER_NAMES,
) -> list[tuple[str, str]]:
    """Return ``(qualname, shape)`` for every function in ``tree`` that opens
    its own session and commits it -- ``shape`` is ``"a"`` or ``"b"`` per the
    module docstring."""

    hits: list[tuple[str, str]] = []
    collector = _QualnameCollector()
    collector.visit(tree)

    for qualname, func in collector.functions:
        own_nodes = list(_own_body_nodes(func))

        opened_vars: dict[str, int] = {}
        for node in own_nodes:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)
                and _call_target_name(node.value.func) in SESSION_FACTORY_NAMES
            ):
                opened_vars[node.targets[0].id] = node.lineno
            if (
                isinstance(node, ast.withitem)
                and isinstance(node.optional_vars, ast.Name)
                and isinstance(node.context_expr, ast.Call)
                and _call_target_name(node.context_expr.func) in SESSION_FACTORY_NAMES
            ):
                opened_vars[node.optional_vars.id] = node.context_expr.lineno

        has_yield = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in own_nodes)
        is_dependency_shape = has_yield and not _is_contextmanager_decorated(func)
        if is_dependency_shape:
            continue

        opens_and_commits = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "commit"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in opened_vars
            for node in own_nodes
        )
        if opens_and_commits:
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


def scan_repo(app_root: Path = APP_ROOT) -> dict[str, str]:
    """``path::qualname`` -> shape, for every hit under ``app/`` (excluding
    :data:`EXCLUDED_PREFIX`)."""

    hits: dict[str, str] = {}
    for path in sorted(app_root.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(EXCLUDED_PREFIX):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for qualname, shape in own_session_commit_hits(tree):
            hits[f"{rel}::{qualname}"] = shape
    return hits


def test_no_hit_outside_baseline() -> None:
    current = set(scan_repo())
    new = sorted(current - set(BASELINE))
    assert not new, (
        "These functions open their own database session and commit it, "
        "escaping any caller's ambient transaction, and are not in the "
        f"reviewed baseline: {new}. Either route through app.db's session "
        "authority (a caller-supplied session, or a reviewed "
        "app/db/session_context.py helper) or add the hit to BASELINE with "
        "a reviewed classification."
    )


def test_baseline_has_no_stale_entries() -> None:
    current = scan_repo()
    stale = sorted(set(BASELINE) - set(current))
    assert not stale, (
        "These BASELINE entries no longer match a real hit -- remove them "
        f"so the ratchet keeps its grip: {stale}"
    )


def test_baseline_reasons_are_correctly_classified() -> None:
    """``grandfathered`` and ``adapter-owned`` are distinct claims; keep them
    honest against the real repository, not just internally consistent."""

    for key, reason in BASELINE.items():
        assert reason.startswith(("grandfathered:", "adapter-owned:")), (
            f"{key} has an unrecognized BASELINE reason: {reason!r}"
        )

    # app/services/infrastructure_health.py::run_infrastructure_health_checks
    # is called only by app/tasks/infrastructure_health.py's
    # run_infrastructure_health_checks_task, which IS the @celery_app.task
    # entry point -- but the function this ratchet flags is the plain
    # service function, never itself decorated. It is not verified to BE an
    # entry point, so it must stay grandfathered, not adapter-owned.
    key = "app/services/infrastructure_health.py::run_infrastructure_health_checks"
    assert BASELINE[key].startswith("grandfathered:")


def _tree(source: str) -> ast.AST:
    return ast.parse(textwrap.dedent(source))


def test_sensitivity_proof_plants_and_near_misses() -> None:
    """Plant every flagged shape and every near-miss side by side; the
    detected set must equal the planted set exactly -- proving the detector
    both fires on the real defect shapes and does not fire on the shapes
    that must stay silent."""

    planted = """
        from contextlib import contextmanager

        from app.db import SessionLocal

        # --- shape (a): opens its own session and commits it directly ---
        def opens_and_commits():
            db = SessionLocal()
            db.add(1)
            db.commit()

        # --- a reviewed commit-on-exit helper (also its own shape-(a) hit,
        # per the module docstring: a @contextmanager-decorated generator is
        # never exempted by the FastAPI-dependency rule) ---
        @contextmanager
        def committing_scope():
            db = SessionLocal()
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
            db = SessionLocal()
            db.add(1)

        # --- near-miss: rollback-only helper ---
        def rollback_only():
            db = SessionLocal()
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

        # --- near-miss: undecorated FastAPI dependency-injection shape ---
        def fastapi_dependency_shape():
            db = SessionLocal()
            try:
                yield db
                db.commit()
            finally:
                db.close()
    """

    tree = _tree(planted)

    # With no curated commit-on-exit helper named, shape (b) cannot fire --
    # proves the caller-side check is not vacuously true.
    assert own_session_commit_hits(tree, commit_on_exit_helpers=frozenset()) == [
        ("opens_and_commits", "a"),
        ("committing_scope", "a"),
    ]

    hits = set(
        own_session_commit_hits(
            tree, commit_on_exit_helpers=frozenset({"committing_scope"})
        )
    )
    assert hits == {
        ("opens_and_commits", "a"),
        ("committing_scope", "a"),
        ("caller_uses_committing_scope", "b"),
    }


def test_a_reviewed_helper_that_never_commits_is_not_flagged() -> None:
    """Near-miss (real-shape proof): the actual
    ``app/db/session_context.py`` ``session_for_org`` shape -- opens its own
    session, yields it, only closes on exit -- must not be flagged, even
    though it constructs a session exactly like a real hit does."""

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


def test_method_hits_are_named_class_dot_method() -> None:
    planted = """
        from app.db import SessionLocal

        class Reconciler:
            def run(self):
                db = SessionLocal()
                db.add(1)
                db.commit()
    """
    assert own_session_commit_hits(_tree(planted)) == [("Reconciler.run", "a")]


def test_app_db_itself_is_excluded_from_the_repo_scan() -> None:
    """Real-repository proof for exclusion 1: app/db/session_context.py
    genuinely opens its own sessions (session_for_org, cross_org_session)
    but must never appear in the live scan."""

    hits = scan_repo()
    assert not any(path.startswith("app/db/session_context.py::") for path in hits), (
        "app/db/ is the session authority and must be excluded wholesale"
    )
