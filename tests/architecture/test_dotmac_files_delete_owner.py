"""Only ``app/services/storage.py`` may reach a ``dotmac_files`` delete primitive.

``dotmac_files.recheck_and_delete_orphan`` performs an irreversible provider delete
outside a database transaction; ``delete_object`` and ``finalize_purge`` are
the paired primitives for the ordinary managed-file deletion lifecycle. ADR-
0013's external-effect owner rule requires exactly one module to reach any of
these — ``app/services/storage.py``, which additionally asserts the live
provider identity before calling ``delete_orphans`` (see
``delete_reviewed_file_orphan``). A second caller could delete storage with no
recheck, no digest authorization, and no provider-identity assertion.

Four independent guards, each with its own planted sensitivity proof:

1. ``find_dotmac_files_delete_calls`` — a direct call to
   ``delete_orphans``/``delete_object``/``finalize_purge``, resolved through a
   plain, aliased, attribute-form, or SUBMODULE (``dotmac_files.physical``)
   import of ``dotmac_files``.
2. ``find_provider_delete_misuse`` — a ``.delete(`` call on a provider
   factory's result: either bound to a local name within one function's own
   body first, or CHAINED directly
   (``get_dotmac_files_provider().delete(...)``) — reached through a plain
   ``from app.services.storage import ...`` or an aliased module import
   (``import app.services.storage as s`` then ``s.get_dotmac_files_provider()``).
   A bypass that reaches the raw provider without going through
   ``delete_reviewed_file_orphan`` at all.

   **Known limitation, stated explicitly rather than silently uncovered:** a
   provider object passed in as a function PARAMETER (e.g. ``def f(provider):
   provider.delete(x)``) is not resolved to a factory call by this AST-only
   scan and is NOT caught — this guard only follows a LOCAL binding or a
   chained call whose origin is visible in the same syntax tree.
3. ``find_provider_construction`` — constructing ``DotmacFilesS3Provider(...)``
   directly, resolved through a plain, aliased, or aliased-module-attribute
   import from ``app.services.storage`` — a second way to reach the raw
   provider without the factory functions (and therefore without
   ``delete_reviewed_file_orphan``) at all.
4. ``find_write_provider_import`` — importing the WRITE factory
   ``get_dotmac_files_provider`` at all (plain or via an aliased module
   import).

   **A blanket "only storage.py may import get_dotmac_files_provider" rule
   is FALSE against this tree as of this change** — reported plainly rather
   than enforced incorrectly. ``get_dotmac_files_provider()`` is the general
   write-provider factory, and it has THREE existing legitimate non-delete
   callers that use it for upload (``.put``/``prepare_upload``), not
   deletion: ``app/services/file_upload.py``,
   ``app/api/finance/import_export.py``, ``app/tasks/imports.py``. Forcing
   every write-provider consumer through ``storage.py`` is an upload-path
   architecture change no one has decided, well outside this bug-fix's
   scope. Guard 4 is therefore a two-directional RATCHET over today's known
   set (grandfathered + ``storage.py``), catching a 4th caller, rather than
   an absolute "only storage.py" assertion — the real gap item 4 closes is
   guard 2 (a provider's ``.delete(`` called directly, bound or chained),
   which has no legitimate caller anywhere.

SCOPE: scans ``app/``, ``scripts/`` and ``tools/`` — the same roots
``test_external_effect_callers.py`` uses, for the same reason (``alembic/``
migrations and ``tests/`` fixtures never reach this surface in production).
An unaliased ``import app.services.storage`` (binding the bare name ``app``)
is also not resolved by the module-alias tracking below, to avoid treating
every ordinary ``app.`` attribute access in a file as a storage reference;
only the explicit ``import ... as <alias>`` form is tracked.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "app",
    REPO_ROOT / "scripts",
    REPO_ROOT / "tools",
)
OWNER = "app/services/storage.py"
GUARDED_NAMES = frozenset(
    {"recheck_and_delete_orphan", "delete_orphans", "delete_object", "finalize_purge"}
)
PROVIDER_FACTORY_NAMES = frozenset(
    {"get_dotmac_files_provider", "get_dotmac_files_read_provider"}
)


def _root_name(node: ast.expr) -> str | None:
    """The leftmost ``Name`` of a (possibly chained) attribute access."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _is_dotmac_files_module(module: str | None) -> bool:
    return module == "dotmac_files" or (
        module is not None and module.startswith("dotmac_files.")
    )


# ---------------------------------------------------------------------------
# Guard 1: direct calls to a guarded dotmac_files delete primitive.
# ---------------------------------------------------------------------------


def find_dotmac_files_delete_calls(tree: ast.AST) -> set[str]:
    """Return the guarded names this module reaches, resolved through imports."""
    module_aliases: set[str] = set()
    direct_names: dict[str, str] = {}  # local name -> real dotmac_files name

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "dotmac_files" or alias.name.startswith(
                    "dotmac_files."
                ):
                    module_aliases.add(alias.asname or "dotmac_files")
        elif isinstance(node, ast.ImportFrom):
            if _is_dotmac_files_module(node.module):
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
            and _root_name(func.value) in module_aliases
        ):
            hits.add(func.attr)
        elif isinstance(func, ast.Name) and func.id in direct_names:
            hits.add(direct_names[func.id])
    return hits


def _storage_module_aliases(tree: ast.AST) -> set[str]:
    """Names bound to ``app.services.storage`` via ``import ... as <alias>``.

    Deliberately excludes the unaliased ``import app.services.storage`` form
    (which binds the bare name ``app``) -- see the module docstring.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.services.storage" and alias.asname:
                    aliases.add(alias.asname)
    return aliases


def _is_factory_call(
    node: ast.expr, factory_names: dict[str, str], storage_aliases: set[str]
) -> bool:
    """Whether ``node`` is a call to a provider factory, resolved either
    through a plain/aliased ``from app.services.storage import ...`` or an
    aliased ``import app.services.storage as s`` module attribute access."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in factory_names
    if isinstance(func, ast.Attribute):
        return (
            func.attr in PROVIDER_FACTORY_NAMES
            and _root_name(func.value) in storage_aliases
        )
    return False


# ---------------------------------------------------------------------------
# Guard 2: `.delete(` on a provider factory's result, bound or chained.
# ---------------------------------------------------------------------------


def find_provider_delete_misuse(tree: ast.AST) -> bool:
    """Whether any function binds a provider factory's result and calls
    ``.delete(`` on it directly, OR calls ``.delete(`` on a CHAINED factory
    call (``get_dotmac_files_provider().delete(...)``) anywhere -- bypassing
    ``delete_reviewed_file_orphan`` (and its provider-identity assertion)
    entirely. See the module docstring for the parameter-passing limitation
    this does NOT cover.
    """
    factory_names: dict[str, str] = {}  # local import name -> real factory name
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.services.storage":
            for alias in node.names:
                if alias.name in PROVIDER_FACTORY_NAMES:
                    factory_names[alias.asname or alias.name] = alias.name
    storage_aliases = _storage_module_aliases(tree)

    if not factory_names and not storage_aliases:
        return False

    # Chained form: a `.delete(` call whose receiver is ITSELF a factory call.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "delete"
            and _is_factory_call(node.func.value, factory_names, storage_aliases)
        ):
            return True

    # Bound form: `provider = get_dotmac_files_provider(); ...; provider.delete(x)`
    for func_node in ast.walk(tree):
        if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound_names: set[str] = set()
        for node in ast.walk(func_node):
            # Stop at a nested function/lambda boundary -- "own body" only.
            if node is not func_node and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                continue
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign) and node.target is not None:
                targets = [node.target]
                value = node.value
            if value is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and _is_factory_call(
                    value, factory_names, storage_aliases
                ):
                    bound_names.add(target.id)
        if not bound_names:
            continue
        for node in ast.walk(func_node):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "delete"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in bound_names
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# Guard 3: constructing DotmacFilesS3Provider directly.
# ---------------------------------------------------------------------------


def find_provider_construction(tree: ast.AST) -> bool:
    """Whether this module constructs ``DotmacFilesS3Provider(...)`` directly
    -- a second way to reach the raw provider (and bypass
    ``delete_reviewed_file_orphan``) without going through either factory
    function at all."""
    direct_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.services.storage":
            for alias in node.names:
                if alias.name == "DotmacFilesS3Provider":
                    direct_names.add(alias.asname or alias.name)
    storage_aliases = _storage_module_aliases(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in direct_names:
            return True
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "DotmacFilesS3Provider"
            and _root_name(func.value) in storage_aliases
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Guard 4: importing the WRITE provider factory at all.
# ---------------------------------------------------------------------------


def find_write_provider_import(tree: ast.AST) -> bool:
    """Whether this module imports, or references via an aliased module
    import, ``get_dotmac_files_provider`` (the WRITE factory) at all -- never
    ``get_dotmac_files_read_provider``, which is a legitimate read-only
    import elsewhere."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.services.storage":
            if any(alias.name == "get_dotmac_files_provider" for alias in node.names):
                return True
    storage_aliases = _storage_module_aliases(tree)
    if storage_aliases:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "get_dotmac_files_provider"
                and _root_name(node.value) in storage_aliases
            ):
                return True
    return False


def _scan(roots: tuple[Path, ...] = SCANNED_ROOTS) -> list[tuple[str, ast.AST]]:
    parsed: list[tuple[str, ast.AST]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            parsed.append(
                (rel, ast.parse(path.read_text(encoding="utf-8"), filename=rel))
            )
    return parsed


def scan_repo(roots: tuple[Path, ...] = SCANNED_ROOTS) -> dict[str, set[str]]:
    """``path`` -> guarded names reached (guard 1 only), for every hit."""
    hits: dict[str, set[str]] = {}
    for rel, tree in _scan(roots):
        found = find_dotmac_files_delete_calls(tree)
        if found:
            hits[rel] = found
    return hits


def scan_provider_delete_misuse(roots: tuple[Path, ...] = SCANNED_ROOTS) -> set[str]:
    return {rel for rel, tree in _scan(roots) if find_provider_delete_misuse(tree)}


def scan_provider_construction(roots: tuple[Path, ...] = SCANNED_ROOTS) -> set[str]:
    return {rel for rel, tree in _scan(roots) if find_provider_construction(tree)}


def scan_write_provider_imports(roots: tuple[Path, ...] = SCANNED_ROOTS) -> set[str]:
    return {rel for rel, tree in _scan(roots) if find_write_provider_import(tree)}


def _tree(source: str) -> ast.AST:
    return ast.parse(textwrap.dedent(source))


# ---------------------------------------------------------------------------
# Guard 1 tests
# ---------------------------------------------------------------------------


def test_only_storage_py_reaches_a_dotmac_files_delete_primitive() -> None:
    hits = scan_repo()
    assert set(hits) == {OWNER}, (
        "These modules reach a dotmac_files delete primitive directly; only "
        f"{OWNER} may — route through its own functions instead: {hits}"
    )


def test_storage_py_actually_reaches_recheck_and_delete_orphan() -> None:
    """A guard covering zero real call sites proves nothing about itself."""
    tree = ast.parse((REPO_ROOT / OWNER).read_text(encoding="utf-8"), filename=OWNER)
    assert find_dotmac_files_delete_calls(tree) == {"recheck_and_delete_orphan"}


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


def test_sensitivity_a_submodule_from_import_is_caught() -> None:
    planted = _tree(
        """
        from dotmac_files.physical import delete_orphans

        def cleanup(provider, scope, keys):
            delete_orphans(provider, scope=scope, keys=keys)
        """
    )
    assert find_dotmac_files_delete_calls(planted) == {"delete_orphans"}


def test_sensitivity_an_aliased_submodule_import_is_caught() -> None:
    planted = _tree(
        """
        import dotmac_files.physical as p

        def cleanup(provider, scope, keys):
            p.delete_orphans(provider, scope=scope, keys=keys)
        """
    )
    assert find_dotmac_files_delete_calls(planted) == {"delete_orphans"}


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


# ---------------------------------------------------------------------------
# Guard 2 tests
# ---------------------------------------------------------------------------


def test_no_module_calls_delete_directly_on_a_provider_factorys_result() -> None:
    hits = scan_provider_delete_misuse()
    assert hits == set(), (
        "These modules call .delete( on a provider bound from a factory "
        f"function, bypassing delete_reviewed_file_orphan entirely: {hits}"
    )


def test_sensitivity_a_planted_provider_delete_bypass_is_caught() -> None:
    planted = _tree(
        """
        from app.services.storage import get_dotmac_files_provider

        def bypass(key):
            provider = get_dotmac_files_provider()
            provider.delete(key)
        """
    )
    assert find_provider_delete_misuse(planted) is True


def test_sensitivity_a_read_provider_delete_bypass_is_also_caught() -> None:
    planted = _tree(
        """
        from app.services.storage import get_dotmac_files_read_provider

        def bypass(key):
            provider = get_dotmac_files_read_provider()
            provider.delete(key)
        """
    )
    assert find_provider_delete_misuse(planted) is True


def test_sensitivity_using_the_provider_for_something_other_than_delete_is_not_a_hit() -> (
    None
):
    near_miss = _tree(
        """
        from app.services.storage import get_dotmac_files_provider

        def observe(key):
            provider = get_dotmac_files_provider()
            return provider.exists(key)
        """
    )
    assert find_provider_delete_misuse(near_miss) is False


def test_sensitivity_a_chained_factory_delete_call_is_caught() -> None:
    planted = _tree(
        """
        from app.services.storage import get_dotmac_files_provider

        def bypass(key):
            get_dotmac_files_provider().delete(key)
        """
    )
    assert find_provider_delete_misuse(planted) is True


def test_sensitivity_a_chained_read_factory_delete_call_is_caught() -> None:
    planted = _tree(
        """
        from app.services.storage import get_dotmac_files_read_provider

        def bypass(key):
            get_dotmac_files_read_provider().delete(key)
        """
    )
    assert find_provider_delete_misuse(planted) is True


def test_sensitivity_an_aliased_module_factory_delete_call_is_caught() -> None:
    planted = _tree(
        """
        import app.services.storage as s

        def bypass(key):
            provider = s.get_dotmac_files_provider()
            provider.delete(key)
        """
    )
    assert find_provider_delete_misuse(planted) is True


def test_sensitivity_a_provider_passed_as_a_parameter_is_not_caught() -> None:
    """Documented limitation: a provider handed in as a parameter is opaque
    to this AST-only scan -- see the module docstring."""
    near_miss = _tree(
        """
        def bypass(provider, key):
            provider.delete(key)
        """
    )
    assert find_provider_delete_misuse(near_miss) is False


# ---------------------------------------------------------------------------
# Guard 3 tests
# ---------------------------------------------------------------------------


def test_no_module_constructs_the_provider_directly() -> None:
    hits = scan_provider_construction()
    assert hits == set(), (
        "These modules construct DotmacFilesS3Provider directly, bypassing "
        f"both factory functions: {hits}"
    )


def test_sensitivity_a_planted_direct_construction_is_caught() -> None:
    planted = _tree(
        """
        from app.services.storage import DotmacFilesS3Provider

        def bypass():
            return DotmacFilesS3Provider()
        """
    )
    assert find_provider_construction(planted) is True


def test_sensitivity_an_aliased_module_construction_is_caught() -> None:
    planted = _tree(
        """
        import app.services.storage as s

        def bypass():
            return s.DotmacFilesS3Provider()
        """
    )
    assert find_provider_construction(planted) is True


# ---------------------------------------------------------------------------
# Guard 4 tests
# ---------------------------------------------------------------------------


#: Grandfathered legitimate non-delete callers of the write-provider factory
#: (upload paths, verified by reading each — none calls ``.delete(``). A
#: blanket "only storage.py" rule is false against this tree; see the module
#: docstring. This is a two-directional ratchet: a name leaving this set
#: without being removed here is as much a signal as a new one arriving.
GRANDFATHERED_WRITE_PROVIDER_IMPORTERS = frozenset(
    {
        "app/services/file_upload.py",
        "app/api/finance/import_export.py",
        "app/tasks/imports.py",
    }
)


def test_write_provider_importers_match_the_grandfathered_baseline() -> None:
    hits = scan_write_provider_imports()
    new = hits - GRANDFATHERED_WRITE_PROVIDER_IMPORTERS
    gone = GRANDFATHERED_WRITE_PROVIDER_IMPORTERS - hits
    assert not new, (
        "A new caller imports get_dotmac_files_provider; read it and either "
        f"add it to the grandfathered baseline above or fix it: {new}"
    )
    assert not gone, (
        "A grandfathered caller no longer imports get_dotmac_files_provider; "
        f"shrink the baseline above to keep the ratchet honest: {gone}"
    )


def test_sensitivity_a_planted_write_provider_import_is_caught() -> None:
    planted = _tree("from app.services.storage import get_dotmac_files_provider\n")
    assert find_write_provider_import(planted) is True


def test_sensitivity_importing_the_read_provider_is_not_a_hit() -> None:
    near_miss = _tree(
        "from app.services.storage import get_dotmac_files_read_provider\n"
    )
    assert find_write_provider_import(near_miss) is False


def test_sensitivity_an_aliased_module_write_provider_reference_is_caught() -> None:
    planted = _tree(
        """
        import app.services.storage as s

        def bypass():
            return s.get_dotmac_files_provider()
        """
    )
    assert find_write_provider_import(planted) is True
