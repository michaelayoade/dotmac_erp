"""Read-only roles cannot mutate Fixed Assets (ADR-0006).

The architecture test next door proves every mutating Fixed Assets route
*declares* a granular guard. This one proves the guards actually deny the
roles that were escalated: it takes the seeded grants for `auditor`,
`finance_viewer` and `asset_viewer` verbatim from `scripts/seed_rbac.py`,
resolves the real dependency FastAPI wired onto the real route, and calls it.

Before this change all three roles — each holding only `fa:access`,
`fa:assets:read`, `fa:depreciation:read` and `fa:categories:read` — could POST
every mutating route in the module, including the one that posts depreciation
journals to the general ledger.

There are positive controls throughout. A test where everybody gets a 403
proves only that something is broken, not that the right thing is enforced:
`finance_director` must still get through every route here, the read-only
roles must keep their read access, and `asset_manager`'s deliberate
separation of run-from-post must survive.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.web.deps import WebAuthContext
from app.web.fixed_assets import router as fa_router
from scripts.seed_rbac import ROLE_PERMISSIONS

# Roles seeded with read-only Fixed Assets grants. Each of these could reach
# all 23 mutating routes through `fa:access` alone.
READ_ONLY_ROLES = ("auditor", "finance_viewer", "asset_viewer")

# (method, path) of routes that change state — the two the scoping named
# (dispose, and posting a depreciation run to the ledger) plus six more
# spanning every resource family in the module. `inventory_manager`, the
# fourth escalated role, is covered by the seeded-grant assertions at the
# bottom; it holds the identical grant set as the three above.
MUTATING_ROUTES = (
    ("POST", "/fixed-assets/assets/{asset_id}/dispose"),
    ("POST", "/fixed-assets/depreciation/runs/{run_id}/post"),
    ("POST", "/fixed-assets/depreciation/run"),
    ("POST", "/fixed-assets/assets/new"),
    ("POST", "/fixed-assets/assets/bulk-delete"),
    ("POST", "/fixed-assets/reports/gl-reconciliation/packages/{run_id}/approve"),
    ("POST", "/fixed-assets/categories/new"),
    ("POST", "/fixed-assets/import/{entity_type}"),
)

READ_ROUTES = (
    ("GET", "/fixed-assets/assets"),
    ("GET", "/fixed-assets/depreciation"),
)


def _context(role: str) -> WebAuthContext:
    """An authenticated principal holding exactly the seeded grants for `role`."""
    return WebAuthContext(
        is_authenticated=True,
        roles=[role],
        scopes=list(ROLE_PERMISSIONS[role]),
    )


def _route_guard(method: str, path: str):
    """The authorization dependency FastAPI wired onto this route.

    Resolved from the live router rather than re-declared here, so a route
    that loses its guard fails this test instead of quietly passing a copy.
    """
    matches = [
        route
        for route in fa_router.routes
        if getattr(route, "path", None) == path
        and method in getattr(route, "methods", set())
    ]
    assert len(matches) == 1, f"expected exactly one {method} {path}, got {matches}"

    guards = [
        dependency
        for dependency in matches[0].dependant.dependencies
        if dependency.name == "auth"
    ]
    assert len(guards) == 1, f"{method} {path} has {len(guards)} auth guards"
    return guards[0].call


def _authorize(method: str, path: str, auth: WebAuthContext) -> WebAuthContext:
    return _route_guard(method, path)(auth=auth)


def _denied(method: str, path: str, auth: WebAuthContext) -> int:
    with pytest.raises(HTTPException) as exc:
        _authorize(method, path, auth)
    return exc.value.status_code


# ---------------------------------------------------------------------------
# The escalation itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", READ_ONLY_ROLES)
@pytest.mark.parametrize("method,path", MUTATING_ROUTES)
def test_read_only_role_is_denied_a_mutating_route(
    role: str, method: str, path: str
) -> None:
    assert _denied(method, path, _context(role)) == 403, (
        f"{role} holds only read grants and must not reach {method} {path}"
    )


@pytest.mark.parametrize("role", READ_ONLY_ROLES)
@pytest.mark.parametrize("method,path", READ_ROUTES)
def test_read_only_role_keeps_its_read_access(
    role: str, method: str, path: str
) -> None:
    """Positive control: the fix narrows write authority, not visibility."""
    auth = _context(role)
    assert _authorize(method, path, auth) is auth


# ---------------------------------------------------------------------------
# Positive controls — the granted roles still get through
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", MUTATING_ROUTES)
def test_finance_director_still_reaches_every_mutating_route(
    method: str, path: str
) -> None:
    auth = _context("finance_director")
    assert _authorize(method, path, auth) is auth


def test_asset_manager_may_run_depreciation_but_not_post_it() -> None:
    """Separation of duties that predates ADR-0006 must survive it.

    `asset_manager` holds `fa:depreciation:run` and not `fa:depreciation:post`.
    Before the fix the distinction was decorative — `fa:access` reached both.
    """
    auth = _context("asset_manager")
    assert _authorize("POST", "/fixed-assets/depreciation/run", auth) is auth
    assert _denied("POST", "/fixed-assets/depreciation/runs/{run_id}/post", auth) == 403


def test_asset_custodian_cannot_dispose_or_run_depreciation() -> None:
    """The custodian was never granted either, yet could POST both."""
    auth = _context("asset_custodian")
    assert _denied("POST", "/fixed-assets/assets/{asset_id}/dispose", auth) == 403
    assert _denied("POST", "/fixed-assets/depreciation/run", auth) == 403
    # ...and keeps the count-check grant this change gives it.
    assert (
        _authorize(
            "POST",
            "/fixed-assets/count-plans/{audit_plan_id}/lines/{audit_line_id}/check",
            auth,
        )
        is auth
    )


def test_an_admin_is_not_accidentally_locked_out() -> None:
    auth = WebAuthContext(is_authenticated=True, roles=["admin"], scopes=[])
    for method, path in MUTATING_ROUTES:
        assert _authorize(method, path, auth) is auth


# ---------------------------------------------------------------------------
# The seeded grants these assertions rest on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", (*READ_ONLY_ROLES, "inventory_manager"))
def test_the_read_only_roles_really_are_read_only(role: str) -> None:
    """If a future change grants one of these a write scope, the denials above
    would start passing for the wrong reason. Pin the premise."""
    write_scopes = sorted(
        scope
        for scope in ROLE_PERMISSIONS[role]
        if scope.startswith("fa:") and not scope.endswith((":access", ":read"))
    )
    assert write_scopes == [], f"{role} is no longer read-only in FA: {write_scopes}"


def test_the_module_visibility_scope_is_still_shared_by_everyone() -> None:
    """`fa:access` remains a navigation scope held by readers and writers
    alike — which is precisely why it can never be write authority."""
    holders = {
        role
        for role, perms in ROLE_PERMISSIONS.items()
        if "fa:access" in perms and role != "admin"
    }
    assert holders >= {
        "auditor",
        "finance_viewer",
        "asset_viewer",
        "inventory_manager",
        "asset_custodian",
        "asset_manager",
        "finance_manager",
        "finance_director",
    }
