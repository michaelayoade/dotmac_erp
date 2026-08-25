"""Transaction-boundary contract for the canonical Sub intake services."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUB_SERVICE_ROOT = PROJECT_ROOT / "app" / "services" / "sync" / "sub"
SUB_SERVICE_FACADE = (
    PROJECT_ROOT / "app" / "services" / "sync" / "dotmac_sub_sync_service.py"
)


def test_sub_services_never_commit_or_rollback_transactions() -> None:
    """Only kernel transaction boundaries may finish a transaction/savepoint."""
    violations: list[str] = []
    paths = [*sorted(SUB_SERVICE_ROOT.glob("*.py")), SUB_SERVICE_FACADE]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if node.func.attr not in {"commit", "rollback"}:
                continue
            violations.append(
                f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}:"
                f"{ast.unparse(node.func)}"
            )

    assert violations == []
