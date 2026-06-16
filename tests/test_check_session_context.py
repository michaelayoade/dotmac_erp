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
