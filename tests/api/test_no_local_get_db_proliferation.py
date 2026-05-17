"""CI guard against re-introducing per-module ``def get_db()`` in API
or web routes.

After the Bug A migration (see ``tests/api/test_deps_get_db_with_org.py``
and ``tests/web/test_deps_org_priming.py``), tenant-scoped routes must
use the canonical shared dependencies:

- ``app.api.deps.get_db_with_org`` — API routes (auto-commit, tenant-bound)
- ``app.web.deps.get_db_for_org`` — web routes (tenant-bound)

Per-module ``def get_db()`` definitions silently regressed tenant
isolation by yielding sessions without the PostgreSQL
``app.current_organization_id`` GUC set; this caused RLS-protected
SELECTs to return zero rows from authenticated routes that
``require_tenant_auth`` had already proven tenancy for.

A locked baseline is kept here so:
- adding a new ``def get_db()`` to a fresh module → test fails
- migrating one of the baseline modules → test fails (forces the dev to
  remove it from the baseline rather than silently leaving it as debt)
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"

# Files that legitimately define a ``get_db`` (or ``_get_db``) — these
# are the canonical session dependencies that everything else should
# import from.
_CANONICAL = {
    "app/db/__init__.py",
    "app/web/deps.py",
    "app/services/auth_dependencies.py",
    "app/api/deps.py",
}

# Per-module ``def get_db()`` definitions that pre-date the migration
# and have NOT yet been moved to the shared helper. Tracked explicitly
# so adding a new one or removing one from this list trips the test.
#
# Modules in this set DO carry the silent-empty-results risk if they
# query RLS-protected tables on the route's session. Future work should
# migrate them. Modules that have been validated safe (touch non-RLS
# schemas only) are still listed here — the goal is architectural
# consistency, not just risk mitigation.
_PRE_MIGRATION_BASELINE = {
    # ── Partial migrations (local get_db for the unauth webhook only) ──
    "app/api/crm.py",  # webhook_router stays on local get_db
    # ── Public/slug-resolved (RLS handled inside service) ──
    "app/api/careers.py",
    "app/web/careers.py",
    "app/web/onboarding_portal.py",
    # ── Tenant-scoped but retain a local ``get_db`` for an unauth
    # ── webhook route alongside ``get_db_with_org`` for the
    # ── authenticated routes. Partial migration — the silent-empty
    # ── results bug never applied (banking/payments schemas have no
    # ── RLS) so the unauth handler can keep its un-primed yielder. ──
    "app/api/finance/banking.py",  # banking.* no RLS; webhook = /banking/webhook/mono
    "app/api/finance/payments.py",  # payments.* no RLS; webhook = /payments/webhook/paystack
    # ── Legit cross-org ``_get_db`` retained for API-key lookup only ──
    # Routes themselves use ``get_db_with_service_org`` (defined locally)
    # which primes both layers from the require_service_auth org context.
    "app/api/sync/dotmac_crm.py",  # _get_db scoped to require_service_auth lookup
}

# Match both ``def get_db`` and ``def _get_db`` — the original regex
# missed the underscored variant, which hid two real RLS holes
# (app/api/sync/dotmac_crm.py::_get_db, app/api/finance/fx.py::_get_db)
# until the 2026-05-16 route-layer audit. The latter was fully migrated;
# the former retains its ``_get_db`` for the API-key lookup path
# (cross-org by necessity — see comment in baseline above).
_DEF_GET_DB = re.compile(r"^def _?get_db\b", re.MULTILINE)


def _scan_for_local_get_db() -> set[str]:
    """Return the set of module paths (repo-relative) that define their
    own ``def get_db()``, excluding canonical locations."""
    violations: set[str] = set()
    for py in APP_ROOT.rglob("*.py"):
        rel = py.relative_to(REPO_ROOT).as_posix()
        if rel in _CANONICAL:
            continue
        if _DEF_GET_DB.search(py.read_text(encoding="utf-8")):
            violations.add(rel)
    return violations


def test_no_unexpected_local_get_db_definitions():
    """The set of modules with a local ``def get_db()`` must match the
    locked baseline exactly. Drift in either direction is a test
    failure that needs human review.
    """
    actual = _scan_for_local_get_db()
    expected = _PRE_MIGRATION_BASELINE

    added = actual - expected
    removed = expected - actual

    msg_parts = []
    if added:
        msg_parts.append(
            "New ``def get_db()`` definitions appeared in:\n  - "
            + "\n  - ".join(sorted(added))
            + "\n→ Use ``Depends(get_db_with_org)`` from app.api.deps instead, "
            "or add the module to _PRE_MIGRATION_BASELINE with a comment "
            "explaining why a local ``get_db`` is justified."
        )
    if removed:
        msg_parts.append(
            "Modules previously in the baseline no longer define ``get_db()``:\n  - "
            + "\n  - ".join(sorted(removed))
            + "\n→ Remove them from _PRE_MIGRATION_BASELINE — this is the "
            "happy path; the baseline is shrinking."
        )

    assert not msg_parts, "\n\n".join(msg_parts)


def test_admin_bypass_dep_pairs_with_admin_gate():
    """``get_db_admin_bypass`` requires authenticated callers, but a
    future module could still pair it with no narrower authorization
    gate and silently expose cross-tenant data to any logged-in user.

    Any API module that uses ``Depends(get_db_admin_bypass)`` MUST
    also reference one of the recognized admin auth gates in the same
    file. Extend the allow-list when introducing a new admin gate.
    """
    admin_gate_patterns = (
        "require_audit_auth",
        "require_permission",
        "require_user_auth",  # auth_flow paths gate user identity first
        "require_admin_bypass",
    )

    violations = []
    api_root = APP_ROOT / "api"
    for py in api_root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "Depends(get_db_admin_bypass)" not in text:
            continue
        if not any(gate in text for gate in admin_gate_patterns):
            rel = py.relative_to(REPO_ROOT).as_posix()
            violations.append(rel)
    assert not violations, (
        "These modules use ``Depends(get_db_admin_bypass)`` without "
        "pairing it with an admin auth gate. The dep bypasses tenant "
        "scoping at *both* layers (PostgreSQL bypass_rls + Python "
        "allow_cross_org), so unauthenticated cross-tenant access is "
        "exactly what gets exposed. Add ``require_audit_auth`` or "
        "another gate from the allow-list:\n  - " + "\n  - ".join(sorted(violations))
    )


def test_no_unprimed_get_db_imports_in_api_modules():
    """Catch the loophole that hid ``coach.py`` and ``ipsas.py`` from the
    initial migration: a module that imports ``get_db`` (or aliases
    ``_get_db as get_db``) and uses it in ``Depends(get_db)`` slips past
    the ``def get_db`` scan but has the same silent-empty-results bug.

    Any API module that calls ``Depends(get_db)`` without defining
    ``def get_db`` locally MUST be in the baseline (because its local
    ``get_db`` is provided via import) OR has been migrated to
    ``Depends(get_db_with_org)``.
    """
    pat_depends_get_db = re.compile(r"\bDepends\(get_db\)")
    pat_def_get_db = re.compile(r"^def get_db\b", re.MULTILINE)

    violations = []
    api_root = APP_ROOT / "api"
    for py in api_root.rglob("*.py"):
        rel = py.relative_to(REPO_ROOT).as_posix()
        text = py.read_text(encoding="utf-8")
        if not pat_depends_get_db.search(text):
            continue
        if pat_def_get_db.search(text):
            # Defines its own get_db — baseline test handles this case.
            continue
        # Uses Depends(get_db) without defining it → must be importing it.
        # This is the coach.py / ipsas.py shape and should be migrated.
        if rel not in _PRE_MIGRATION_BASELINE:
            violations.append(rel)

    assert not violations, (
        "These modules use ``Depends(get_db)`` with an imported ``get_db`` "
        "(not a local def). They have the same silent-empty-results bug as "
        "the migrated modules — switch them to ``Depends(get_db_with_org)``:\n"
        "  - " + "\n  - ".join(sorted(violations))
    )


def test_migrated_modules_use_get_db_with_org():
    """Modules migrated in the Bug A fix must continue to depend on
    ``get_db_with_org`` for every authenticated query, never falling
    back to a freshly re-introduced local ``get_db`` or a plain
    ``Depends(_get_db)``.
    """
    migrated = {
        # Wave 1
        "app/api/me.py",
        "app/api/expense_limits.py",
        "app/api/people/leave.py",
        "app/api/procurement/rfqs.py",
        "app/api/procurement/vendors.py",
        "app/api/procurement/quotations.py",
        "app/api/procurement/evaluations.py",
        "app/api/procurement/contracts.py",
        # Wave 2 — direct migrations
        "app/api/finance/analysis.py",
        "app/api/finance/cons.py",
        "app/api/finance/gl.py",
        "app/api/finance/lease.py",
        "app/api/finance/rpt.py",
        "app/api/finance/tax.py",
        "app/api/fixed_assets/__init__.py",
        "app/api/inventory/__init__.py",
        "app/api/people/assets.py",
        "app/api/people/attendance.py",
        "app/api/people/discipline.py",
        "app/api/people/expense.py",
        "app/api/people/hr.py",
        "app/api/people/lifecycle.py",
        "app/api/people/payroll.py",
        "app/api/people/perf.py",
        "app/api/people/recruit.py",
        "app/api/people/scheduling.py",
        "app/api/people/training.py",
        "app/api/procurement/plans.py",
        "app/api/procurement/requisitions.py",
        # Wave 2 — ap_routes/ar_routes siblings (get_db formerly
        # re-exported through base.py; now use get_db_with_org directly)
        "app/api/finance/ap_routes/aging.py",
        "app/api/finance/ap_routes/invoices.py",
        "app/api/finance/ap_routes/goods_receipts.py",
        "app/api/finance/ap_routes/suppliers.py",
        "app/api/finance/ap_routes/payments.py",
        "app/api/finance/ap_routes/payment_batches.py",
        "app/api/finance/ap_routes/purchase_orders.py",
        "app/api/finance/ar_routes/customers.py",
        "app/api/finance/ar_routes/invoices.py",
        "app/api/finance/ar_routes/contracts.py",
        "app/api/finance/ar_routes/aging.py",
        "app/api/finance/ar_routes/receipts.py",
        "app/api/finance/ar_routes/credit_notes.py",
        # Wave 3 — tenant-scoped non-RLS hygiene migrations
        "app/api/coach.py",  # was importing get_db from app.web.deps
        "app/api/expense.py",
        "app/api/finance/ipsas.py",  # was aliasing _get_db as get_db
        "app/api/fleet/assignments.py",
        "app/api/fleet/documents.py",
        "app/api/fleet/fuel.py",
        "app/api/fleet/incidents.py",
        "app/api/fleet/maintenance.py",
        "app/api/fleet/reservations.py",
        "app/api/fleet/vehicles.py",
        "app/api/pm/milestones.py",
        "app/api/pm/projects.py",
        "app/api/pm/resources.py",
        "app/api/pm/tasks.py",
        "app/api/pm/time_entries.py",
        # Wave 4 — Category A non-tenant baseline that turned out to be
        # genuinely tenant-scoped (every route uses require_tenant_auth or
        # require_organization_id; just didn't fit the original scan).
        "app/api/files.py",
        "app/api/scheduler.py",
        "app/api/service_hooks.py",
        "app/api/support.py",
        "app/api/workflow_tasks.py",
        # Wave 4B — admin/cross-tenant via get_db_admin_bypass (genuine
        # cross-tenant access, app.bypass_rls + allow_cross_org).
        "app/api/audit.py",
        "app/api/auth.py",
        "app/api/persons.py",
        "app/api/rbac.py",
        "app/api/settings.py",
    }
    # Modules that legitimately use the admin-bypass variant instead of
    # the tenant-scoped one (genuine cross-tenant access).
    admin_bypass_modules = {
        "app/api/audit.py",
        "app/api/auth.py",
        "app/api/persons.py",
        "app/api/rbac.py",
        "app/api/settings.py",
    }
    regressions = []
    for rel in sorted(migrated):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        if _DEF_GET_DB.search(text):
            regressions.append(f"{rel}: re-introduced local ``def get_db()``")
        expected_dep = (
            "Depends(get_db_admin_bypass)"
            if rel in admin_bypass_modules
            else "Depends(get_db_with_org)"
        )
        if expected_dep not in text:
            regressions.append(f"{rel}: no longer uses {expected_dep}")
    assert not regressions, "\n".join(regressions)
