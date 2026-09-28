"""Only ``app/services/storage.py`` may reach a ``dotmac_files`` delete primitive.

``dotmac_files.delete_orphans`` performs an irreversible provider delete
outside a database transaction; ``delete_object`` and ``finalize_purge`` are
the paired primitives for the ordinary managed-file deletion lifecycle. ADR-
0013's external-effect owner rule requires exactly one module to reach any of
these — ``app/services/storage.py``, which additionally asserts the live
provider identity before calling ``delete_orphans`` (see
``delete_reviewed_file_orphan``). A second caller could delete storage with no
recheck, no digest authorization, and no provider-identity assertion.

SCOPE: scans ``app/``, ``scripts/`` and ``tools/`` — the same roots
``test_external_effect_callers.py`` uses, for the same reason (``alembic/``
migrations and ``tests/`` fixtures never reach this surface in production).

WHAT IS FLAGGED: a ``Call`` whose callee resolves — through a plain, aliased,
or attribute-form import of ``dotmac_files`` — to one of ``delete_orphans``,
``delete_object``, or ``finalize_purge``. A same-named LOCAL function/method
that was never imported from ``dotmac_files`` is deliberately not a hit (the
near-miss proof below).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "app",
    REPO_ROOT / "scripts",
    REPO_ROOT / "tools",
)
OWNER = "app/services/storage.py"
GUARDED_NAMES = frozenset({"delete_orphans", "delete_object", "finalize_purge"})


def find_dotmac_files_delete_calls(tree: ast.AST) -> set[str]:
    """Return the guarded names this module reaches, resolved through imports."""
    module_aliases: set[str] = set()
    direct_names: dict[str, str] = {}  # local name -> real dotmac_files name

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "dotmac_files":
                    module_aliases.add(alias.asname or "dotmac_files")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "dotmac_files":
                for alias in node.names:
                    if alias.name in GUARDED_NAMES:
                        direct_names[alias.asname or alias.name] = alias.name

    hits: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in GUARDED_NAMES
            and isinstance(func.value, ast.Name)
            and func.value.id in module_aliases
        ):
            hits.add(func.attr)
        elif isinstance(func, ast.Name) and func.id in direct_names:
            hits.add(direct_names[func.id])
    return hits


def scan_repo(roots: tuple[Path, ...] = SCANNED_ROOTS) -> dict[str, set[str]]:
    """``path`` -> guarded names reached, for every module with a hit."""
    hits: dict[str, set[str]] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            found = find_dotmac_files_delete_calls(tree)
            if found:
                hits[rel] = found
    return hits


def _tree(source: str) -> ast.AST:
    return ast.parse(source)


def test_only_storage_py_reaches_a_dotmac_files_delete_primitive() -> None:
    hits = scan_repo()
    assert set(hits) == {OWNER}, (
        "These modules reach a dotmac_files delete primitive directly; only "
        f"{OWNER} may — route through its own functions instead: {hits}"
    )


def test_storage_py_actually_reaches_delete_orphans() -> None:
    """A guard covering zero real call sites proves nothing about itself."""
    tree = ast.parse((REPO_ROOT / OWNER).read_text(encoding="utf-8"), filename=OWNER)
    assert find_dotmac_files_delete_calls(tree) == {"delete_orphans"}


def test_sensitivity_a_planted_direct_call_is_caught() -> None:
    planted = _tree(
        """
        from dotmac_files import delete_orphans

        def cleanup(provider, scope, keys):
            delete_orphans(provider, scope=scope, keys=keys)
        """
    )
    assert find_dotmac_files_delete_calls(planted) == {"delete_orphans"}


def test_sensitivity_a_planted_attribute_form_call_is_caught() -> None:
    planted = _tree(
        """
        import dotmac_files as df

        def purge(provider, target, now):
            df.finalize_purge(provider, target=target, now=now)
        """
    )
    assert find_dotmac_files_delete_calls(planted) == {"finalize_purge"}


def test_sensitivity_an_aliased_import_call_is_caught() -> None:
    planted = _tree(
        """
        from dotmac_files import delete_object as remove_object

        def purge(provider, target):
            remove_object(provider, target=target)
        """
    )
    assert find_dotmac_files_delete_calls(planted) == {"delete_object"}


def test_sensitivity_a_local_lookalike_is_not_a_hit() -> None:
    """A locally defined function sharing a guarded name is not imported."""
    near_miss = _tree(
        """
        def delete_orphans(provider, scope, keys):
            '''Not the dotmac_files primitive -- never imported from it.'''
            return None

        def cleanup(provider, scope, keys):
            delete_orphans(provider, scope, keys)
        """
    )
    assert find_dotmac_files_delete_calls(near_miss) == set()


def test_sensitivity_a_bare_reference_with_no_call_is_not_a_hit() -> None:
    near_miss = _tree(
        """
        from dotmac_files import delete_orphans

        CALLBACK = delete_orphans
        """
    )
    assert find_dotmac_files_delete_calls(near_miss) == set()
