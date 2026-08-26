"""Authorization declarations stay independent of runtime and persistence."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.authz import profile


DECLARATION_ROOT = Path("app/authz")
FORBIDDEN_IMPORTS = (
    "app.api",
    "app.db",
    "app.models",
    "app.services",
    "app.web",
    "fastapi",
    "sqlalchemy",
    "starlette",
)


def _imported_modules(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return (module, *(f"{module}.{alias.name}" for alias in node.names))
    return tuple(alias.name for alias in node.names)


def test_authz_declarations_import_no_runtime_or_persistence_layer() -> None:
    paths = tuple(sorted(DECLARATION_ROOT.glob("*.py")))
    assert {
        "__init__.py",
        "expense.py",
        "payment_execution.py",
        "profile.py",
    } <= {path.name for path in paths}

    violations: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ImportFrom) and node.level:
                violations.append(f"{path}:{node.lineno}: relative import")
                continue
            for imported in _imported_modules(node):
                if any(
                    imported == forbidden or imported.startswith(f"{forbidden}.")
                    for forbidden in FORBIDDEN_IMPORTS
                ):
                    violations.append(f"{path}:{node.lineno}: {imported}")

    assert violations == []


def test_authored_authorization_profile_is_internally_consistent() -> None:
    assert profile.validate_authorization_profile() == []


def test_authorization_profile_rejects_an_unknown_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        profile,
        "EXPENSE_PAYOUT_ROLE_GRANTS",
        {"expense_reimburser": ("payments:undeclared",)},
    )

    assert profile.validate_authorization_profile() == [
        "Authorization grants reference undeclared permissions: payments:undeclared"
    ]
