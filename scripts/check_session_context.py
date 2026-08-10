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

Scope: ``app/tasks``, ``app/tools`` and ``scripts`` — every entry point that
runs outside the HTTP request lifecycle, so nothing primes tenant context for
it. The web/api layers have their own session-lifecycle owners
(``get_db_with_org`` / ``get_db_for_org``) and legitimately open raw sessions
in the "open-unprimed, resolve org, then ``prime_tenant_context``" pattern, so
they are intentionally out of scope.

``scripts/archive/`` is out of scope by design: an archived one-off has
already run and is kept for provenance, not for execution.

Usage
-----
    # CI / whole-tree mode (no args → scans every default root):
    python3 scripts/check_session_context.py
    python3 scripts/check_session_context.py app/tasks

    # Single-file mode (PostToolUse hook): pass one .py path.
    python3 scripts/check_session_context.py app/tasks/finance.py

Exit code 0 = clean, 1 = violation(s) found (printed to stderr).

Two escape hatches, and they mean different things — do not swap one for the
other to make a build pass:

``# session-context: allow`` (per line)
    "Reviewed, and genuinely correct here" — e.g. a session opened purely to
    read a non-org-scoped global before priming. Use sparingly.

``scripts/session_context_legacy.txt`` (per file, with a count)
    "Known-wrong, grandfathered, must shrink." A ratchet over the unscoped
    scripts that predate this guard. A file whose count moves in EITHER
    direction fails: upward is new debt, downward is progress that must be
    recorded by lowering the number. An entry that stops being scanned at all
    (archived, moved, deleted) must be removed.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

# Directories whose entry points must use the canonical context managers.
# Celery tasks (app/tasks), one-off admin scripts (app/tools) and the
# operational script tree (scripts) all run outside the HTTP request
# lifecycle, so nothing primes tenant context for them. (HTTP routes are out
# of scope — get_db_with_org / get_db_for_org and the documented
# open-unprimed-then-resolve pattern own that boundary.)
DEFAULT_SCAN_ROOTS = ("app/tasks", "app/tools", "scripts")

# Skipped anywhere beneath a scan root. `archive` holds one-offs that have
# already run: kept for provenance, never executed again, so priming rules
# do not apply — and moving a script there is the intended way to retire its
# ratchet entry.
SKIPPED_DIR_NAMES = ("__pycache__", "archive")

# The canonical helpers a guarded file is expected to use instead.
CANONICAL_HELPERS = ("session_for_org", "cross_org_session")

ALLOW_MARKER = "session-context: allow"

REPO_ROOT = Path(__file__).resolve().parent.parent
LEGACY_ALLOWLIST_PATH = REPO_ROOT / "scripts" / "session_context_legacy.txt"


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


def load_legacy_ratchet(path: str | os.PathLike[str] | None = None) -> dict[str, int]:
    """Parse the grandfathered-file ratchet into ``{repo-relative path: count}``.

    A missing file is an empty ratchet, which is the strictest possible state
    — the guard then holds every scanned file to zero.
    """
    source = Path(path) if path is not None else LEGACY_ALLOWLIST_PATH
    entries: dict[str, int] = {}
    try:
        text = source.read_text()
    except OSError:
        return entries
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, count = line.rpartition(" ")
        if not name or not count.isdigit():
            raise ValueError(
                f"{source}: malformed line {raw!r} — expected `<path> <count>`"
            )
        entries[name.strip()] = int(count)
    return entries


def _repo_relative(path: str) -> str:
    """Normalize to a repo-relative POSIX path so ratchet keys are comparable."""
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.replace(os.sep, "/")


def _iter_python_files(path: str):
    if os.path.isfile(path):
        if path.endswith(".py"):
            yield path
        return
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIR_NAMES]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


def main(argv: list[str]) -> int:
    targets = argv[1:] or list(DEFAULT_SCAN_ROOTS)
    ratchet = load_legacy_ratchet()

    # Prefixes actually covered by this invocation. A stale ratchet entry can
    # only be reported for a tree that was really walked — otherwise running
    # the guard over `app/tasks` alone would condemn every `scripts/` entry.
    scanned_prefixes: list[str] = []
    walked: set[str] = set()

    failed = False
    for target in targets:
        # Single-file hook mode: only guard files under a scan root.
        if os.path.isfile(target):
            normalized = target.replace(os.sep, "/")
            if not any(f"/{root}/" in f"/{normalized}" for root in DEFAULT_SCAN_ROOTS):
                continue
        else:
            prefix = _repo_relative(target)
            scanned_prefixes.append("" if prefix in (".", "") else f"{prefix}/")

        for filepath in _iter_python_files(target):
            relative = _repo_relative(filepath)
            walked.add(relative)
            violations = check_file(filepath)
            found = len(violations)
            allowed = ratchet.get(relative, 0)

            if found == allowed:
                continue

            failed = True
            if found > allowed and not allowed:
                print(f"SESSION-CONTEXT VIOLATION in {relative}:", file=sys.stderr)
                for v in violations:
                    print(f"  {v}", file=sys.stderr)
            elif found > allowed:
                print(
                    f"SESSION-CONTEXT REGRESSION in {relative}: {found} raw "
                    f"`SessionLocal()` call(s), ratchet allows {allowed}. A "
                    f"grandfathered script may be retired, not extended.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"SESSION-CONTEXT RATCHET STALE for {relative}: now {found} raw "
                    f"`SessionLocal()` call(s), ratchet still claims {allowed}. "
                    f"Lower it in {LEGACY_ALLOWLIST_PATH.name} to record the progress.",
                    file=sys.stderr,
                )

    for relative, allowed in sorted(ratchet.items()):
        if relative in walked:
            continue
        if not any(relative.startswith(p) for p in scanned_prefixes):
            continue
        failed = True
        print(
            f"SESSION-CONTEXT RATCHET STALE for {relative}: listed with {allowed} "
            f"raw `SessionLocal()` call(s) but no longer scanned (archived, moved "
            f"or deleted). Remove its line from {LEGACY_ALLOWLIST_PATH.name}.",
            file=sys.stderr,
        )

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
