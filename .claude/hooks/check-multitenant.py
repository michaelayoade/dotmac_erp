#!/usr/bin/env python3
"""
PostToolUse hook: warn when a service function uses select() without organization_id.

Walks the AST of service files and flags functions that contain a select() call
but never reference organization_id in the function body — a likely multi-tenancy
gap that could leak data across tenants.

Usage:  python3 check-multitenant.py <file_path>
Exit 0 = clean, exit 2 = warning (shown to Claude).
"""

from __future__ import annotations

import ast
import sys

# Functions that legitimately skip organization_id filtering
SKIP_FUNCTION_NAMES = frozenset(
    {
        # PK lookups (UUID is globally unique)
        "get_by_id",
        "get_or_404",
        "_get_or_404",
        "get_by_pk",
        # Lifecycle
        "__init__",
        "__repr__",
        "__str__",
        # Migration / admin
        "upgrade",
        "downgrade",
    }
)

# Prefixes for function names to skip
SKIP_FUNCTION_PREFIXES = (
    "test_",
    "_test_",
    "_build_",  # query builders often compose into org-filtered parents
)


def _has_select_call(node: ast.AST) -> bool:
    """Check if an AST node contains a standalone select() call."""
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        # select(Model) — top-level function call
        if isinstance(child.func, ast.Name) and child.func.id == "select":
            return True
    return False


def _has_org_id_ref(node: ast.AST) -> bool:
    """Check if an AST node references organization_id anywhere."""
    for child in ast.walk(node):
        # Attribute: Model.organization_id, self.organization_id
        if isinstance(child, ast.Attribute) and child.attr == "organization_id":
            return True
        # Variable name: organization_id
        if isinstance(child, ast.Name) and child.id == "organization_id":
            return True
        # Keyword arg: organization_id=value
        if isinstance(child, ast.keyword) and child.arg == "organization_id":
            return True
        # String literal: "organization_id" (e.g. in filter dicts)
        if isinstance(child, ast.Constant) and child.value == "organization_id":
            return True
        # Parameter name in function signature: org_id is a common alias
        if isinstance(child, ast.arg) and child.arg in (
            "organization_id",
            "org_id",
        ):
            return True
    return False


def _references_org_param(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function accepts org_id/organization_id as a parameter."""
    for arg in func_node.args.args + func_node.args.kwonlyargs:
        if arg.arg in ("organization_id", "org_id"):
            return True
    return False


def check_file(filepath: str) -> list[str]:
    """Find functions with select() but no organization_id reference."""
    try:
        with open(filepath) as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    warnings: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Skip known-safe function names
        if node.name in SKIP_FUNCTION_NAMES:
            continue
        if any(node.name.startswith(p) for p in SKIP_FUNCTION_PREFIXES):
            continue

        # Only flag if function uses select() AND doesn't reference org_id
        if _has_select_call(node) and not _has_org_id_ref(node):
            # Extra check: if the function receives org_id as param, it's fine
            if _references_org_param(node):
                continue
            warnings.append(
                f"  {node.name}() line {node.lineno}: "
                f"select() without organization_id — potential multi-tenancy gap"
            )

    return warnings


def main() -> int:
    if len(sys.argv) < 2:
        return 0

    filepath = sys.argv[1]

    if not filepath or not filepath.endswith(".py"):
        return 0

    # Only check service files
    if "/app/services/" not in filepath:
        return 0

    # Skip web services (they delegate to business services for filtering)
    if "/web/" in filepath or filepath.endswith("/web.py"):
        return 0

    # Skip __init__.py, conftest, test files
    basename = filepath.rsplit("/", 1)[-1]
    if basename in ("__init__.py", "conftest.py") or basename.startswith("test_"):
        return 0

    warnings = check_file(filepath)
    if warnings:
        print(f"MULTI-TENANCY WARNING in {filepath}:", file=sys.stderr)
        for w in warnings:
            print(w, file=sys.stderr)
        print(
            "  -> Verify these queries filter by organization_id for tenant isolation.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
