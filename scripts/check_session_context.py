#!/usr/bin/env python3
"""Guard: enforce dual-layer tenant-context priming at non-HTTP entry points.

Celery tasks (and any other non-request entry point) MUST open database
sessions through the canonical context managers in
``app.db.session_context`` — ``session_for_org`` (single tenant) or
``cross_org_session`` (genuine cross-tenant batch). Those helpers set BOTH
tenant-isolation layers:

1. the SQLAlchemy ORM ``do_orm_execute`` listener (``session.info[...]``), and
2. the PostgreSQL RLS GUC (``SET LOCAL app.current_organization_id``).

A raw ``SessionLocal()`` in a task primes neither, which is a silent bug:
queries either raise ``MissingOrgContextError``, return zero rows under
DB-RLS, or — worst case — read/write across tenants. This check makes that
class of regression fail fast instead of relying on code review.

Scope: ``app/tasks/`` by default. The web/api layers have their own
session-lifecycle owners (``get_db_with_org`` / ``get_db_for_org``) and
legitimately open raw sessions in the "open-unprimed, resolve org, then
``prime_tenant_context``" pattern, so they are intentionally out of scope.

Usage
-----
    # CI / whole-tree mode (no args → scans app/tasks):
    python3 scripts/check_session_context.py
    python3 scripts/check_session_context.py app/tasks

    # Single-file mode (PostToolUse hook): pass one .py path.
    python3 scripts/check_session_context.py app/tasks/finance.py

Exit code 0 = clean, 1 = violation(s) found (printed to stderr).

Escape hatch: append ``# session-context: allow`` to a line that must use a
raw ``SessionLocal()`` for a documented reason (e.g. a session opened purely
to read a non-org-scoped global before priming). Use sparingly.
"""

from __future__ import annotations

import ast
import os
import sys

# Directories whose entry points must use the canonical context managers.
# Both Celery tasks (app/tasks) and one-off admin scripts (app/tools) run
# outside the HTTP request lifecycle, so nothing primes tenant context for
# them. (HTTP routes are out of scope — get_db_with_org / get_db_for_org and
# the documented open-unprimed-then-resolve pattern own that boundary.)
DEFAULT_SCAN_ROOTS = ("app/tasks", "app/tools")

# The canonical helpers a guarded file is expected to use instead.
CANONICAL_HELPERS = ("session_for_org", "cross_org_session")

ALLOW_MARKER = "session-context: allow"


def _is_session_local_call(node: ast.AST) -> bool:
    """True for ``SessionLocal()`` and ``x.SessionLocal()`` call expressions."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "SessionLocal"
    if isinstance(func, ast.Attribute):
        return func.attr == "SessionLocal"
    return False


def check_source(source: str, filepath: str) -> list[str]:
    """Return a list of human-readable violations for one file's source."""
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        # Don't fail the build on unparseable files — other tooling owns that.
        return []

    allowed_lines = {
        lineno
        for lineno, line in enumerate(source.splitlines(), start=1)
        if ALLOW_MARKER in line
    }

    violations: list[str] = []
    for node in ast.walk(tree):
        if _is_session_local_call(node) and node.lineno not in allowed_lines:
            violations.append(
                f"line {node.lineno}: raw `SessionLocal()` — open the session via "
                f"`session_for_org(org_id)` or `cross_org_session()` "
                f"(app.db.session_context) so both tenant layers are primed."
            )
    return violations


def check_file(filepath: str) -> list[str]:
    try:
        with open(filepath) as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return []
    return check_source(source, filepath)


def _iter_python_files(path: str):
    if os.path.isfile(path):
        if path.endswith(".py"):
            yield path
        return
    for root, _dirs, files in os.walk(path):
        if "__pycache__" in root:
            continue
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


def main(argv: list[str]) -> int:
    targets = argv[1:] or list(DEFAULT_SCAN_ROOTS)

    failed = False
    for target in targets:
        # Single-file hook mode: only guard files under a scan root.
        if os.path.isfile(target):
            normalized = target.replace(os.sep, "/")
            if not any(f"/{root}/" in f"/{normalized}" for root in DEFAULT_SCAN_ROOTS):
                continue
        for filepath in _iter_python_files(target):
            violations = check_file(filepath)
            if violations:
                failed = True
                print(f"SESSION-CONTEXT VIOLATION in {filepath}:", file=sys.stderr)
                for v in violations:
                    print(f"  {v}", file=sys.stderr)

    if failed:
        print(
            "\n  -> Non-HTTP entry points must prime BOTH tenant layers "
            "(ORM listener + PostgreSQL RLS).\n"
            "     See app/db/session_context.py and .claude/rules/celery-tasks.md.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
