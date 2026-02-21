#!/usr/bin/env python3
"""
PostToolUse hook: enforce Python backend code quality standards.

Checks for anti-patterns documented in CLAUDE.md and .claude/rules/:

  SQLAlchemy 2.0    — no db.query(), use select() + db.scalars()
  Pydantic v2       — no orm_mode, no @validator/@root_validator
  Modern typing     — no Optional[], List[], Dict[], Tuple[], Set[]
  Service layer     — no db.commit() in services, no HTTPException in services
  Route handlers    — no async def on routes
  Error handling    — no bare except:
  Boilerplate       — from __future__ import annotations, logger in services

Usage:  python3 check-python-style.py <file_path>
Exit 0 = clean, exit 2 = violations found (shown to Claude).
"""

from __future__ import annotations

import ast
import re
import sys

# ── Constants ───────────────────────────────────────────────────

ROUTE_DECORATORS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)

# typing imports that have modern replacements with `from __future__ import annotations`
LEGACY_TYPING_NAMES = frozenset(
    {
        "Optional",
        "List",
        "Dict",
        "Tuple",
        "Set",
        "FrozenSet",
        "Type",
        "Union",
    }
)

# Patterns: annotation usage of legacy typing (e.g. `Optional[str]`, `List[int]`)
LEGACY_ANNOTATION_RE = re.compile(r"\b(" + "|".join(LEGACY_TYPING_NAMES) + r")\[")

# Methods that indicate db session usage
DB_NAMES = frozenset({"db", "session", "self.db", "self.session"})


# ── AST Helpers ─────────────────────────────────────────────────


def _is_route_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function has a router decorator (@router.get, etc.)."""
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if dec.func.attr in ROUTE_DECORATORS:
                return True
        elif isinstance(dec, ast.Attribute) and dec.attr in ROUTE_DECORATORS:
            return True
    return False


def _get_receiver(node: ast.Attribute) -> str:
    """Get 'db' from db.commit(), 'self.db' from self.db.commit()."""
    if isinstance(node.value, ast.Name):
        return node.value.id
    if isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name):
        return f"{node.value.value.id}.{node.value.attr}"
    return ""


# ── Individual Checks ───────────────────────────────────────────


def check_future_annotations(filepath: str, source: str) -> list[str]:
    """Files in app/ must have `from __future__ import annotations`."""
    if "/app/" not in filepath:
        return []
    # Skip __init__.py that are often empty
    if filepath.endswith("__init__.py") and len(source.strip()) < 50:
        return []
    if "from __future__ import annotations" in source:
        return []
    return ["line 1: missing `from __future__ import annotations`"]


def check_db_query(tree: ast.AST) -> list[str]:
    """Flag db.query() — SQLAlchemy 1.x pattern."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "query" and _get_receiver(node.func) in DB_NAMES:
                violations.append(
                    f"line {node.lineno}: `{_get_receiver(node.func)}.query()` "
                    f"— use `select()` + `db.scalars()` (SQLAlchemy 2.0)"
                )
    return violations


def check_pydantic_v1_config(source: str, lines: list[str]) -> list[str]:
    """Flag orm_mode and old Config class patterns."""
    violations: list[str] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if "orm_mode" in stripped and not stripped.startswith("#"):
            violations.append(
                f"line {i}: `orm_mode` — use `model_config = ConfigDict(from_attributes=True)` (Pydantic v2)"
            )
    return violations


def check_pydantic_v1_decorators(tree: ast.AST) -> list[str]:
    """Flag @validator and @root_validator — Pydantic v1 decorators."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            name = None
            if isinstance(dec, ast.Name):
                name = dec.id
            elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
                name = dec.func.id
            if name == "validator":
                violations.append(
                    f"line {node.lineno}: `@validator` — use `@field_validator` (Pydantic v2)"
                )
            elif name == "root_validator":
                violations.append(
                    f"line {node.lineno}: `@root_validator` — use `@model_validator` (Pydantic v2)"
                )
    return violations


def check_legacy_typing_imports(lines: list[str]) -> list[str]:
    """Flag `from typing import Optional, List, Dict, ...`."""
    violations: list[str] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped.startswith("from typing import"):
            continue
        # Parse the imported names
        import_part = stripped.split("import", 1)[1]
        # Handle parenthesized imports
        import_part = import_part.replace("(", "").replace(")", "")
        names = [n.strip().split(" as ")[0] for n in import_part.split(",")]
        legacy = [n for n in names if n in LEGACY_TYPING_NAMES]
        if legacy:
            replacements = {
                "Optional": "X | None",
                "List": "list[X]",
                "Dict": "dict[X, Y]",
                "Tuple": "tuple[X, ...]",
                "Set": "set[X]",
                "FrozenSet": "frozenset[X]",
                "Type": "type[X]",
                "Union": "X | Y",
            }
            hints = ", ".join(f"{n} → {replacements.get(n, 'builtin')}" for n in legacy)
            violations.append(
                f"line {i}: legacy typing imports: {', '.join(legacy)} — use modern syntax ({hints})"
            )
    return violations


def check_legacy_typing_usage(lines: list[str]) -> list[str]:
    """Flag Optional[X], List[X], Dict[X,Y] etc. in annotations."""
    violations: list[str] = []
    seen_lines: set[int] = set()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Skip comments and imports (imports are caught separately)
        if (
            stripped.startswith("#")
            or stripped.startswith("from ")
            or stripped.startswith("import ")
        ):
            continue
        for match in LEGACY_ANNOTATION_RE.finditer(line):
            if i not in seen_lines:
                seen_lines.add(i)
                name = match.group(1)
                violations.append(
                    f"line {i}: `{name}[...]` — use modern syntax with `from __future__ import annotations`"
                )
    return violations


def check_bare_except(tree: ast.AST) -> list[str]:
    """Flag bare `except:` without an exception type."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            violations.append(
                f"line {node.lineno}: bare `except:` — catch a specific exception (e.g. `except Exception as e:`)"
            )
    return violations


def check_async_routes(tree: ast.AST) -> list[str]:
    """Flag async def on route handlers — SQLAlchemy sessions are sync."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and _is_route_function(node):
            violations.append(
                f"line {node.lineno}: `async def {node.name}()` — route handlers must be sync `def` (SQLAlchemy sessions are sync)"
            )
    return violations


def check_service_commit(filepath: str, tree: ast.AST) -> list[str]:
    """Flag db.commit() in service files — services should use db.flush()."""
    if "/app/services/" not in filepath:
        return []
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "commit" and _get_receiver(node.func) in DB_NAMES:
                violations.append(
                    f"line {node.lineno}: `{_get_receiver(node.func)}.commit()` in service — use `db.flush()`, caller controls transaction"
                )
    return violations


def check_service_http_exception(filepath: str, tree: ast.AST) -> list[str]:
    """Flag `raise HTTPException` in service files — use domain exceptions."""
    if "/app/services/" not in filepath:
        return []
    # Allow web services — they bridge HTTP and services
    if "/web/" in filepath or filepath.endswith("/web.py"):
        return []
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        exc = node.exc
        # raise HTTPException(...)
        if isinstance(exc, ast.Call):
            if isinstance(exc.func, ast.Name) and exc.func.id == "HTTPException":
                violations.append(
                    f"line {node.lineno}: `raise HTTPException` in service — raise domain exceptions (ValueError, RuntimeError), routes translate to HTTP"
                )
            elif (
                isinstance(exc.func, ast.Attribute) and exc.func.attr == "HTTPException"
            ):
                violations.append(
                    f"line {node.lineno}: `raise HTTPException` in service — raise domain exceptions, routes translate to HTTP"
                )
    return violations


def check_service_logger(filepath: str, source: str) -> list[str]:
    """Service files must have `logger = logging.getLogger(__name__)`."""
    if "/app/services/" not in filepath:
        return []
    # Skip __init__.py, conftest, small files
    basename = filepath.rsplit("/", 1)[-1]
    if basename in ("__init__.py", "conftest.py"):
        return []
    if len(source.strip()) < 100:
        return []
    if "logging.getLogger(__name__)" in source:
        return []
    return [
        "missing `logger = logging.getLogger(__name__)` — every service file needs a logger"
    ]


# ── Main ────────────────────────────────────────────────────────


def check_file(filepath: str) -> dict[str, list[str]]:
    """Run all checks on a file, return violations grouped by category."""
    try:
        with open(filepath) as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return {}

    lines = source.splitlines()

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return {}

    is_route_file = "/app/api/" in filepath or "/app/web/" in filepath
    is_schema_file = "/app/schemas/" in filepath

    groups: dict[str, list[str]] = {}

    # Boilerplate
    v = check_future_annotations(filepath, source)
    if v:
        groups["Boilerplate"] = v

    # SQLAlchemy 2.0
    v = check_db_query(tree)
    if v:
        groups["SQLAlchemy 2.0"] = v

    # Pydantic v2
    v = check_pydantic_v1_decorators(tree)
    if is_schema_file:
        v += check_pydantic_v1_config(source, lines)
    if v:
        groups["Pydantic v2"] = v

    # Modern typing
    v = check_legacy_typing_imports(lines) + check_legacy_typing_usage(lines)
    if v:
        groups["Modern typing"] = v

    # Error handling
    v = check_bare_except(tree)
    if v:
        groups["Error handling"] = v

    # Route patterns
    if is_route_file:
        v = check_async_routes(tree)
        if v:
            groups["Route handlers"] = v

    # Service layer
    v = check_service_commit(filepath, tree) + check_service_http_exception(
        filepath, tree
    )
    v2 = check_service_logger(filepath, source)
    v += v2
    if v:
        groups["Service layer"] = v

    return groups


def main() -> int:
    if len(sys.argv) < 2:
        return 0

    filepath = sys.argv[1]

    if not filepath or not filepath.endswith(".py"):
        return 0

    # Only check app/ source files
    if "/app/" not in filepath:
        return 0

    # Skip test files and migrations
    if "/tests/" in filepath or "/alembic/" in filepath:
        return 0

    groups = check_file(filepath)
    if not groups:
        return 0

    total = sum(len(v) for v in groups.values())
    print(
        f"PYTHON STYLE: {total} issue{'s' if total != 1 else ''} in {filepath}:",
        file=sys.stderr,
    )
    for category, violations in groups.items():
        print(f"  {category}:", file=sys.stderr)
        for v in violations:
            print(f"    {v}", file=sys.stderr)
    print(
        "  -> Fix these to match project standards (see CLAUDE.md).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
