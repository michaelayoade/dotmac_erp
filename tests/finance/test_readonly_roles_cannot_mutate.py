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
from app.web.finance.automation import router as automation_router
from app.web.finance.exp import router as exp_router
from app.web.finance.import_export import router as import_router
from app.web.finance.quote import router as quote_router
from app.web.finance.remita import router as remita_router
from app.web.finance.reports import router as reports_router
from app.web.finance.sales_order import router as sales_order_router
from app.web.finance.settings import router as settings_router
from app.web.finance.tax import router as tax_router
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


# ---------------------------------------------------------------------------
# app/web/finance/** — the same defect, nine modules further on
#
# `require_finance_access` is `if not auth.has_module_access("finance")` and
# nothing else, and it was the sole write authority on 68 mutating routes
# across automation, tax, sales orders, settings, quotes, Remita, expense
# claims, imports and reports. `require_expense_access` is the same hole under
# a second name: it returns OK on `finance:access` too, which is how exp.py's
# mutating routes were reachable.
#
# `auditor`, `finance_viewer` and `junior_accountant` all hold `finance:access`
# and are deliberately read-only in their granular grants, so all three could
# file a statutory tax return, assert that money arrived against a Remita
# reference, execute a bulk import, and re-issue document numbers.
#
# Every denial below is paired with a POSITIVE CONTROL naming the role that
# legitimately performs that act. A route where everybody gets a 403 is broken,
# not secured.
# ---------------------------------------------------------------------------

# Read-only in finance and holders of `finance:access`. `auditor` and
# `finance_viewer` are read-only by design; `junior_accountant` is "data entry
# and draft creation only" and holds no approve, post, file or manage scope.
READ_ONLY_FINANCE_ROLES = ("auditor", "finance_viewer", "junior_accountant")

# (router, method, path, the role that SHOULD reach it). One per decomposed
# module, chosen for consequence rather than convenience: filing a statutory
# return, asserting a payment arrived, executing a bulk import, re-issuing
# document numbers, generating a real document from a template.
FINANCE_MUTATING_ROUTES = (
    (tax_router, "POST", "/tax/returns/{return_id}/file", "tax_specialist"),
    (tax_router, "POST", "/tax/codes/{tax_code_id}/toggle", "tax_specialist"),
    (remita_router, "POST", "/remita/{rrr_id}/mark-paid", "finance_manager"),
    (remita_router, "POST", "/remita/{rrr_id}/cancel", "ar_clerk"),
    (
        settings_router,
        "POST",
        "/settings/numbering/{sequence_id}/reset",
        "finance_director",
    ),
    (settings_router, "POST", "/settings/exchange-rates", "finance_director"),
    (sales_order_router, "POST", "/sales-orders/{so_id}/approve", "finance_manager"),
    (quote_router, "POST", "/quotes/{quote_id}/convert-to-invoice", "finance_manager"),
    (
        automation_router,
        "POST",
        "/automation/recurring/{template_id}/generate",
        "finance_director",
    ),
    (import_router, "POST", "/import/{entity_type}", "finance_manager"),
    (reports_router, "POST", "/reports/general-ledger/export", "senior_accountant"),
    (exp_router, "POST", "/expense/claims/{claim_id}/cancel", "expense_admin"),
    (exp_router, "POST", "/expense/new", "employee"),
)

# GET routes keep their module-visibility guard: this change narrows write
# authority, it does not take pages away from anyone.
FINANCE_READ_ROUTES = (
    (tax_router, "GET", "/tax/returns"),
    (quote_router, "GET", "/quotes"),
    (remita_router, "GET", "/remita"),
)


def _guard_on(router, method: str, path: str):
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


def _finance_denied(router, method: str, path: str, auth: WebAuthContext) -> int:
    with pytest.raises(HTTPException) as exc:
        _guard_on(router, method, path)(auth=auth)
    return exc.value.status_code


@pytest.mark.parametrize("role", READ_ONLY_FINANCE_ROLES)
@pytest.mark.parametrize(
    "router,method,path",
    [(r, m, p) for r, m, p, _ in FINANCE_MUTATING_ROUTES],
    ids=[f"{m} {p}" for _, m, p, _ in FINANCE_MUTATING_ROUTES],
)
def test_read_only_finance_role_is_denied_a_mutating_route(
    role: str, router, method: str, path: str
) -> None:
    assert _finance_denied(router, method, path, _context(role)) == 403, (
        f"{role} holds finance:access and no write grant for this act, "
        f"and must not reach {method} {path}"
    )


@pytest.mark.parametrize(
    "router,method,path,granted_role",
    FINANCE_MUTATING_ROUTES,
    ids=[f"{m} {p}" for _, m, p, _ in FINANCE_MUTATING_ROUTES],
)
def test_the_granted_role_still_reaches_the_route(
    router, method: str, path: str, granted_role: str
) -> None:
    """Positive control, one per route. Without this the denials above would
    pass just as well if every guard named a permission nobody holds."""
    auth = _context(granted_role)
    assert _guard_on(router, method, path)(auth=auth) is auth


@pytest.mark.parametrize(
    "router,method,path",
    FINANCE_READ_ROUTES,
    ids=[f"{m} {p}" for _, m, p in FINANCE_READ_ROUTES],
)
def test_finance_read_routes_keep_the_module_visibility_guard(
    router, method: str, path: str
) -> None:
    """The other half of the ADR: a `<module>:access` scope is still exactly
    the right guard for a page you may only look at."""
    guard = _guard_on(router, method, path)
    assert guard.__name__ == "require_finance_access", (
        f"{method} {path} is a read and should still rest on module "
        f"visibility, but is guarded by {guard.__name__}"
    )


def test_tax_specialist_may_file_a_return_but_not_touch_fiscal_positions() -> None:
    """Separation inside one module, which a single `finance:access` erased.

    `tax_specialist` holds `tax:returns:file` and `tax:codes:manage`;
    `finance_manager` holds `tax:returns:review` and deliberately NOT
    `tax:returns:file`. Before this change both reached both.
    """
    specialist = _context("tax_specialist")
    manager = _context("finance_manager")

    assert (
        _guard_on(tax_router, "POST", "/tax/returns/{return_id}/file")(auth=specialist)
        is specialist
    )
    assert (
        _finance_denied(tax_router, "POST", "/tax/returns/{return_id}/file", manager)
        == 403
    )
    assert (
        _guard_on(tax_router, "POST", "/tax/returns/{return_id}/review")(auth=manager)
        is manager
    )


def test_ar_clerk_runs_the_rrr_lifecycle_but_cannot_assert_payment() -> None:
    """`mark_paid` settles the source document, so it sits with the roles that
    may post a receipt. ar_clerk holds `ar:receipts:create`, not `:post`."""
    clerk = _context("ar_clerk")
    assert _guard_on(remita_router, "POST", "/remita/generate")(auth=clerk) is clerk
    assert (
        _finance_denied(remita_router, "POST", "/remita/{rrr_id}/mark-paid", clerk)
        == 403
    )


def test_configuring_a_numbering_sequence_is_not_resetting_its_counter() -> None:
    """Editing a sequence changes future numbers; resetting the counter can
    re-issue numbers already printed on filed invoices."""
    manager = _context("finance_manager")
    director = _context("finance_director")

    assert (
        _guard_on(settings_router, "POST", "/settings/numbering/{sequence_id}")(
            auth=manager
        )
        is manager
    )
    assert (
        _finance_denied(
            settings_router, "POST", "/settings/numbering/{sequence_id}/reset", manager
        )
        == 403
    )
    assert (
        _guard_on(settings_router, "POST", "/settings/numbering/{sequence_id}/reset")(
            auth=director
        )
        is director
    )


def test_an_employee_may_comment_on_a_claim_but_an_auditor_may_not() -> None:
    """`require_expense_access` admits `finance:access`, so `auditor` — whose
    only expense grant is `expense:claims:read` — could write to a claim's
    official record."""
    employee = _context("employee")
    auditor = _context("auditor")

    path = "/expense/claims/{claim_id}/comments"
    assert _guard_on(exp_router, "POST", path)(auth=employee) is employee
    assert _finance_denied(exp_router, "POST", path, auditor) == 403


def test_withdrawing_an_approval_is_open_to_any_approver_tier() -> None:
    """`expense_approver` holds tier2 only. The route it needs is the one that
    reverses its own decision, so it must not be pinned to the tier1 name."""
    approver = _context("expense_approver")
    path = "/expense/claims/{claim_id}/withdraw-approval"
    assert _guard_on(exp_router, "POST", path)(auth=approver) is approver
    assert _finance_denied(exp_router, "POST", path, _context("auditor")) == 403


def test_an_admin_reaches_every_decomposed_finance_route() -> None:
    auth = WebAuthContext(is_authenticated=True, roles=["admin"], scopes=[])
    for router, method, path, _ in FINANCE_MUTATING_ROUTES:
        assert _guard_on(router, method, path)(auth=auth) is auth


# ---------------------------------------------------------------------------
# The seeded premises the finance assertions rest on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", READ_ONLY_FINANCE_ROLES)
def test_the_read_only_finance_roles_hold_the_visibility_scope(role: str) -> None:
    """The denials above are only interesting because these roles CAN see the
    finance module. If a future change drops `finance:access` from one of them
    the test would start passing for an entirely different reason."""
    assert "finance:access" in ROLE_PERMISSIONS[role]


@pytest.mark.parametrize("role", READ_ONLY_FINANCE_ROLES)
def test_the_read_only_finance_roles_hold_no_decomposed_write_scope(role: str) -> None:
    """Pin the other premise: none of them was granted any of the permissions
    the decomposed routes now name."""
    named = {
        "tax:codes:manage",
        "tax:returns:file",
        "ar:orders:approve",
        "ar:orders:ship",
        "ar:quotes:convert",
        "import:execute",
        "fx:rates:manage",
        "finance:numbering:manage",
        "finance:numbering:reset",
        "finance:settings:manage",
        "automation:recurring:generate",
        "automation:fields:manage",
        "remita:rrr:mark_paid",
        "remita:rrr:cancel",
        "expense:claims:comment",
        "expense:claims:cancel",
    }
    held = named.intersection(ROLE_PERMISSIONS[role])
    assert held == set(), f"{role} is no longer read-only in finance: {sorted(held)}"


def test_the_finance_visibility_scope_is_shared_by_readers_and_writers() -> None:
    """`finance:access` is held by read-only and write roles alike — which is
    precisely why it can never be write authority."""
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
        "finance_manager",
        "finance_director",
        "ar_clerk",
        "ap_clerk",
        "tax_specialist",
    }
