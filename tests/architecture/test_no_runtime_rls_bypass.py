"""The user-settable PostgreSQL RLS bypass has no runtime writer.

Historical Alembic revisions still describe the schema they originally
installed and are deliberately outside this guard.  Runtime entry-point
families are not: application code, workers/tasks, CLI/maintenance scripts,
cron SQL, and archived executable scripts all live under ``app/`` or
``scripts/`` and are scanned together.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOTS = ("app", "scripts")
RUNTIME_SUFFIXES = frozenset({".bash", ".psql", ".py", ".sh", ".sql", ".zsh"})

_FORBIDDEN_SYMBOLS = frozenset(
    {
        "bypass_rls",
        "bypass_rls_sync",
        "disable_rls_bypass",
        "disable_rls_bypass_sync",
        "enable_rls_bypass",
        "enable_rls_bypass_on_connection",
        "enable_rls_bypass_sync",
    }
)
_FORBIDDEN_SQL = re.compile(
    r"(?:"
    r"(?:set(?:\s+local)?|reset|show)\s+['\"]?app\.bypass_rls['\"]?"
    r"|(?:pg_catalog\.)?(?:set_config|current_setting)\s*\(\s*"
    r"['\"]app\.bypass_rls['\"]"
    r")",
    re.IGNORECASE,
)


def _python_symbol_violations(path: Path, source: str) -> list[str]:
    tree = ast.parse(source, filename=str(path))
    violations: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_SYMBOLS:
            violations.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_SYMBOLS:
            violations.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name in _FORBIDDEN_SYMBOLS:
                    violations.add(alias.name)
    return sorted(violations)


def find_runtime_bypass_violations(root: Path = REPO_ROOT) -> list[str]:
    """Return exact runtime files that can assert the retired GUC bypass."""
    violations: list[str] = []
    for relative_root in RUNTIME_ROOTS:
        source_root = root / relative_root
        if not source_root.exists():
            continue
        for path in sorted(source_root.rglob("*")):
            if not path.is_file() or path.suffix not in RUNTIME_SUFFIXES:
                continue
            source = path.read_text(encoding="utf-8")
            reasons: list[str] = []
            if path.suffix == ".py":
                reasons.extend(_python_symbol_violations(path, source))
            if _FORBIDDEN_SQL.search(source):
                reasons.append("app.bypass_rls SQL")
            if reasons:
                rel = path.relative_to(root).as_posix()
                violations.append(f"{rel}: {', '.join(sorted(set(reasons)))}")
    return violations


def test_runtime_has_no_user_settable_rls_bypass() -> None:
    assert find_runtime_bypass_violations() == []


def test_detector_catches_python_and_raw_sql_entry_points(tmp_path: Path) -> None:
    """Sensitivity proof: both helper and raw-SQL escape paths are detected."""
    app = tmp_path / "app"
    scripts = tmp_path / "scripts" / "cron"
    app.mkdir()
    scripts.mkdir(parents=True)
    (app / "worker.py").write_text(
        "from app.rls import enable_rls_bypass_sync\nenable_rls_bypass_sync(session)\n",
        encoding="utf-8",
    )
    (scripts / "audit.sql").write_text(
        "SELECT pg_catalog.set_config ( 'app.bypass_rls', 'true', true);\n",
        encoding="utf-8",
    )

    assert find_runtime_bypass_violations(tmp_path) == [
        "app/worker.py: enable_rls_bypass_sync",
        "scripts/cron/audit.sql: app.bypass_rls SQL",
    ]
