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
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.seed_rbac import DEFAULT_PERMISSIONS

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXED_ASSETS = REPO_ROOT / "app" / "web" / "fixed_assets.py"
FINANCE_WEB = REPO_ROOT / "app" / "web" / "finance"

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
    }
)

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
#
# `gl.py` (21), `banking.py` (43) and `ar.py` (40) left this table together
# when their 104 mutating routes were decomposed into granular guards — the
# money-movement core, held by tests/finance/test_readonly_roles_cannot_mutate
# .py the same way ap.py is held by its own test.
# ---------------------------------------------------------------------------
FINANCE_BACKLOG: dict[str, int] = {
    "automation.py": 17,
    "exp.py": 5,
    "import_export.py": 2,
    "quote.py": 7,
    "remita.py": 6,
    "reports.py": 1,
    "sales_order.py": 10,
    "settings.py": 8,
    "tax.py": 12,
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


def _visibility_only_mutations(source: str) -> list[str]:
    """Mutating routes whose only authorization is module visibility."""
    offenders = []
    for lineno, name, methods, guards, _ in _routes(source):
        if not methods & MUTATING_METHODS:
            continue
        if guards and not all(g in MODULE_ACCESS_GUARDS for g in guards):
            continue
        held = ",".join(sorted(set(guards))) or "no guard at all"
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
    """A wildcard is HELD, never REQUESTED (ADR-0006 decision 3)."""
    paths = [FIXED_ASSETS, *sorted(FINANCE_WEB.glob("*.py"))]
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
