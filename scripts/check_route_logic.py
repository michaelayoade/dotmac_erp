#!/usr/bin/env python3
"""
Post-edit hook: detect business logic leaking into route files.

Checks app/api/ and app/web/ files for anti-patterns:
- select() / insert() as top-level SQLAlchemy calls in route functions
- db.query() / db.add() / db.flush() / db.commit() / db.scalars() etc.
- Direct model operations that belong in services

Exit code 0 = clean, exit code 1 = violations found (printed to stderr).
"""

from __future__ import annotations

import ast
import sys

ROUTE_DECORATORS = {"get", "post", "put", "patch", "delete", "head", "options"}

# Top-level function calls that indicate SQLAlchemy core usage
# These are called as standalone functions: select(Model), insert(Model), etc.
SQLALCHEMY_FUNCTIONS = {"select", "insert", "update", "delete"}

# Method calls that are only violations when called on db/session objects
# e.g. db.add(), db.flush(), db.scalars() — but NOT context.update()
DB_SESSION_METHODS = {
    "query",
    "add",
    "add_all",
    "flush",
    "commit",
    "rollback",
    "execute",
    "scalar",
    "scalars",
    "merge",
    "refresh",
    "expire",
    "expunge",
}

# Variable names that indicate a DB session
DB_NAMES = {"db", "session", "self.db", "self.session"}


def is_route_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function has a router decorator (@router.get, etc.)."""
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if dec.func.attr in ROUTE_DECORATORS:
                return True
        elif isinstance(dec, ast.Attribute) and dec.attr in ROUTE_DECORATORS:
            return True
    return False


def _get_receiver_name(node: ast.Attribute) -> str:
    """Get the name of the object a method is called on. e.g. 'db' from db.add()."""
    if isinstance(node.value, ast.Name):
        return node.value.id
    if isinstance(node.value, ast.Attribute):
        # self.db.add() → 'self.db'
        if isinstance(node.value.value, ast.Name):
            return f"{node.value.value.id}.{node.value.attr}"
    return ""


def check_for_db_calls(node: ast.AST) -> list[str]:
    """Find direct DB operations inside a route function body."""
    violations: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue

        # Case 1: Top-level SQLAlchemy function call — select(Model), insert(Model)
        if isinstance(child.func, ast.Name):
            if child.func.id in SQLALCHEMY_FUNCTIONS:
                violations.append(
                    f"line {child.lineno}: `{child.func.id}()` — move to service layer"
                )

        # Case 2: Method call on a db/session object — db.add(), db.scalars()
        elif isinstance(child.func, ast.Attribute):
            method_name = child.func.attr
            receiver = _get_receiver_name(child.func)

            if method_name in DB_SESSION_METHODS and receiver in DB_NAMES:
                violations.append(
                    f"line {child.lineno}: `{receiver}.{method_name}()` — move to service layer"
                )

    return violations


def check_file(filepath: str) -> list[str]:
    """Check a single file for route logic violations."""
    try:
        with open(filepath) as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if is_route_function(node):
                for stmt in node.body:
                    hits = check_for_db_calls(stmt)
                    for hit in hits:
                        violations.append(f"  {node.name}() {hit}")

    return violations


def main() -> int:
    if len(sys.argv) < 2:
        return 0

    filepath = sys.argv[1]

    # Only check route files
    if not filepath.endswith(".py"):
        return 0
    if "/app/api/" not in filepath and "/app/web/" not in filepath:
        return 0

    violations = check_file(filepath)
    if violations:
        print(f"ROUTE LOGIC VIOLATION in {filepath}:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "  -> Move this logic to a service. Routes must be thin wrappers.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
