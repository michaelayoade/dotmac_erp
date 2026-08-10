"""Reading a source file the way a regression guard should read it.

Several conversions of `scripts/` one-offs into services carry AST guards
asserting that a defect is gone — no hardcoded organization, no raw SQL, no
`SessionLocal()`. Those guards kept tripping on the *documentation*: each
converted script's docstring quotes the defect it used to have, precisely so
the next reader understands what changed.

A plain substring search over the file therefore fails forever, and the
tempting fix — deleting the explanation — makes the codebase worse to teach
the test a lesson.

`executable_strings` returns the string literals that are actually code,
excluding module/class/function docstrings, so a guard can assert on what the
file *does* rather than on what it *says about itself*.
"""

from __future__ import annotations

import ast
from pathlib import Path

_DOCSTRING_OWNERS = (
    ast.Module,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_OWNERS):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def executable_strings(path: Path) -> list[str]:
    """Every string literal in the file except docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_node_ids(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
    ]


def mentions_in_code(path: Path, needle: str) -> list[str]:
    """Executable string literals containing `needle`. Empty means "only the
    documentation mentions it", which is the passing state for these guards."""
    return [s for s in executable_strings(path) if needle in s]


def module_level_assignments(path: Path) -> set[str]:
    """Names assigned at module level — how a hardcoded `ORG_ID` is detected."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def calls_named(path: Path, func_name: str) -> list[int]:
    """Line numbers calling `func_name` — e.g. `text` for raw SQL."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == func_name
    ]
