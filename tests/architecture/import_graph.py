"""AST-based production import-reachability graph for ERP's own `app/` tree.

Used to derive `runtime_consumption` mechanically: whether a distribution's
import package is reached by a real `Import`/`ImportFrom` AST node, walking
the actual import graph from ERP's declared production entry points --
never by a substring/text match on file contents. "A substring is not a
structure" -- this module exists specifically because the prior
runtime_consumption tests asserted an intermediary needle (one file imports
another file) and never reached the real `from dotmac_files import ...`
statement, so deleting the real import left them passing.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _module_name_for_path(app_parent: Path, file_path: Path) -> str:
    """`app/foo/bar.py` -> `app.foo.bar`; `app/foo/__init__.py` -> `app.foo`."""
    rel = file_path.relative_to(app_parent)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _build_module_index(app_root: Path) -> dict[str, Path]:
    app_parent = app_root.parent
    index: dict[str, Path] = {}
    for py_file in app_root.rglob("*.py"):
        index[_module_name_for_path(app_parent, py_file)] = py_file
    return index


def _resolve_relative(
    current_module: str, node: ast.ImportFrom, current_is_package: bool
) -> str | None:
    """Resolve a relative `from . import x` / `from ..y import z` to an
    absolute dotted module name, given the importing module's own dotted
    name and whether it is itself a package (`__init__.py`)."""
    parts = current_module.split(".")
    base_len = len(parts) if current_is_package else len(parts) - 1
    up = node.level - 1
    truncated_len = base_len - up
    if truncated_len < 0:
        return None
    anchor = parts[:truncated_len]
    if node.module:
        anchor = anchor + node.module.split(".")
    if not anchor:
        return None
    return ".".join(anchor)


def discover_autodiscover_task_roots(celery_app_path: Path) -> tuple[str, ...]:
    """AST-parse `celery_app.py`'s `...autodiscover_tasks([...])` call and
    return its string-literal list arguments as additional BFS roots -- the
    one dynamic-import special case Celery's own autodiscovery requires,
    resolved structurally from the real Call node, not by text-matching the
    string "autodiscover_tasks" anywhere in the file."""
    tree = ast.parse(celery_app_path.read_text())
    roots: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "autodiscover_tasks"
        ):
            for arg in node.args:
                if isinstance(arg, ast.List):
                    for elt in arg.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            roots.append(elt.value)
    return tuple(roots)


def build_reachable_import_graph(
    app_root: Path,
    root_modules: tuple[str, ...],
) -> tuple[set[str], dict[str, str]]:
    """BFS from `root_modules` over ERP's own `app/` tree, following real
    `Import`/`ImportFrom` AST nodes (including relative imports). Returns
    `(external_top_level_import_names_reached, {external_name: an_example_reaching_module})`.
    Local (`app.*`) imports are followed further; anything else is recorded
    as an external leaf -- this repository does not vendor third-party
    source, so there is nothing further under `app/` to walk from there."""
    index = _build_module_index(app_root)
    visited: set[str] = set()
    queue = list(dict.fromkeys(m for m in root_modules if m))
    external: set[str] = set()
    external_source: dict[str, str] = {}

    while queue:
        module = queue.pop()
        if module in visited or module not in index:
            continue
        visited.add(module)
        file_path = index[module]
        is_package = file_path.name == "__init__.py"
        try:
            tree = ast.parse(file_path.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top == "app":
                        if alias.name not in visited:
                            queue.append(alias.name)
                    else:
                        external.add(top)
                        external_source.setdefault(top, module)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    target = _resolve_relative(module, node, is_package)
                    if (
                        target
                        and target.split(".")[0] == "app"
                        and target not in visited
                    ):
                        queue.append(target)
                    continue
                if node.module is None:
                    continue
                top = node.module.split(".")[0]
                if top == "app":
                    if node.module not in visited:
                        queue.append(node.module)
                else:
                    external.add(top)
                    external_source.setdefault(top, module)

    return external, external_source


def module_name_from_repo_root(repo_root: Path, file_path: Path) -> str:
    """`<repo_root>/app/foo/bar.py` -> `app.foo.bar`;
    `<repo_root>/app/foo/__init__.py` -> `app.foo`. Public so callers outside
    this module (e.g. a search over `tests/`, `scripts/`, not just `app/`)
    can resolve a file's own dotted name the identical way the reachability
    graph above does."""
    return _module_name_for_path(repo_root, file_path)


def find_module_importers(
    search_files: list[Path], repo_root: Path, target_module: str
) -> set[str]:
    """Return the (repo-root-relative, POSIX) paths of every file in
    `search_files` that contains a real `Import`/`ImportFrom` AST node
    resolving to `target_module` -- e.g. `import app.product_assembly`,
    `from app.product_assembly import X`, `from app import product_assembly`,
    or the relative-import equivalent of any of those. Never a substring/text
    match: a docstring or comment merely naming `target_module` does not
    count, and a constructed/dynamic import this cannot statically resolve
    is not counted either (an AST classifier can only ever refuse to count
    what it cannot prove, never guess yes)."""
    target_parent, _, target_leaf = target_module.rpartition(".")
    importers: set[str] = set()

    def matches_target(resolved_module: str | None, names: list[ast.alias]) -> bool:
        if resolved_module == target_module:
            return True
        return resolved_module == target_parent and any(
            alias.name == target_leaf for alias in names
        )

    for file_path in search_files:
        own_module = _module_name_for_path(repo_root, file_path)
        is_package = file_path.name == "__init__.py"
        try:
            tree = ast.parse(file_path.read_text())
        except SyntaxError:
            continue

        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == target_module for alias in node.names):
                    found = True
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    resolved_module = _resolve_relative(own_module, node, is_package)
                    found = matches_target(resolved_module, node.names)
                    continue
                found = matches_target(node.module, node.names)
            if found:
                break

        if found:
            importers.add(file_path.relative_to(repo_root).as_posix())

    return importers
