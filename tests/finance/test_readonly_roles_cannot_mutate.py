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

The second half of the file does the same job for the money-movement core —
`app/web/finance/{gl,banking,ar}.py`, 104 mutating routes whose only guard was
`require_finance_access`, i.e. the `finance:access` navigation scope that
`auditor`, `finance_viewer` and `junior_accountant` all hold. It also pins the
separations of duty the decomposition creates, in both directions: the roles
that may post to the ledger, and the roles that may draft but not post.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.web.deps import WebAuthContext, require_finance_access
from app.web.finance.ar import router as ar_router
from app.web.finance.banking import router as banking_router
from app.web.finance.gl import router as gl_router
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


def _route_guard(method: str, path: str, router=fa_router):
    """The authorization dependency FastAPI wired onto this route.

    Resolved from the live router rather than re-declared here, so a route
    that loses its guard fails this test instead of quietly passing a copy.
    """
    matches = [
        route
        for route in router.routes
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


def _authorize(
    method: str, path: str, auth: WebAuthContext, router=fa_router
) -> WebAuthContext:
    return _route_guard(method, path, router)(auth=auth)


def _denied(method: str, path: str, auth: WebAuthContext, router=fa_router) -> int:
    with pytest.raises(HTTPException) as exc:
        _authorize(method, path, auth, router)
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


# ---------------------------------------------------------------------------
# The money-movement core: app/web/finance/{gl,banking,ar}.py
#
# 104 mutating routes whose only guard was `require_finance_access` — the
# `finance:access` navigation scope. Same defect, same ADR, larger blast
# radius: posting journals, closing periods, voiding invoices and approving
# bank reconciliations were all reachable by every read-only finance role.
# ---------------------------------------------------------------------------

FINANCE_READ_ONLY_ROLES = ("auditor", "finance_viewer", "junior_accountant")

# (router, method, path) — nine routes spanning all three modules and every
# irreversible act the decomposition had to separate. `create-and-match` is
# here because it creates, submits, approves AND posts a journal in one call.
FINANCE_MUTATING_ROUTES = (
    (gl_router, "POST", "/gl/journals/{entry_id}/post"),
    (gl_router, "POST", "/gl/journals/bulk-post"),
    (gl_router, "POST", "/gl/periods/{period_id}/close"),
    (ar_router, "POST", "/ar/invoices/{invoice_id}/void"),
    (ar_router, "POST", "/ar/invoices/{invoice_id}/post"),
    (ar_router, "POST", "/ar/credit-notes/{credit_note_id}/post"),
    (banking_router, "POST", "/banking/reconciliations/{reconciliation_id}/approve"),
    (banking_router, "POST", "/banking/statements/import"),
    (
        banking_router,
        "POST",
        "/banking/statements/{statement_id}/lines/{line_id}/create-and-match",
    ),
)

# GET routes keep the visibility gate: this change narrows write authority,
# it does not take the module away from anyone who could read it.
FINANCE_READ_ROUTES = (
    (gl_router, "GET", "/gl/journals"),
    (ar_router, "GET", "/ar/invoices"),
    (banking_router, "GET", "/banking/accounts"),
)


@pytest.mark.parametrize("role", FINANCE_READ_ONLY_ROLES)
@pytest.mark.parametrize("router,method,path", FINANCE_MUTATING_ROUTES)
def test_read_only_finance_role_is_denied_a_mutating_route(
    role: str, router, method: str, path: str
) -> None:
    assert _denied(method, path, _context(role), router) == 403, (
        f"{role} holds only read grants in GL/AR/Banking and must not reach "
        f"{method} {path}"
    )


@pytest.mark.parametrize("router,method,path", FINANCE_MUTATING_ROUTES)
def test_finance_director_still_reaches_every_finance_route(
    router, method: str, path: str
) -> None:
    """Positive control. A 403-for-everybody test proves nothing."""
    auth = _context("finance_director")
    assert _authorize(method, path, auth, router) is auth


@pytest.mark.parametrize("router,method,path", FINANCE_READ_ROUTES)
def test_finance_get_routes_keep_the_module_visibility_gate(
    router, method: str, path: str
) -> None:
    """`require_finance_access` is still the right guard — for reads.

    ADR-0006 narrows what a visibility scope may authorize; it does not
    delete the scope. If a later change swaps these for a permission guard,
    the read-only roles silently lose the module and this test says so.
    """
    assert _route_guard(method, path, router) is require_finance_access


# ---------------------------------------------------------------------------
# Separation of duties — the point of naming the act rather than the module
# ---------------------------------------------------------------------------


def test_accountant_may_draft_a_journal_but_not_post_one() -> None:
    """`accountant` holds `gl:journals:create` and not `gl:journals:post`.

    Before this change the distinction was decorative: `finance:access`
    reached both, and reached the banking route that posts a journal without
    ever showing one for approval.
    """
    auth = _context("accountant")
    assert _authorize("POST", "/gl/journals/new", auth, gl_router) is auth
    assert _denied("POST", "/gl/journals/{entry_id}/post", auth, gl_router) == 403
    assert (
        _denied(
            "POST",
            "/banking/statements/{statement_id}/lines/{line_id}/create-and-match",
            auth,
            banking_router,
        )
        == 403
    )


def test_ar_clerk_may_submit_an_invoice_but_not_approve_or_void_it() -> None:
    """The clerk raises the invoice; the manager approves it.

    `ar:invoices:submit` is new here and granted to `ar_clerk`;
    `ar:invoices:approve` is new and deliberately is not.
    """
    auth = _context("ar_clerk")
    assert _authorize("POST", "/ar/invoices/new", auth, ar_router) is auth
    assert (
        _authorize("POST", "/ar/invoices/{invoice_id}/submit", auth, ar_router) is auth
    )
    assert _denied("POST", "/ar/invoices/{invoice_id}/approve", auth, ar_router) == 403
    assert _denied("POST", "/ar/invoices/{invoice_id}/void", auth, ar_router) == 403


def test_senior_accountant_may_import_a_statement_but_not_delete_one() -> None:
    """`banking:statements:delete` destroys reconciliation source data and is
    seeded to the director and the manager only."""
    auth = _context("senior_accountant")
    assert (
        _authorize("POST", "/banking/statements/import", auth, banking_router) is auth
    )
    assert (
        _denied(
            "POST", "/banking/statements/{statement_id}/delete", auth, banking_router
        )
        == 403
    )


def test_finance_manager_may_close_a_period_but_not_reopen_one() -> None:
    """A deliberate consequence, recorded so it is reviewed rather than found.

    `POST /periods/{id}/open` moves a period out of FUTURE *or* SOFT_CLOSED,
    so it asks for `gl:periods:reopen` — the stronger act it can perform.
    `finance_manager` holds `gl:periods:close` and has never held
    `gl:periods:reopen`, so it loses web reopen. Filling that omission would
    be an authority increase hidden inside a security narrowing.
    """
    auth = _context("finance_manager")
    assert _authorize("POST", "/gl/periods/{period_id}/close", auth, gl_router) is auth
    assert _denied("POST", "/gl/periods/{period_id}/open", auth, gl_router) == 403

    # Positive control: somebody still can, so this is a separation of duties
    # and not an unreachable route.
    director = _context("finance_director")
    assert (
        _authorize("POST", "/gl/periods/{period_id}/open", director, gl_router)
        is director
    )


def test_junior_accountant_may_draft_a_journal_but_not_edit_or_delete_one() -> None:
    """The other deliberate consequence.

    `junior_accountant` holds `gl:journals:create`; the new
    `gl:journals:update` that guards editing and deleting a draft is not
    granted to it, per ADR-0006 decision 5.
    """
    auth = _context("junior_accountant")
    assert _authorize("POST", "/gl/journals/new", auth, gl_router) is auth
    assert _denied("POST", "/gl/journals/{entry_id}/edit", auth, gl_router) == 403
    assert _denied("POST", "/gl/journals/bulk-delete", auth, gl_router) == 403


def test_an_admin_is_not_accidentally_locked_out_of_finance() -> None:
    auth = WebAuthContext(is_authenticated=True, roles=["admin"], scopes=[])
    for router, method, path in FINANCE_MUTATING_ROUTES:
        assert _authorize(method, path, auth, router) is auth


# ---------------------------------------------------------------------------
# The seeded grants these assertions rest on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", FINANCE_READ_ONLY_ROLES)
def test_the_finance_read_only_roles_hold_no_ledger_write_scope(role: str) -> None:
    """Pin the premise. `junior_accountant` is allowed exactly one write scope
    — `gl:journals:create`, which drafts and never posts — and the other two
    roles are allowed none. If a future change grants any of them a posting,
    approval, void or delete scope, the denials above would start passing for
    the wrong reason."""
    allowed = {"gl:journals:create"}
    write_scopes = sorted(
        scope
        for scope in ROLE_PERMISSIONS[role]
        if scope.split(":")[0] in {"gl", "ar", "banking"}
        and not scope.endswith((":access", ":read"))
        and scope not in allowed
    )
    assert write_scopes == [], (
        f"{role} is no longer read-only in finance: {write_scopes}"
    )


def test_finance_access_is_still_a_navigation_scope_everyone_shares() -> None:
    """Which is precisely why it can never be write authority."""
    holders = {
        role
        for role, perms in ROLE_PERMISSIONS.items()
        if "finance:access" in perms and role != "admin"
    }
    assert holders >= {
        "auditor",
        "finance_viewer",
        "junior_accountant",
        "accountant",
        "senior_accountant",
        "ar_clerk",
        "ap_clerk",
        "finance_manager",
        "finance_director",
    }
