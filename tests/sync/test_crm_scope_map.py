"""The scope map must describe the routes a service credential can actually reach.

A map assembled once and never checked is worse than none: it reads as
authoritative while silently omitting whatever was added after it was written,
and a reissue made against it would under-scope the new route without anyone
noticing until the caller broke.

The comparison is STATIC — the router is parsed, not imported — so the map stays
checkable with no database, no settings and no running application. An operator
issuing a credential must be able to consult it from a checkout.

Two things this parser learned the hard way, both worth keeping:

* the guard is in the function SIGNATURE, not the decorator, so a regex that
  stops at `def` sees every route as unguarded;
* `require_crm_sync_enabled` WRAPS `require_service_auth` and adds a per-org
  kill switch, so a classifier looking only for the inner name reports
  seventeen authenticated routes as having no guard at all. Wrappers are
  therefore resolved transitively.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.api.sync.crm_scope_map import (
    CRM_SERVICE_MINIMUM_SCOPES,
    CRM_SYNC_ROUTE_SCOPES,
)

ROUTER = Path(__file__).resolve().parents[2] / "app/api/sync/dotmac_crm.py"
MAP_MODULE = Path(__file__).resolve().parents[2] / "app/api/sync/crm_scope_map.py"
RETIREMENT_GUARD = "require_crm_material_sync_retired"
TENANT_GUARD = "require_tenant_auth"
SERVICE_GUARD = "require_service_auth"


def _classified() -> dict[str, set[str]]:
    """`{bucket: {"METHOD /path"}}` for every route the router declares."""

    tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]

    def default_names(fn: ast.FunctionDef) -> set[str]:
        return {
            node.id
            for default in fn.args.defaults
            for node in ast.walk(default)
            if isinstance(node, ast.Name)
        }

    # Anything that depends on the service guard IS the service guard, however
    # many wrappers deep. Fixed point rather than a fixed depth.
    wrappers = {SERVICE_GUARD}
    changed = True
    while changed:
        changed = False
        for fn in functions:
            if fn.name not in wrappers and default_names(fn) & wrappers:
                wrappers.add(fn.name)
                changed = True

    buckets: dict[str, set[str]] = {
        "service": set(),
        "tenant": set(),
        "retired": set(),
        "unguarded": set(),
    }
    for fn in functions:
        for decorator in fn.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
                continue
            if func.value.id != "router" or not decorator.args:
                continue
            key = f"{func.attr.upper()} {decorator.args[0].value}"
            used = {n.id for n in ast.walk(decorator) if isinstance(n, ast.Name)}
            used |= default_names(fn)
            if RETIREMENT_GUARD in used:
                buckets["retired"].add(key)
            elif used & wrappers:
                buckets["service"].add(key)
            elif TENANT_GUARD in used:
                buckets["tenant"].add(key)
            else:
                buckets["unguarded"].add(key)
    return buckets


def test_every_service_route_declares_a_scope() -> None:
    """A service route with no entry is one a reissue cannot reason about."""

    missing = _classified()["service"] - set(CRM_SYNC_ROUTE_SCOPES)

    assert missing == set()


def test_the_map_names_no_route_a_credential_cannot_reach() -> None:
    """A stale or mis-filed entry keeps a scope alive for nothing.

    This is what caught `GET /projects` and `GET /work-orders`: both are live,
    but both authenticate as a TENANT, so putting them in a machine
    credential's grant would widen it for ERP's own interactive surface.
    """

    extra = set(CRM_SYNC_ROUTE_SCOPES) - _classified()["service"]

    assert extra == set()


def test_retired_routes_are_absent() -> None:
    """A retired route answers 410 to everyone; an entry would imply otherwise."""

    assert _classified()["retired"] & set(CRM_SYNC_ROUTE_SCOPES) == set()


def test_no_route_is_left_unguarded() -> None:
    """Every route resolves to a guard. An unclassified one is either a new
    auth pattern this parser has not learned, or a genuinely open endpoint —
    and both must stop the build rather than be silently excluded from the map.
    """

    assert _classified()["unguarded"] == set()


def test_the_parser_still_distinguishes_the_buckets() -> None:
    """Sensitivity. Every assertion above is a set difference and all of them
    pass trivially against an empty parse — exactly what a decorator or
    signature style change would produce.
    """

    buckets = _classified()

    assert len(buckets["service"]) >= 15
    assert len(buckets["retired"]) >= 5
    assert len(buckets["tenant"]) >= 1


def test_the_catch_all_scope_is_not_granted() -> None:
    """`crm:write` is the unlimited grant this exercise exists to remove."""

    assert "crm:write" not in CRM_SERVICE_MINIMUM_SCOPES


def test_the_minimum_set_is_derived_not_maintained() -> None:
    assert CRM_SERVICE_MINIMUM_SCOPES == frozenset(CRM_SYNC_ROUTE_SCOPES.values())


def test_the_map_imports_nothing_but_stdlib() -> None:
    """It must stay consultable with no database and no settings."""

    tree = ast.parse(MAP_MODULE.read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert modules <= {"__future__", "typing"}
