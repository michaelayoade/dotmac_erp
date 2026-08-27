"""A read permission never authorizes a money-mutating route.

``app/api/finance/payments.py`` once guarded all five expense-reimbursement
routes with a single dependency that admitted on ``scopes.intersection([...])``
over a list containing ``payments:read``. One of those routes performs a real
Paystack ``POST /transfer``, so a read permission bought a disbursement. This
gate is what stops that shape from coming back.

The premise, stated so it is enforceable rather than aspirational:

    For every mounted route under ``/api/v1/payments`` whose method mutates,
    the authorization every guard on it accepts must be STATICALLY READABLE,
    and none of the permissions any of those guards accepts may be a read
    permission.

Two halves, both load-bearing:

* *readable* — a guard declares its admit set on the function object
  (``authorized_permissions``), or it is a ``require_tenant_permission("...")``
  closure whose key can be read out of the closure cell. A mutating payout
  route whose authorization cannot be read is UNMONITORED, not exempt, and
  fails here. Without this half the check would pass vacuously the moment
  someone wrote a guard the extractor does not understand.
* *no read permission* — deliberately conservative. FastAPI evaluates route
  dependencies conjunctively, so a permissive guard sitting beside a strict
  one cannot actually widen access; the gate rejects the read permission in
  ANY guard on such a route anyway, because "it is fine, the other dependency
  narrows it" is not a thing a reviewer should have to reconstruct from the
  dependency tree during an incident.

Exemptions are enumerated with the premise that makes each one true, and the
list is a ratchet: it may shrink, never grow, without a reviewed reason.

SCOPE DISCLOSURE: this gate covers the ``/api/v1/payments`` surface — the
routes that command the payment provider. Other mutating ERP surfaces (AP
payments, payroll, procurement) are NOT covered yet. Widening ``MONEY_PREFIX``
to a tuple of prefixes is the intended next step and is deliberately a
separate change, because every route pulled into scope must first have a
readable guard.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute

from tests.architecture import openapi_contract_lib as lib

MONEY_PREFIX = "/api/v1/payments"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# The trailing segment of a permission key that means "may look, not touch".
READ_SEGMENTS = frozenset({"read", "view", "list", "get", "show", "export"})

# Closure variable names used by this repository's permission-dependency
# factories (``require_tenant_permission``, ``require_permission``, ...).
_PERMISSION_FREEVARS = frozenset({"permission_key", "permission", "permission_keys"})


# Routes exempt from the "must have a readable guard" half. Each entry states
# the premise that makes the exemption true; the assertions below re-check
# that premise, so an exemption cannot outlive the reason for it.
UNAUTHENTICATED_BY_DESIGN = {
    # Paystack calls this; there is no principal to authorize. Authenticity is
    # the HMAC in X-Paystack-Signature, and org attribution is covered by
    # tests/architecture/test_webhook_org_attribution.py. Premise re-checked
    # below: the route takes NO authorization dependency at all and does take
    # the signature header.
    "POST /api/v1/payments/webhook/paystack",
}

# Routes that carry a read permission legitimately: they use a mutating HTTP
# method but write nothing, locally or at the provider. Each entry states why.
READ_ONLY_BODY_ROUTES = {
    # Asks Paystack for the account NAME behind an account number + bank code.
    # It is a POST only because those two values do not belong in a URL; it
    # persists nothing and changes nothing at Paystack. (Contrast
    # POST /initialize/expense, which creates a transfer recipient there.)
    "POST /api/v1/payments/resolve-account",
}


def _is_read_permission(permission: str) -> bool:
    """Is this permission key a read permission?

    Matches the trailing SEGMENT exactly. ``payments:read`` is a read
    permission; ``payments:reader:create`` is not, and no substring or prefix
    test is used — the defect this gate exists for was itself a sloppy
    membership test.
    """
    return re.split(r"[:.]", permission)[-1].lower() in READ_SEGMENTS


def _closure_permissions(call: object) -> frozenset[str] | None:
    """Read a permission key out of a ``require_*_permission(...)`` closure."""
    code = getattr(call, "__code__", None)
    closure = getattr(call, "__closure__", None)
    if code is None or not closure:
        return None
    for name, cell in zip(code.co_freevars, closure, strict=False):
        if name not in _PERMISSION_FREEVARS:
            continue
        try:
            value = cell.cell_contents
        except ValueError:  # pragma: no cover - cell not yet filled
            continue
        if isinstance(value, str):
            return frozenset({value})
        if isinstance(value, (list, tuple, set, frozenset)) and all(
            isinstance(item, str) for item in value
        ):
            return frozenset(value)
    return None


def _declared_permissions(call: object) -> frozenset[str] | None:
    """The admit set a dependency declares, or ``None`` if it declares none."""
    declared = getattr(call, "authorized_permissions", None)
    if declared is not None:
        return frozenset(declared)
    return _closure_permissions(call)


def _route_key(route: APIRoute, method: str) -> str:
    return f"{method} {route.path}"


def _walk_dependencies(route: APIRoute):
    """Every dependency on a route, including nested ones."""
    stack = list(route.dependant.dependencies)
    seen: set[int] = set()
    while stack:
        dependant = stack.pop()
        if id(dependant) in seen:
            continue
        seen.add(id(dependant))
        stack.extend(dependant.dependencies)
        yield dependant


def collect_money_route_permissions(app) -> dict[str, frozenset[str]]:
    """``{"POST /path": {permissions...}}`` for every mutating money route.

    A route with no dependency declaring any permission maps to an EMPTY set,
    which is the unmonitored case the caller asserts against.
    """
    found: dict[str, frozenset[str]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith(MONEY_PREFIX):
            continue
        for method in sorted(set(route.methods or ()) & MUTATING_METHODS):
            permissions: set[str] = set()
            for dependant in _walk_dependencies(route):
                declared = _declared_permissions(dependant.call)
                if declared is not None:
                    permissions |= declared
            found[_route_key(route, method)] = frozenset(permissions)
    return found


def find_violations(app) -> list[str]:
    """Every money route that is unmonitored or read-authorized."""
    violations: list[str] = []
    for key, permissions in sorted(collect_money_route_permissions(app).items()):
        if not permissions:
            if key not in UNAUTHENTICATED_BY_DESIGN:
                violations.append(
                    f"{key}: mutates money but no guard on it declares a "
                    f"permission — its authorization cannot be read, so it is "
                    f"unmonitored, not exempt"
                )
            continue
        if key in READ_ONLY_BODY_ROUTES:
            continue
        for permission in sorted(permissions):
            if _is_read_permission(permission):
                violations.append(
                    f"{key}: authorized by read permission {permission!r}"
                )
    return violations


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def full_app():
    return lib.build_full_app()


def test_no_money_mutating_route_is_authorized_by_a_read_permission(full_app) -> None:
    violations = find_violations(full_app)
    assert not violations, "money-mutating routes accept a read permission:\n  " + (
        "\n  ".join(violations)
    )


def test_the_payout_route_requires_exactly_the_transfer_permission(full_app) -> None:
    """The specific containment this gate was written for.

    ``POST /transfers/{intent_id}/initiate`` is the route where money leaves
    the account. Its total accepted permission set is ONE key. On the unfixed
    parent this set was seven keys wide and included ``payments:read``.
    """
    permissions = collect_money_route_permissions(full_app)
    key = "POST /api/v1/payments/transfers/{intent_id}/initiate"
    assert key in permissions, (
        f"{key} is not mounted — this gate is looking at the wrong surface"
    )
    assert permissions[key] == frozenset({"payments:transfer:initiate"})


# ---------------------------------------------------------------------------
# Sensitivity: the detector sees the real routes, and it bites when planted on
# ---------------------------------------------------------------------------


def test_the_detector_actually_sees_the_payments_routes(full_app) -> None:
    """A check over an empty or mis-globbed route set passes for the wrong
    reason. Pin the exact surface this gate inspects."""
    inspected = set(collect_money_route_permissions(full_app))
    assert inspected == {
        "POST /api/v1/payments/initialize/invoice",
        "POST /api/v1/payments/verify/{reference}",
        "POST /api/v1/payments/resolve-account",
        "POST /api/v1/payments/initialize/expense",
        "POST /api/v1/payments/expense-claims/{expense_claim_id}/reset-payment-intent",
        "POST /api/v1/payments/transfers/{intent_id}/initiate",
        "POST /api/v1/payments/webhook/paystack",
    }, (
        "the set of mutating /api/v1/payments routes changed. Classify the new "
        "route (does it move money or command the provider?), guard it on the "
        "right tier, then update this pin."
    )


def test_every_inspected_route_except_the_webhook_has_readable_authorization(
    full_app,
) -> None:
    """The 'readable' half, asserted directly rather than only as a side
    effect of the main gate."""
    permissions = collect_money_route_permissions(full_app)
    unreadable = {key for key, perms in permissions.items() if not perms}
    assert unreadable == UNAUTHENTICATED_BY_DESIGN


def test_the_webhook_exemption_premise_still_holds(full_app) -> None:
    """The webhook is exempt because it authorizes NOBODY — it verifies a
    Paystack HMAC instead. If it ever grows an authenticated principal, or
    loses the signature header, the exemption is void and this fails."""
    route = next(
        r
        for r in full_app.routes
        if isinstance(r, APIRoute) and r.path == "/api/v1/payments/webhook/paystack"
    )
    dependency_names = {
        getattr(d.call, "__name__", "") for d in _walk_dependencies(route)
    }
    assert "require_tenant_auth" not in dependency_names
    assert not any(_declared_permissions(d.call) for d in _walk_dependencies(route))
    header_names = set()
    for param in route.dependant.header_params:
        header_names.add(param.name.lower().replace("_", "-"))
        alias = getattr(param, "alias", None)
        if isinstance(alias, str):
            header_names.add(alias.lower().replace("_", "-"))
    assert "x-paystack-signature" in header_names


def test_read_permission_matcher_is_segment_exact() -> None:
    """The matcher must not repeat the defect it exists to catch."""
    assert _is_read_permission("payments:read")
    assert _is_read_permission("ap:payments:read")
    assert _is_read_permission("gl.journal.view")
    assert not _is_read_permission("payments:transfer:initiate")
    assert not _is_read_permission("payments:expense:initialize")
    # Substring/prefix traps: 'read' appears, but not as the trailing segment.
    assert not _is_read_permission("payments:reader:create")
    assert not _is_read_permission("payments:read:override")


def test_the_detector_bites_on_a_planted_read_authorized_money_route() -> None:
    """Plant the exact defect on a synthetic app and prove the gate fails.

    A gate that has never been observed to fail is not evidence of anything.
    """
    from fastapi import Depends, FastAPI

    def planted_guard():  # pragma: no cover - never invoked
        return {}

    planted_guard.authorized_permissions = frozenset(
        {"payments:read", "payments:transfer:initiate"}
    )

    app = FastAPI()

    @app.post(f"{MONEY_PREFIX}/transfers/{{intent_id}}/initiate")
    def _initiate(_auth=Depends(planted_guard)):  # pragma: no cover
        return {}

    violations = find_violations(app)
    assert any("payments:read" in v for v in violations), violations


def test_the_same_route_passes_once_the_read_permission_is_removed() -> None:
    """Remove the planted violation and the gate goes quiet — so it is
    reacting to the read permission and not to the synthetic app."""
    from fastapi import Depends, FastAPI

    def fixed_guard():  # pragma: no cover - never invoked
        return {}

    fixed_guard.authorized_permissions = frozenset({"payments:transfer:initiate"})

    app = FastAPI()

    @app.post(f"{MONEY_PREFIX}/transfers/{{intent_id}}/initiate")
    def _initiate(_auth=Depends(fixed_guard)):  # pragma: no cover
        return {}

    assert find_violations(app) == []


def test_the_detector_bites_on_an_unreadable_guard() -> None:
    """The other half: a mutating money route whose authorization cannot be
    read is reported as unmonitored rather than silently skipped."""
    from fastapi import Depends, FastAPI

    def opaque_guard():  # pragma: no cover - never invoked
        return {}

    app = FastAPI()

    @app.post(f"{MONEY_PREFIX}/some-new-payout")
    def _payout(_auth=Depends(opaque_guard)):  # pragma: no cover
        return {}

    violations = find_violations(app)
    assert any("unmonitored" in v for v in violations), violations


def test_the_detector_ignores_read_methods() -> None:
    """GET routes are out of scope — the gate is about mutation, and a read
    route authorized by a read permission is correct."""
    from fastapi import Depends, FastAPI

    def read_guard():  # pragma: no cover - never invoked
        return {}

    read_guard.authorized_permissions = frozenset({"payments:read"})

    app = FastAPI()

    @app.get(f"{MONEY_PREFIX}/banks")
    def _banks(_auth=Depends(read_guard)):  # pragma: no cover
        return []

    assert collect_money_route_permissions(app) == {}
    assert find_violations(app) == []
