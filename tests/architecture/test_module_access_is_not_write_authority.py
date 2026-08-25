"""A module-visibility scope is never write authority (ADR-0006).

`app/web/fixed_assets.py` had one authorization dependency for all 49 of its
routes — `require_fixed_assets_access` — and 23 of those routes mutate. The
guard admitted `fa:access`, a navigation scope every read-only asset role
holds, so `auditor`, `finance_viewer`, `asset_viewer` and `inventory_manager`
could post depreciation journals to the ledger. It also admitted `fa:*`, which
the old two-directional wildcard matcher let ANY `fa:`-prefixed scope satisfy.

This file is what stops that regrowing, and it does three separable jobs:

1. **Fixed Assets is clean and must stay clean.** No mutating route there may
   have a visibility guard as its only guard.
2. **Every guarded permission is real.** A guard naming a permission that
   `seed_rbac` never defines cannot be granted to anybody, so the route is
   unreachable rather than protected — a silent denial of service that looks
   like security.
3. **No guard asks for a wildcard.** A wildcard is HELD, never REQUESTED.
   `WebAuthContext.has_permission` now matches a requested `X:*` literally, so
   a guard containing one would deny everyone; before this change it admitted
   the whole subtree. Either way it is never what the author meant.

`app/web/finance/**` carries the identical defect and is NOT fixed here. It is
frozen by a two-directional ratchet against a named per-file allowlist below:
the count may not rise, and it may not fall without lowering the allowlist in
the same reviewed change. A follow-up branch empties it.

## Scope: all of `app/web/**` (2026-08-25 amendment)

The detector originally read `app/web/fixed_assets.py` and `app/web/finance/**`
and nothing else, which is 20% of the web adapter. The premise ADR-0006 states
is fleet-wide, so the remaining 80% was not exempt from it — it was simply
unmonitored, which is the failure mode the fleet's own guard-exemption rule
(ADR-0018) names. The identical defect could be added to `app/web/people/**`,
`app/web/inventory.py`, `app/web/projects.py` or `app/web/support.py` and no
test would have noticed.

The detector now walks every `app/web/**/*.py`. `WEB_BACKLOG` below records
what that measured — 489 mutating routes authorized by module visibility alone
across 41 files — on the same two-directional ratchet, and
`UNAUTHENTICATED_WEB_MUTATIONS` separates out the strictly worse category
underneath it: mutating routes reachable with no authenticated principal at
all. Job (2) widened too and found its own debt —
`WEB_UNSEEDABLE_PERMISSIONS`. None of these tables is an assessment. All three
are debt markers.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.seed_rbac import DEFAULT_PERMISSIONS

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = REPO_ROOT / "app" / "web"
FIXED_ASSETS = WEB_ROOT / "fixed_assets.py"
FINANCE_WEB = WEB_ROOT / "finance"

MUTATING_METHODS = frozenset({"post", "put", "patch", "delete"})

PERMISSION_GUARD_FACTORIES = frozenset(
    {"require_web_permission", "require_any_web_permission"}
)

# Guards that answer "may this person SEE this module" and nothing else. Each
# admits a `<module>:access` scope that every read-only role in that module
# holds, so none of them can carry a mutating route on its own. `require_web_auth`
# and `optional_web_auth` are here because they establish identity, not
# authority.
#
# Deliberately NOT here: `require_admin_access` and `require_finance_admin`
# both test `auth.is_admin`, which is a real authority tier, not visibility.
MODULE_ACCESS_GUARDS = frozenset(
    {
        "optional_web_auth",
        "require_web_auth",
        "require_module_access",
        "require_discipline_access",
        "require_expense_access",
        "require_finance_access",
        "require_fixed_assets_access",
        "require_fleet_access",
        "require_hr_access",
        "require_inventory_access",
        "require_procurement_access",
        "require_projects_access",
        "require_public_sector_access",
        "require_self_service_access",
        "require_settings_access",
        "require_support_access",
        "require_training_access",
        # --- added 2026-08-25 with the app/web/** scope extension ---
        #
        # Every name below was classified by READING ITS BODY in
        # `app/web/deps.py`, not by its name. Each has an admit path that lets
        # a caller through on module visibility alone, which is what puts it
        # here; the granular permission each also mentions is unreachable for
        # anyone who already holds the module scope.
        #
        # `require_discipline_cases_{read,create,update}` and
        # `require_discipline_workflow_manage` all open with
        # `if auth.is_admin or auth.has_module_access("people")` and only THEN
        # consider `discipline:cases:create` / `discipline:workflow:manage`.
        # Any holder of `hr:access` — or of the `hr_manager`, `hr_director`,
        # `payroll_admin`, `payroll_approver` role names, which
        # `accessible_modules` also maps to "people" — passes without holding
        # any discipline permission. The name says capability; the body says
        # module visibility.
        #
        # `require_self_service_discipline_manager` admits
        # `discipline:access`, which IS the discipline module's visibility
        # scope (`accessible_modules` derives the module from exactly that
        # string). Its other admit paths — full discipline permissions, or a
        # position tree showing direct reports — are real; the visibility one
        # is not, and one bad path is enough.
        #
        # `require_private_performance_mode` and `require_government_pms_mode`
        # are ROUTER-level dependencies on `app/web/people/perf.py` and
        # `app/web/people/pms.py`. They chain `require_hr_access` and then
        # compare the organization's `PerformanceMode` against the route
        # family. A deployment mode is a statement about which features are
        # switched on, not about who may use them: it cannot distinguish two
        # people in the same organization, so it adds no authority over the
        # `require_hr_access` it wraps.
        "require_discipline_cases_read",
        "require_discipline_cases_create",
        "require_discipline_cases_update",
        "require_discipline_workflow_manage",
        "require_self_service_discipline_manager",
        "require_private_performance_mode",
        "require_government_pms_mode",
    }
)

# The strictly worse subset. `optional_web_auth` does not authorize and does
# not authenticate either — its docstring says so ("returns a guest context if
# no valid authentication is provided") and its body returns
# `WebAuthContext(is_authenticated=False)` on every failure path rather than
# raising. A mutating route whose only dependency is this one, or which has no
# `require_*`/`optional_*` dependency at all, is reachable by an anonymous
# caller. That is a different and larger finding than a coarse guard, so it is
# counted separately below instead of disappearing into WEB_BACKLOG's total.
NO_AUTHORIZATION_GUARDS = frozenset({"optional_web_auth"})

# ---------------------------------------------------------------------------
# Backlog allowlist — GRANDFATHERED, NOT REVIEWED-AND-CORRECT.
#
# These are the finance web modules that have not yet been decomposed into
# granular permissions. Every number below is a mutating route whose only
# guard is module visibility: exactly the defect ADR-0006 names, still live.
# Nobody has confirmed any individual route here is acceptable — the count is
# a debt marker, not an approval.
#
# The ratchet is two-directional on purpose. A rise means new debt. A fall
# without editing this table means the number moved for a reason nobody
# reviewed (a route deleted, renamed, or made unreachable) and the same
# reviewer who fixed the routes must lower the entry in the same change, so
# that "we fixed 12 routes" and "the number dropped by 12" are one statement.
#
# `ap.py` is absent because it is already decomposed (77 granular guards,
# held there by tests/finance/test_ap_route_permissions.py) — it is the shape
# the rest of this table is migrating towards. `payments.py`, `exp_limits.py`,
# `dashboard.py`, `help.py`, `lease.py` and `opening_balance.py` are absent
# because they are already clean.
# ---------------------------------------------------------------------------
FINANCE_BACKLOG: dict[str, int] = {
    "ar.py": 40,
    "automation.py": 17,
    "banking.py": 43,
    "exp.py": 5,
    "gl.py": 21,
    "import_export.py": 2,
    "quote.py": 7,
    "remita.py": 6,
    "reports.py": 1,
    "sales_order.py": 10,
    "settings.py": 8,
    "tax.py": 12,
}

# ---------------------------------------------------------------------------
# The rest of the web adapter — GRANDFATHERED, NOT REVIEWED-AND-CORRECT.
#
# Same defect, same semantics, same ratchet as FINANCE_BACKLOG above; a
# separate table because it is a separate, later-measured scope and because
# the finance decomposition is in flight against the one above. Keys are paths
# relative to `app/web/`, so nested modules keep their directory.
#
# 489 mutating routes across 41 files whose only authorization is module
# visibility. Nobody has confirmed that any individual route here is
# acceptable. These are not exemptions and this table is not an assessment: it
# is a measurement, taken so the debt is bounded and cannot grow silently.
#
# Absent because they are already granular, not because they were skipped:
# `app/web/fleet.py` (every mutating route carries a
# `require_fleet_<resource>_manage` built from `require_fleet_permissions`,
# which ANDs a real `fleet:<resource>:manage` permission onto module access),
# `app/web/people/weekly_meeting_reports.py`
# (`require_any_web_permission(["performance:weekly_reports:<action>"])`),
# and `app/web/admin.py` / `app/web/admin_sla_policies.py` (both mount their
# router with `dependencies=[Depends(require_admin_access)]`, an `is_admin`
# authority tier — see `_router_level_guards`).
#
# Decomposing these is a separate, prioritized program. See the 2026-08-25
# amendment in docs/adr/0006-module-access-is-not-write-authority.md.
# ---------------------------------------------------------------------------
WEB_BACKLOG: dict[str, int] = {
    "admin_crm_sync.py": 5,
    "admin_dotmac_sub_sync.py": 3,
    "careers.py": 4,
    "help.py": 6,
    "inventory.py": 31,
    "notifications.py": 2,
    "onboarding_portal.py": 1,
    "people/attendance.py": 10,
    "people/hr/competencies.py": 2,
    "people/hr/discipline.py": 12,
    "people/hr/employee_extended.py": 11,
    "people/hr/employees.py": 11,
    "people/hr/handbook.py": 4,
    "people/hr/info_changes.py": 4,
    "people/hr/job_descriptions.py": 6,
    "people/hr/lifecycle.py": 10,
    "people/hr/locations.py": 4,
    "people/hr/onboarding_admin.py": 5,
    "people/hr/organization.py": 9,
    "people/hr/positions.py": 4,
    "people/hr/skills.py": 3,
    "people/import_export.py": 2,
    "people/leave.py": 17,
    "people/payroll.py": 35,
    "people/perf.py": 44,
    "people/pms.py": 60,
    "people/recruit.py": 23,
    "people/scheduling.py": 10,
    "people/self_service.py": 17,
    "people/settings.py": 3,
    "people/training.py": 50,
    "procurement.py": 6,
    "profile.py": 2,
    "projects.py": 39,
    "public_sector/appropriations.py": 2,
    "public_sector/commitments.py": 1,
    "public_sector/funds.py": 1,
    "public_sector/virements.py": 3,
    "settings.py": 1,
    "support.py": 23,
    "workflow_tasks.py": 3,
}

# ---------------------------------------------------------------------------
# The severe subset of WEB_BACKLOG: mutating routes an ANONYMOUS caller
# reaches. Counted per file, and a strict subset of the entry above it.
#
# `admin_crm_sync.py` (mounted at `/admin/sync/crm` whenever the `crm` module
# is enabled) and `admin_dotmac_sub_sync.py` (prefix `/admin/sync/dotmac-sub`,
# currently imported by nothing, so unreachable until somebody mounts it) both
# guard every route with `optional_web_auth` and neither router carries a
# `dependencies=` list. Unlike `admin.py` next to them, nothing supplies
# `require_admin_access`. These are NOT grandfathered-acceptable: they save
# integration credentials and mint and revoke API keys.
#
# `careers.py` and `onboarding_portal.py` are deliberately public portals and
# their premise is enforceable and stated: the four careers routes are a job
# application, an application-status email request, and offer accept/decline —
# the first two are anonymous intake behind a captcha and a rate limit, and
# the last two plus the onboarding task completion authorize by possession of
# an unguessable per-record token checked in the handler (`get_offer_by_token`
# / `_require_valid_token`), which is bearer-of-secret authority rather than
# session authority. They are listed so the count is honest, not because they
# are wrong.
# ---------------------------------------------------------------------------
UNAUTHENTICATED_WEB_MUTATIONS: dict[str, int] = {
    "admin_crm_sync.py": 5,
    "admin_dotmac_sub_sync.py": 3,
    "careers.py": 4,
    "onboarding_portal.py": 1,
}

# ---------------------------------------------------------------------------
# Job (2) — "every guarded permission is real" — measured over the widened
# scope. GRANDFATHERED, NOT REVIEWED-AND-CORRECT.
#
# `app/web/inventory.py`'s material-request routes are the mirror image of the
# rest of this file: they DO carry granular guards, and the names those guards
# ask for are not in `scripts/seed_rbac.py`'s catalogue, so no role can be
# granted them. `has_permission` short-circuits on `auth.is_admin`, so the
# effect is that six material-request routes plus three of their forms are
# admin-only by accident rather than by decision, and every non-admin holder
# of an inventory role gets a 403 nobody chose.
#
# Two spellings appear because `require_any_web_permission` lists both an
# `inv:` and an `inventory:` form of the same act; neither exists. Values are
# the number of guard sites naming that permission.
#
# Fixing it means adding rows to the seeded catalogue and deciding which roles
# hold them, which is an authority grant and belongs in its own reviewed
# change — not in a guard-extension branch. Recorded here so it is monitored
# rather than merely known.
# ---------------------------------------------------------------------------
WEB_UNSEEDABLE_PERMISSIONS: dict[str, int] = {
    "inv:material_requests:approve": 2,
    "inv:material_requests:create": 5,
    "inv:material_requests:delete": 1,
    "inv:material_requests:submit": 2,
    "inventory:material_requests:approve": 2,
    "inventory:material_requests:delete": 1,
    "inventory:material_requests:submit": 2,
}


def _is_guard_name(name: str) -> bool:
    """A dependency that authorizes, as opposed to one that supplies a session.

    Matches `require_*` / `optional_*`, with or without a leading underscore —
    `app/web/finance/payments.py` defines a module-private
    `_require_expense_reimburse`, and a private guard is still a guard.
    """
    return name.lstrip("_").startswith(("require_", "optional_"))


def _dependency_names(node: ast.AST) -> list[str]:
    """Names passed to `Depends(...)` anywhere inside an argument default."""
    names: list[str] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        called = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else ""
        )
        if called != "Depends":
            continue
        for arg in sub.args:
            for inner in ast.walk(arg):
                if isinstance(inner, ast.Name):
                    names.append(inner.id)
                elif isinstance(inner, ast.Attribute):
                    names.append(inner.attr)
    return names


def _permission_strings(node: ast.AST) -> list[str]:
    """String literals handed to a permission-guard factory."""
    perms: list[str] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        called = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else ""
        )
        if called not in PERMISSION_GUARD_FACTORIES:
            continue
        for arg in sub.args:
            for inner in ast.walk(arg):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    perms.append(inner.value)
    return perms


def _router_methods(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    methods: set[str] = set()
    for decorator in func.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        target = decorator.func
        if not isinstance(target, ast.Attribute):
            continue
        value = target.value
        if isinstance(value, ast.Name) and value.id.endswith("router"):
            methods.add(target.attr.lower())
    return methods


def _argument_defaults(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    return [d for d in (*func.args.defaults, *func.args.kw_defaults) if d is not None]


def _routes(source: str) -> list[tuple[int, str, set[str], list[str], list[str]]]:
    """(lineno, name, http methods, guard names, permission strings) per route."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        methods = _router_methods(node)
        if not methods:
            continue
        guards: list[str] = []
        perms: list[str] = []
        for default in _argument_defaults(node):
            guards.extend(n for n in _dependency_names(default) if _is_guard_name(n))
            perms.extend(_permission_strings(default))
        found.append((node.lineno, node.name, methods, guards, perms))
    return found


def _router_level_guards(source: str) -> list[str]:
    """Guards applied to every route in the file by `APIRouter(dependencies=)`.

    A per-route dependency is not the whole story. `app/web/admin.py` declares
    `optional_web_auth` on all 35 of its mutating handlers and is nonetheless
    admin-only, because its router is constructed with
    `dependencies=[Depends(require_admin_access)]` — that runs first, for every
    route, and `require_admin_access` tests `auth.is_admin`. Reading only the
    argument defaults would report those 35 routes plus five more in
    `admin_sla_policies.py` as unauthorized, which is false.

    The reverse case is why this cannot simply exempt any file with a
    router-level dependency: `people/perf.py` and `people/pms.py` also declare
    one, and theirs (`require_private_performance_mode`,
    `require_government_pms_mode`) are visibility guards wearing a deployment
    mode — see MODULE_ACCESS_GUARDS. A router-level guard is folded into the
    route's guard set and then classified like any other.

    Only `APIRouter(...)` construction is read. `include_router(...,
    dependencies=[...])` in `app/main.py` adds guards too, but no HTML router
    is mounted that way — every occurrence there is on a JSON `/api` router —
    so following it would add reach without adding coverage.
    """
    guards: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else ""
        )
        if called != "APIRouter":
            continue
        for keyword in node.keywords:
            if keyword.arg != "dependencies":
                continue
            guards.extend(
                n for n in _dependency_names(keyword.value) if _is_guard_name(n)
            )
    return guards


def _visibility_only_mutations(source: str) -> list[str]:
    """Mutating routes whose only authorization is module visibility."""
    router_guards = _router_level_guards(source)
    offenders = []
    for lineno, name, methods, guards, _ in _routes(source):
        if not methods & MUTATING_METHODS:
            continue
        effective = [*guards, *router_guards]
        if effective and not all(g in MODULE_ACCESS_GUARDS for g in effective):
            continue
        held = ",".join(sorted(set(effective))) or "no guard at all"
        offenders.append(f"{lineno}:{name} ({held})")
    return offenders


def _unauthenticated_mutations(source: str) -> list[str]:
    """The subset of the above that no authenticated principal is needed for.

    A route qualifies when its effective guard set is empty or contains
    nothing but NO_AUTHORIZATION_GUARDS.
    """
    router_guards = _router_level_guards(source)
    offenders = []
    for lineno, name, methods, guards, _ in _routes(source):
        if not methods & MUTATING_METHODS:
            continue
        effective = {*guards, *router_guards}
        if effective - NO_AUTHORIZATION_GUARDS:
            continue
        held = ",".join(sorted(effective)) or "no guard at all"
        offenders.append(f"{lineno}:{name} ({held})")
    return offenders


# ---------------------------------------------------------------------------
# (a) Fixed Assets: no mutating route may rest on module visibility alone
# ---------------------------------------------------------------------------


def test_no_mutating_fixed_assets_route_rests_on_module_visibility() -> None:
    offenders = _visibility_only_mutations(FIXED_ASSETS.read_text(encoding="utf-8"))
    assert offenders == [], (
        "these mutating Fixed Assets routes are authorized by module "
        "visibility alone:\n  " + "\n  ".join(offenders) + "\n\n"
        "`require_fixed_assets_access` admits `fa:access`, which every "
        "read-only asset role holds. Add "
        '`require_web_permission("fa:<resource>:<action>")`, reusing the name '
        "the JSON API in app/api/fixed_assets/ already enforces for the same "
        "act. See docs/adr/0006-module-access-is-not-write-authority.md."
    )


def test_fixed_assets_still_has_mutating_routes_to_protect() -> None:
    """Guards against the rule above passing because the routes went away."""
    mutating = [
        name
        for _, name, methods, _, _ in _routes(FIXED_ASSETS.read_text(encoding="utf-8"))
        if methods & MUTATING_METHODS
    ]
    assert len(mutating) >= 23, (
        f"expected the 23 mutating Fixed Assets routes ADR-0006 decomposed, "
        f"found {len(mutating)}: {sorted(mutating)}"
    )


# ---------------------------------------------------------------------------
# (b) Every guarded permission exists in the seeded catalogue
# ---------------------------------------------------------------------------


def _web_modules() -> list[Path]:
    """Every Python module under `app/web/`, the full adapter surface."""
    return sorted(WEB_ROOT.rglob("*.py"))


def _web_backlog_modules() -> list[Path]:
    """`_web_modules()` minus the two scopes with their own rule above.

    Fixed Assets is decomposed and held at zero by rule (a); `app/web/finance`
    is ratcheted by FINANCE_BACKLOG. Excluding them here keeps every mutating
    route in exactly one table, so the totals add up rather than overlap.
    """
    return [
        path
        for path in _web_modules()
        if path != FIXED_ASSETS and FINANCE_WEB not in path.parents
    ]


def _guarded_permissions(paths: list[Path]) -> dict[str, set[str]]:
    by_permission: dict[str, set[str]] = {}
    for path in paths:
        for lineno, name, _, _, perms in _routes(path.read_text(encoding="utf-8")):
            for perm in perms:
                site = f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno} {name}"
                by_permission.setdefault(perm, set()).add(site)
    return by_permission


@pytest.mark.parametrize(
    "paths",
    [
        pytest.param([FIXED_ASSETS], id="fixed_assets"),
        pytest.param(sorted(FINANCE_WEB.glob("*.py")), id="finance"),
    ],
)
def test_every_guarded_permission_is_seedable(paths: list[Path]) -> None:
    catalogue = {key for key, _ in DEFAULT_PERMISSIONS}
    unknown = {
        perm: sorted(sites)
        for perm, sites in _guarded_permissions(paths).items()
        if perm not in catalogue
    }
    assert unknown == {}, (
        "these route guards name permissions scripts/seed_rbac.py never "
        "defines, so no role can ever hold them and the routes are "
        f"unreachable rather than protected:\n  {unknown}"
    )


# ---------------------------------------------------------------------------
# (c) No guard requests a wildcard
# ---------------------------------------------------------------------------


def test_no_route_guard_requests_a_wildcard_permission() -> None:
    """A wildcard is HELD, never REQUESTED (ADR-0006 decision 3).

    Scoped to the whole web adapter since 2026-08-25. It passes there with no
    allowlist: `fa:*` really was the only requested wildcard in `app/web/**`.
    """
    paths = _web_modules()
    offenders = {
        perm: sorted(sites)
        for perm, sites in _guarded_permissions(paths).items()
        if "*" in perm
    }
    assert offenders == {}, (
        "these guards ask for a wildcard permission:\n  "
        f"{offenders}\n\n"
        "`has_permission` matches a requested `X:*` literally, so such a "
        "guard denies everyone; before ADR-0006 it admitted the entire `X` "
        "subtree, which is how `fa:*` came to mean `fa:anything`. Name the "
        "action, or list several with require_any_web_permission."
    )


def test_the_wildcard_matcher_is_one_directional() -> None:
    """The held/requested asymmetry the (c) rule depends on, asserted directly.

    Without this, `test_no_route_guard_requests_a_wildcard_permission` could
    keep passing while somebody restored the inverted match in deps.py.
    """
    from app.web.deps import WebAuthContext

    holder = WebAuthContext(
        is_authenticated=True,
        roles=["asset_viewer"],
        scopes=["fa:access", "fa:assets:read"],
    )
    assert holder.has_permission("fa:assets:read") is True
    assert holder.has_permission("fa:*") is False, (
        "a requested `fa:*` must not be satisfied by a held `fa:assets:read` — "
        "that inversion is the ADR-0006 defect"
    )
    assert holder.has_permission("fa:assets:dispose") is False

    wildcard_holder = WebAuthContext(
        is_authenticated=True,
        roles=["asset_manager"],
        scopes=["fa:*"],
    )
    assert wildcard_holder.has_permission("fa:assets:dispose") is True, (
        "a HELD `fa:*` must still grant the whole subtree"
    )
    assert wildcard_holder.has_permission("gl:journals:post") is False


# ---------------------------------------------------------------------------
# (d) Sensitivity — the detector must fire on a planted violation
# ---------------------------------------------------------------------------


_CLEAN_ROUTER = """
from fastapi import Depends
from app.web.deps import require_fixed_assets_access, require_web_permission

@router.get("/things")
def list_things(auth=Depends(require_fixed_assets_access)):
    ...

@router.post("/things")
def create_thing(auth=Depends(require_web_permission("fa:assets:create"))):
    ...
"""

_PLANTED_VIOLATION = """
@router.post("/things/{thing_id}/dispose")
def dispose_thing(thing_id: str, auth=Depends(require_fixed_assets_access)):
    ...
"""


def test_the_detector_fires_on_a_planted_violation() -> None:
    """A mutating route guarded by visibility alone must be reported..."""
    offenders = _visibility_only_mutations(_CLEAN_ROUTER + _PLANTED_VIOLATION)
    assert len(offenders) == 1, offenders
    assert "dispose_thing" in offenders[0]
    assert "require_fixed_assets_access" in offenders[0]


def test_the_detector_goes_quiet_when_the_violation_is_removed() -> None:
    """...and removing it must go quiet. A rule that never fires proves
    nothing, and a rule that always fires is not a rule."""
    assert _visibility_only_mutations(_CLEAN_ROUTER) == []


def test_the_detector_fires_on_a_mutating_route_with_no_guard_at_all() -> None:
    assert _visibility_only_mutations(
        '\n@router.delete("/things/{thing_id}")\ndef drop_thing(thing_id: str):\n    ...\n'
    ) == ["3:drop_thing (no guard at all)"]


def test_the_detector_ignores_reads_and_private_guards() -> None:
    """GET routes may rest on visibility, and a module-private guard that does
    a real permission check (payments.py's `_require_expense_reimburse`) is a
    guard, not a visibility gate."""
    assert (
        _visibility_only_mutations(
            '\n@router.get("/things")\ndef read_things(auth=Depends(require_finance_access)):\n    ...\n'
            '\n@router.post("/things")\ndef write_thing(auth=Depends(_require_expense_reimburse)):\n    ...\n'
        )
        == []
    )


def test_the_permission_extractor_actually_extracts() -> None:
    """(b) and (c) both pass vacuously if no permission string is ever seen."""
    routes = _routes(_CLEAN_ROUTER)
    perms = {p for *_, found in routes for p in found}
    assert perms == {"fa:assets:create"}


# ---------------------------------------------------------------------------
# The finance backlog ratchet
# ---------------------------------------------------------------------------


def _observed_finance_backlog() -> dict[str, int]:
    observed: dict[str, int] = {}
    for path in sorted(FINANCE_WEB.glob("*.py")):
        count = len(_visibility_only_mutations(path.read_text(encoding="utf-8")))
        if count:
            observed[path.name] = count
    return observed


def test_the_finance_backlog_is_a_two_directional_ratchet() -> None:
    observed = _observed_finance_backlog()
    recorded = FINANCE_BACKLOG

    grew = {f: observed[f] for f in observed if observed[f] > recorded.get(f, 0)}
    shrank = {
        f: observed.get(f, 0) for f in recorded if observed.get(f, 0) < recorded[f]
    }

    assert observed == recorded, (
        "the app/web/finance module-visibility backlog moved.\n"
        f"  grew (new debt, forbidden): {grew}\n"
        f"  shrank (lower FINANCE_BACKLOG in the same change): {shrank}\n\n"
        "A new mutating finance route must carry a granular "
        "require_web_permission guard — ap.py is the worked example. When you "
        "decompose a module, edit FINANCE_BACKLOG so the fix and the number "
        "land together. These entries are grandfathered, not "
        "reviewed-and-correct: see "
        "docs/adr/0006-module-access-is-not-write-authority.md."
    )


def test_the_finance_backlog_names_only_files_that_exist() -> None:
    """A ratchet against a deleted file silently stops ratcheting."""
    missing = [name for name in FINANCE_BACKLOG if not (FINANCE_WEB / name).is_file()]
    assert missing == [], f"FINANCE_BACKLOG names missing files: {missing}"


def test_fixed_assets_is_not_in_the_finance_backlog() -> None:
    """Fixed Assets is fixed, not grandfathered — it must never acquire a row
    here as a way of getting a regression past the (a) rule."""
    assert "fixed_assets.py" not in FINANCE_BACKLOG


# ---------------------------------------------------------------------------
# Sensitivity for the widened scope
#
# A ratchet that has never been shown to bite on the surface it newly claims
# to cover is not evidence. Each proof below is two-sided: the planted defect
# is reported, and the corrected version goes quiet.
# ---------------------------------------------------------------------------


_CLEAN_PEOPLE_ROUTER = """
from fastapi import APIRouter, Depends
from app.web.deps import require_hr_access, require_web_permission

router = APIRouter(prefix="/people/hr/employees")

@router.get("")
def list_employees(auth=Depends(require_hr_access)):
    ...

@router.post("/{employee_id}/terminate")
def terminate(employee_id: str, auth=Depends(require_web_permission("hr:employees:terminate"))):
    ...
"""

_PLANTED_PEOPLE_VIOLATION = """
@router.post("/{employee_id}/salary")
def change_salary(employee_id: str, auth=Depends(require_hr_access)):
    ...
"""


def test_the_detector_fires_outside_finance_and_fixed_assets() -> None:
    """The widened scope's own two-sided proof, half one.

    `require_hr_access` is `has_module_access("people")` and nothing else, so
    a mutating People route holding it alone is the ADR-0006 defect in the
    module the widened scope exists to reach.
    """
    offenders = _visibility_only_mutations(
        _CLEAN_PEOPLE_ROUTER + _PLANTED_PEOPLE_VIOLATION
    )
    assert len(offenders) == 1, offenders
    assert "change_salary" in offenders[0]
    assert "require_hr_access" in offenders[0]


def test_the_detector_goes_quiet_outside_finance_when_the_route_is_granular() -> None:
    """...and half two."""
    assert _visibility_only_mutations(_CLEAN_PEOPLE_ROUTER) == []


_ADMIN_STYLE_ROUTER = """
from fastapi import APIRouter, Depends
from app.web.deps import optional_web_auth, require_admin_access

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin_access)])

@router.post("/settings/paystack")
def update_paystack(auth=Depends(optional_web_auth)):
    ...
"""

_UNMOUNTED_ADMIN_STYLE_ROUTER = _ADMIN_STYLE_ROUTER.replace(
    ", dependencies=[Depends(require_admin_access)]", ""
)


def test_a_router_level_authority_guard_clears_its_routes() -> None:
    """`app/web/admin.py`'s shape: `optional_web_auth` per route, and a
    router-level `require_admin_access` that actually decides. Without this
    the widened scan would have reported 40 false positives, and a table of
    40 false positives is how a ratchet gets deleted."""
    assert _visibility_only_mutations(_ADMIN_STYLE_ROUTER) == []


def test_dropping_the_router_level_guard_is_detected() -> None:
    """The other half: `app/web/admin_crm_sync.py`'s shape — the same routes
    with nothing supplying the router-level guard. The detector must not
    credit a guard that is not there."""
    offenders = _visibility_only_mutations(_UNMOUNTED_ADMIN_STYLE_ROUTER)
    assert len(offenders) == 1, offenders
    assert "update_paystack" in offenders[0]
    assert "optional_web_auth" in offenders[0]


def test_a_router_level_visibility_guard_does_not_clear_its_routes() -> None:
    """`people/pms.py`'s shape. A router-level dependency is folded in and
    then classified; being router-level does not make it authority."""
    source = _ADMIN_STYLE_ROUTER.replace(
        "require_admin_access", "require_government_pms_mode"
    )
    offenders = _visibility_only_mutations(source)
    assert len(offenders) == 1, offenders
    assert "require_government_pms_mode" in offenders[0]


def test_the_unauthenticated_detector_separates_the_severe_case() -> None:
    """A coarse guard and no guard are not the same finding."""
    coarse = _visibility_only_mutations(_PLANTED_PEOPLE_VIOLATION)
    assert len(coarse) == 1
    assert _unauthenticated_mutations(_PLANTED_PEOPLE_VIOLATION) == []

    anonymous = _unauthenticated_mutations(_UNMOUNTED_ADMIN_STYLE_ROUTER)
    assert len(anonymous) == 1, anonymous
    assert "update_paystack" in anonymous[0]
    assert _unauthenticated_mutations(_ADMIN_STYLE_ROUTER) == []


def test_the_extended_module_access_guards_are_real_dependencies() -> None:
    """Every name added to MODULE_ACCESS_GUARDS on 2026-08-25 must still be
    defined somewhere under `app/web/`. A frozenset entry for a guard that has
    been renamed away silently stops classifying anything, and the routes it
    used to cover would leave the backlog without anyone deciding to."""
    added = {
        "require_discipline_cases_read",
        "require_discipline_cases_create",
        "require_discipline_cases_update",
        "require_discipline_workflow_manage",
        "require_self_service_discipline_manager",
        "require_private_performance_mode",
        "require_government_pms_mode",
    }
    defined: set[str] = set()
    for path in _web_modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)
    assert added <= defined, (
        f"MODULE_ACCESS_GUARDS names no-longer-defined guards: {sorted(added - defined)}"
    )


# ---------------------------------------------------------------------------
# The rest-of-web backlog ratchet
# ---------------------------------------------------------------------------


def _observed_web_backlog() -> dict[str, int]:
    observed: dict[str, int] = {}
    for path in _web_backlog_modules():
        count = len(_visibility_only_mutations(path.read_text(encoding="utf-8")))
        if count:
            observed[path.relative_to(WEB_ROOT).as_posix()] = count
    return observed


def test_the_web_backlog_is_a_two_directional_ratchet() -> None:
    observed = _observed_web_backlog()
    recorded = WEB_BACKLOG

    grew = {f: observed[f] for f in observed if observed[f] > recorded.get(f, 0)}
    shrank = {
        f: observed.get(f, 0) for f in recorded if observed.get(f, 0) < recorded[f]
    }

    assert observed == recorded, (
        "the app/web module-visibility backlog moved.\n"
        f"  grew (new debt, forbidden): {grew}\n"
        f"  shrank (lower WEB_BACKLOG in the same change): {shrank}\n\n"
        "A new mutating web route must carry a granular require_web_permission "
        "guard naming the act it authorizes — app/web/finance/ap.py and "
        "app/web/fixed_assets.py are the worked examples, and app/web/fleet.py "
        "shows the module-access-AND-permission shape. When you decompose a "
        "module, edit WEB_BACKLOG so the fix and the number land together. "
        "These entries are grandfathered, not reviewed-and-correct: see the "
        "2026-08-25 amendment in "
        "docs/adr/0006-module-access-is-not-write-authority.md."
    )


def test_the_unauthenticated_web_mutations_are_a_two_directional_ratchet() -> None:
    """The severe subset, ratcheted separately so it cannot be diluted by the
    489 coarse ones sitting above it."""
    observed: dict[str, int] = {}
    for path in _web_backlog_modules():
        count = len(_unauthenticated_mutations(path.read_text(encoding="utf-8")))
        if count:
            observed[path.relative_to(WEB_ROOT).as_posix()] = count

    assert observed == UNAUTHENTICATED_WEB_MUTATIONS, (
        "the set of mutating web routes reachable with NO authenticated "
        f"principal moved: {observed} vs {UNAUTHENTICATED_WEB_MUTATIONS}.\n\n"
        "A new one is never acceptable. Removing one means lowering "
        "UNAUTHENTICATED_WEB_MUTATIONS in the same change. The two admin sync "
        "routers in this table are a live defect, not a grandfathered "
        "shape — see the 2026-08-25 amendment in "
        "docs/adr/0006-module-access-is-not-write-authority.md."
    )


def test_the_unauthenticated_table_is_a_subset_of_the_web_backlog() -> None:
    """Every anonymous mutation is also a visibility-only mutation, so the two
    tables must agree about the files they share. If they ever disagree the
    severe count is being kept somewhere the headline number does not reach."""
    for name, count in UNAUTHENTICATED_WEB_MUTATIONS.items():
        assert name in WEB_BACKLOG, f"{name} is missing from WEB_BACKLOG"
        assert count <= WEB_BACKLOG[name], (
            f"{name}: {count} anonymous mutations recorded but WEB_BACKLOG "
            f"only records {WEB_BACKLOG[name]} visibility-only mutations"
        )


def test_the_web_backlog_names_only_files_that_exist() -> None:
    """A ratchet against a deleted file silently stops ratcheting."""
    missing = [name for name in WEB_BACKLOG if not (WEB_ROOT / name).is_file()]
    assert missing == [], f"WEB_BACKLOG names missing files: {missing}"


def test_the_web_backlog_does_not_overlap_the_finance_one() -> None:
    """One route, one table. Overlap would double-count the headline number
    and let a decomposition be claimed twice."""
    overlapping = [name for name in WEB_BACKLOG if name.startswith("finance/")]
    assert overlapping == [], f"WEB_BACKLOG reaches into finance: {overlapping}"
    assert "fixed_assets.py" not in WEB_BACKLOG


def test_the_web_scan_actually_reaches_the_whole_adapter() -> None:
    """The scan must be measuring the surface it claims to. A glob that
    silently stopped matching — a rename of `app/web`, a packaging change —
    would empty every table above and pass."""
    scanned = _web_backlog_modules()
    assert len(scanned) >= 55, f"only {len(scanned)} web modules scanned"
    relative = {p.relative_to(WEB_ROOT).as_posix() for p in scanned}
    for expected in ("people/pms.py", "people/hr/employees.py", "support.py"):
        assert expected in relative, f"{expected} is not being scanned"


def test_the_unseedable_web_permission_backlog_is_a_two_directional_ratchet() -> None:
    """Job (2) over the widened scope.

    Fixed Assets and finance are held at zero by
    `test_every_guarded_permission_is_seedable` above, which takes no
    allowlist. The rest of the adapter gets one, because the only way to empty
    it is to grant authority, and a guard-extension change must not do that.
    """
    catalogue = {key for key, _ in DEFAULT_PERMISSIONS}
    observed = {
        perm: len(sites)
        for perm, sites in _guarded_permissions(_web_backlog_modules()).items()
        if perm not in catalogue
    }

    assert observed == WEB_UNSEEDABLE_PERMISSIONS, (
        "the set of route guards naming permissions scripts/seed_rbac.py never "
        f"defines moved:\n  observed: {observed}\n  "
        f"recorded: {WEB_UNSEEDABLE_PERMISSIONS}\n\n"
        "A guard naming an unseeded permission is not protection — no role can "
        "hold it, so the route answers 403 to everyone except an admin, whose "
        "`is_admin` short-circuits `has_permission`. Seed the permission and "
        "grant it deliberately, then remove the row here in the same change."
    )
