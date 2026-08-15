"""The tenant-catalog discovery contract stays narrow.

The contract's security argument has four load-bearing parts, and each one is
an assertion here rather than a claim in a docstring:

1. the definer returns identifiers and nothing else;
2. it is not EXECUTE-able by PUBLIC, and is granted to exactly one role;
3. its ``search_path`` is pinned, so it cannot be hijacked by a caller that
   plants its own ``organization`` relation; and
4. discovery has exactly one owner — no other module calls the function, and
   no module re-grows the "enumerate organizations under ``cross_org_session``"
   pattern the contract exists to retire.

A test that only checked (1) would pass while the function was world-executable.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "alembic" / "versions" / "20260815_tenant_catalog_discovery.py"
CONTRACT_MODULE = REPO_ROOT / "app" / "tenant_catalog.py"
RUNTIME_ROOTS = ("app", "scripts")
SKIPPED_PARTS = frozenset({"__pycache__", "archive"})

#: The only module allowed to name the definer, and the only module allowed to
#: enumerate the organization catalog.
DISCOVERY_OWNER = "app/tenant_catalog.py"

#: Mirrors ``app.tenant_catalog.DISCOVERY_FUNCTION``. Duplicated rather than
#: imported so the guard keeps working if importing the app package ever needs
#: settings or a database.
DISCOVERY_FUNCTION = "tenant_catalog.organization_ids"


def _runtime_files() -> list[Path]:
    files: list[Path] = []
    for root in RUNTIME_ROOTS:
        for path in (REPO_ROOT / root).rglob("*.py"):
            if SKIPPED_PARTS & set(path.parts):
                continue
            files.append(path)
    return sorted(files)


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


@pytest.fixture(scope="module")
def migration_source() -> str:
    return MIGRATION.read_text()


def test_the_definer_returns_identifiers_and_nothing_else(
    migration_source: str,
) -> None:
    """``RETURNS SETOF uuid`` is the whole narrowness guarantee.

    Widening this to ``SETOF record``, a composite type, or ``TABLE(...)`` would
    turn a discovery hole into a general cross-tenant read of organization data
    with none of RLS's guarantees — so the return type is pinned exactly.
    """
    assert "RETURNS SETOF uuid" in migration_source

    body = migration_source.split("$function$")[1]
    selected = re.search(r"SELECT\s+(.+?)\s+FROM", body, re.S | re.I)
    assert selected is not None, "could not find the definer's target list"
    assert selected.group(1).strip() == "o.organization_id", (
        "the definer must select the identifier column alone; selecting any "
        "further column makes it a cross-tenant read path for that column"
    )


def test_the_definer_is_not_executable_by_public(migration_source: str) -> None:
    """A function is EXECUTE-able by PUBLIC from the moment it is created."""
    assert "REVOKE ALL ON FUNCTION" in migration_source
    assert "FROM PUBLIC" in migration_source

    # The GRANT is emitted by a loop over DISCOVERY_GRANTEES, so the grantee
    # list is read from the module rather than from the SQL text — a regex for
    # a literal "TO <role>" would match nothing here and pass vacuously.
    namespace: dict[str, object] = {}
    for node in ast.parse(migration_source).body:
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == "DISCOVERY_GRANTEES"
            for target in node.targets
        ):
            namespace["grantees"] = ast.literal_eval(node.value)
    assert "grantees" in namespace, "DISCOVERY_GRANTEES must declare the grantees"
    assert set(namespace["grantees"]) == {"app_user"}, (
        f"discovery is granted to {sorted(namespace['grantees'])}; only "
        "app_user runs batch entry points, and every added role widens who "
        "can enumerate tenants"
    )


def test_the_definer_pins_its_search_path(migration_source: str) -> None:
    """Without a pinned ``search_path`` a definer is hijackable.

    A caller that can create a schema earlier in the path can plant its own
    ``organization`` relation and have this function read it with ``app_admin``
    privileges. Pinning the path AND schema-qualifying the body closes that.
    """
    assert "SET search_path = pg_catalog" in migration_source
    assert "core_org.organization" in migration_source, (
        "the body must schema-qualify its table; a pinned search_path that "
        "does not contain the table would otherwise fail at runtime"
    )


def test_the_definer_is_owned_by_app_admin(migration_source: str) -> None:
    """SECURITY DEFINER means "runs as its owner", so the owner is the grant."""
    assert "SECURITY DEFINER" in migration_source
    assert 'owner != "app_admin"' in migration_source, (
        "the migration must refuse to install the definer under any identity "
        "other than app_admin, whose reviewed posture is BYPASSRLS NOSUPERUSER"
    )


def test_only_the_contract_module_calls_the_discovery_function() -> None:
    """One owner for discovery, enforced rather than documented.

    Matched on the SQL call shape (``FROM tenant_catalog.organization_ids(``)
    rather than on the bare dotted name: the bare name also appears in Sphinx
    cross-references in other modules' docstrings, and a guard that fires on
    prose teaches people to work around it.
    """
    offenders = [
        _relative(path)
        for path in _runtime_files()
        if f"FROM {DISCOVERY_FUNCTION}(" in path.read_text()
        and _relative(path) != DISCOVERY_OWNER
    ]
    assert offenders == [], (
        f"{offenders} call the discovery definer directly. Discovery has one "
        f"owner ({DISCOVERY_OWNER}); callers use active_organization_ids() or "
        "for_each_organization() so the narrowing lives in one place."
    )


def _selects_the_organization_id_column(node: ast.AST) -> bool:
    """True for ``select(Organization.organization_id)`` — the retired shape.

    Deliberately narrower than "mentions Organization anywhere". A bootstrap
    that queries the *entity* cross-org for a genuine reason —
    ``scripts/create_org.py`` checks organization-code uniqueness before the
    organization exists, when there is by definition no tenant to scope to —
    is a different disposition, and flagging it here would make the guard
    something to route around rather than satisfy.
    """
    for inner in ast.walk(node):
        if not (
            isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "select"
        ):
            continue
        for argument in inner.args:
            if (
                isinstance(argument, ast.Attribute)
                and argument.attr == "organization_id"
                and getattr(argument.value, "id", None) == "Organization"
            ):
                return True
    return False


def test_no_module_re_grows_the_cross_org_catalog_enumeration() -> None:
    """The retired pattern must not come back.

    ``select(Organization.organization_id)`` inside a ``cross_org_session`` is
    the exact shape this slice removed: it returns every row under today's
    ``postgres`` superuser and zero rows under ``app_user``, so the task
    silently processes nothing and reports success.
    """
    offenders: list[str] = []
    for path in _runtime_files():
        relative = _relative(path)
        if relative == DISCOVERY_OWNER:
            continue
        source = path.read_text()
        if "cross_org_session" not in source:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            opens_cross_org = any(
                isinstance(item.context_expr, ast.Call)
                and getattr(item.context_expr.func, "id", None) == "cross_org_session"
                for item in node.items
            )
            if not opens_cross_org:
                continue
            if _selects_the_organization_id_column(node):
                offenders.append(f"{relative}:{node.lineno}")
    assert offenders == [], (
        f"{offenders} enumerate the organization catalog under "
        "cross_org_session. core_org.organization is RLS-protected: use "
        "app.tenant_catalog.active_organization_ids() instead."
    )


def _dump_without_docstring(node: ast.FunctionDef) -> str:
    """Dump a function's CODE, not its prose.

    These docstrings discuss ``allow_cross_org`` at length in order to explain
    why the PostgreSQL path must not use it. Dumping the docstring alongside the
    body would make the guard fire on its own explanation — a guard that the
    correct implementation cannot satisfy.
    """
    stripped = ast.FunctionDef(
        name=node.name,
        args=node.args,
        body=[
            statement
            for statement in node.body
            if not (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            )
        ],
        decorator_list=node.decorator_list,
        returns=node.returns,
        type_params=[],
    )
    return ast.dump(ast.fix_missing_locations(stripped))


def test_the_contract_module_uses_raw_sql_not_the_orm_for_discovery() -> None:
    """The ORM path would need the very bypass this module retires.

    ``select(Organization...)`` on an unprimed session raises
    ``MissingOrgContextError``; suppressing that with ``allow_cross_org`` on the
    PostgreSQL path would reinstate the bypass. So the PostgreSQL branch must go
    through raw SQL, and ``allow_cross_org`` may appear only in the SQLite
    branch.
    """
    tree = ast.parse(CONTRACT_MODULE.read_text())
    functions = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert "_discover_postgresql" in functions
    assert "_discover_sqlite" in functions

    postgres_body = _dump_without_docstring(functions["_discover_postgresql"])
    assert "allow_cross_org" not in postgres_body, (
        "the PostgreSQL discovery path must not touch the ORM cross-org "
        "marker; that is the bypass this contract replaces"
    )
    assert "allow_cross_org" in ast.dump(functions["_discover_sqlite"]), (
        "the SQLite branch is the only place the marker is legitimate, and the "
        "cross-org caller inventory records it as such"
    )
