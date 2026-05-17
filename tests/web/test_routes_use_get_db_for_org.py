"""Regression lock for the PR #85 web migration.

PR #83 migrated 49 web modules to ``Depends(get_db_for_org)``. PR #85
filled the gap left by #83's audit script (10 more modules; 317 sites).
This test locks the property in: a future PR that adds a new route to
any of these 10 modules but wires it to bare ``Depends(get_db)`` will
fail this test rather than silently shipping a route that returns empty
rows once ``ENFORCE_ORG_FILTER`` flips on.

``get_db_for_org`` carries a 403 guard (PR #80, commit 58f7e55f) that
raises if ``auth.organization_id`` is missing. Routes wired to bare
``get_db`` bypass that guard. The point of this test is to prevent the
bypass from creeping back in via copy-paste from one of the few modules
that legitimately retain ``get_db`` (login, public webhooks, etc.).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Modules where every route is org-scoped and must use get_db_for_org.
# Migrated by PR #85.
_FULLY_MIGRATED = (
    "app/web/finance/exp.py",
    "app/web/finance/exp_limits.py",
    "app/web/fixed_assets.py",
    "app/web/fleet.py",
    "app/web/people/hr/discipline.py",
    "app/web/people/self_service.py",
    "app/web/projects.py",
    "app/web/settings.py",
    "app/web/support.py",
)


def test_migrated_web_modules_use_get_db_for_org_not_bare_get_db():
    """Every Depends(...) for a db session in the 9 fully-migrated
    modules must reference get_db_for_org. Any bare Depends(get_db)
    means a new route slipped in without the org guard."""
    violations: list[tuple[str, list[tuple[int, str]]]] = []
    for rel in _FULLY_MIGRATED:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        offending: list[tuple[int, str]] = []
        for i, line in enumerate(text.splitlines(), 1):
            if "Depends(get_db)" in line:
                offending.append((i, line.strip()))
        if offending:
            violations.append((rel, offending))
    assert not violations, (
        "These modules must use Depends(get_db_for_org), not Depends(get_db). "
        "If a route legitimately cannot use the org-guarded dep, move the "
        f"file out of _FULLY_MIGRATED and document why.\n\n{violations}"
    )


def test_payments_module_keeps_exactly_two_documented_get_db_sites():
    """``app/web/finance/payments.py`` keeps exactly two ``Depends(get_db)``
    sites by design:

    1. The ``_require_expense_reimburse`` helper, which has an
       ``if auth.is_admin: return auth`` branch that fires before the
       org check — migrating it would 403 cross-org admins doing
       reimbursement workflows.
    2. The Paystack ``/callback`` webhook, which is unauthenticated by
       design.

    Adding a third ``Depends(get_db)`` site here is almost certainly a
    mistake (the new route should use ``get_db_for_org``). Removing one
    of the two would change behaviour that the commit message of #85
    explicitly documented; this test forces the dev to re-justify.
    """
    text = (REPO_ROOT / "app/web/finance/payments.py").read_text(encoding="utf-8")
    sites = [
        (i, line.strip())
        for i, line in enumerate(text.splitlines(), 1)
        if "Depends(get_db)" in line
    ]
    assert len(sites) == 2, (
        "app/web/finance/payments.py must have exactly 2 Depends(get_db) "
        "sites (the _require_expense_reimburse helper and the Paystack "
        f"/callback webhook). Found {len(sites)}:\n"
        + "\n".join(f"  line {i}: {line}" for i, line in sites)
    )
