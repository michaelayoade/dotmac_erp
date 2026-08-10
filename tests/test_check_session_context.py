"""Tests for the session-context guard (scripts/check_session_context.py).

The guard enforces that non-HTTP entry points (Celery tasks) open DB sessions
through the canonical dual-layer context managers, never a raw ``SessionLocal()``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_session_context.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("check_session_context", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_module()


def test_flags_raw_session_local():
    src = "from app.db import SessionLocal\n\ndef run():\n    db = SessionLocal()\n    return db\n"
    violations = guard.check_source(src, "app/tasks/example.py")
    assert len(violations) == 1
    assert "line 4" in violations[0]
    assert "SessionLocal" in violations[0]


def test_flags_with_statement_session_local():
    src = "from app.db import SessionLocal\n\ndef run():\n    with SessionLocal() as db:\n        return db\n"
    violations = guard.check_source(src, "app/tasks/example.py")
    assert len(violations) == 1
    assert "line 4" in violations[0]


def test_allow_marker_suppresses_violation():
    src = (
        "from app.db import SessionLocal\n\n"
        "def run():\n"
        "    db = SessionLocal()  # session-context: allow\n"
        "    return db\n"
    )
    assert guard.check_source(src, "app/tasks/example.py") == []


def test_canonical_helpers_are_clean():
    src = (
        "from app.db.session_context import session_for_org\n\n"
        "def run(org_id):\n"
        "    with session_for_org(org_id) as db:\n"
        "        db.commit()\n"
    )
    assert guard.check_source(src, "app/tasks/example.py") == []


def test_attribute_form_is_flagged():
    src = "import app.db as d\n\ndef run():\n    return d.SessionLocal()\n"
    violations = guard.check_source(src, "app/tasks/example.py")
    assert len(violations) == 1


def test_syntax_error_does_not_raise():
    assert guard.check_source("def broken(:\n", "app/tasks/example.py") == []


def test_real_tasks_tree_is_clean():
    """Regression guard: the live app/tasks/ tree must stay migrated."""
    tasks_dir = _SCRIPT.parent.parent / "app" / "tasks"
    offenders: list[str] = []
    for path in tasks_dir.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        if guard.check_file(str(path)):
            offenders.append(str(path))
    assert offenders == [], f"raw SessionLocal() found in tasks: {offenders}"


@pytest.mark.parametrize("exit_code,target", [(0, "app/tasks")])
def test_main_clean_exit(exit_code, target, capsys):
    root = _SCRIPT.parent.parent
    assert guard.main(["check_session_context.py", str(root / target)]) == exit_code


# --------------------------------------------------------------------------
# scripts/ coverage and the legacy ratchet
#
# `scripts/` is where the unscoped sessions actually accumulated: it was
# carved out of semgrep, pre-commit and ruff on the premise that it holds
# "one-off maintenance scripts", and 100 session-opening files grew behind
# that exemption. The guard now covers it, with a ratchet for the backlog.
# --------------------------------------------------------------------------

_ROOT = _SCRIPT.parent.parent


def test_scripts_and_tools_are_scan_roots():
    """The docstring has always claimed 'any other non-request entry point';
    these are the roots that make the claim true."""
    assert set(guard.DEFAULT_SCAN_ROOTS) >= {"app/tasks", "app/tools", "scripts"}


def test_archive_is_out_of_scan_scope(tmp_path):
    """An archived one-off has already run — it is provenance, not an entry
    point, and moving a script there is how its ratchet entry is retired."""
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive" / "old.py").write_text("db = SessionLocal()\n")
    (tmp_path / "live.py").write_text("db = SessionLocal()\n")
    found = {Path(p).name for p in guard._iter_python_files(str(tmp_path))}
    assert found == {"live.py"}


def test_ratchet_entries_match_reality():
    """Every listed file must exist and hold exactly the recorded number of
    raw sessions. This is what stops the list drifting into fiction."""
    ratchet = guard.load_legacy_ratchet()
    assert ratchet, "the ratchet is empty — did the file move?"
    wrong: list[str] = []
    for relative, recorded in ratchet.items():
        path = _ROOT / relative
        if not path.exists():
            wrong.append(f"{relative}: listed but missing")
            continue
        actual = len(guard.check_file(str(path)))
        if actual != recorded:
            wrong.append(f"{relative}: recorded {recorded}, found {actual}")
    assert wrong == [], "ratchet is out of date:\n  " + "\n  ".join(wrong)


def test_ratchet_rejects_a_malformed_line(tmp_path):
    bad = tmp_path / "legacy.txt"
    bad.write_text("# comment\nscripts/no_count_here.py\n")
    with pytest.raises(ValueError, match="malformed"):
        guard.load_legacy_ratchet(bad)


def test_ratchet_ignores_comments_and_blanks(tmp_path):
    good = tmp_path / "legacy.txt"
    good.write_text("# header\n\nscripts/a.py 2\nscripts/b.py 1\n")
    assert guard.load_legacy_ratchet(good) == {"scripts/a.py": 2, "scripts/b.py": 1}


def test_scripts_tree_is_clean_against_the_ratchet():
    """The live invariant: no unratcheted violation anywhere under scripts/."""
    assert guard.main(["check_session_context.py", str(_ROOT / "scripts")]) == 0


def test_scripts_tree_would_fail_without_the_ratchet(monkeypatch, tmp_path):
    """Sensitivity proof. The previous test must pass because the backlog is
    recorded, NOT because the guard stopped looking at scripts/. Point it at
    an empty ratchet and the same tree must fail."""
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing grandfathered\n")
    monkeypatch.setattr(guard, "LEGACY_ALLOWLIST_PATH", empty)
    assert guard.main(["check_session_context.py", str(_ROOT / "scripts")]) == 1


def test_a_scoped_scripts_file_needs_no_ratchet_entry(tmp_path, monkeypatch):
    """The exit the backlog is supposed to take: use the canonical helper and
    the file simply passes, with nothing added to the list."""
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    monkeypatch.setattr(guard, "LEGACY_ALLOWLIST_PATH", empty)
    (tmp_path / "job.py").write_text(
        "from app.db.session_context import cross_org_session\n"
        "with cross_org_session() as db:\n"
        "    db.commit()\n"
    )
    assert guard.main(["check_session_context.py", str(tmp_path)]) == 0
