"""A script that opens a session without a scope reads NOTHING, and says so nowhere.

`dotmac_erp_app` is not a superuser and does not hold BYPASSRLS, and 87 tables
carry `FORCE ROW LEVEL SECURITY`. The policy on them is

    should_bypass_rls() OR organization_id = get_current_organization_id()

and `get_current_organization_id()` returns NULL when `app.current_organization_id`
is unset — via `current_setting(..., true)`, `NULLIF`, and a catch-all
`EXCEPTION WHEN OTHERS THEN RETURN NULL`. So `organization_id = NULL` is never
true, and the query returns zero rows **without raising**.

Measured on production 2026-08-10, same session, same role, same table:

    SET LOCAL ROLE dotmac_erp_app;
    SELECT count(*) FROM people;                       -->     0
    -- then, with app.current_organization_id set:
    SELECT count(*) FROM people;                       -->   569

A batch job in that state does not fail. It processes nothing and reports
success. `allocate_splynx_fifo.py` allocates no payments; `post_unposted_ap_invoices.py`
posts no invoices; `reconcile_invoice_amount_paid.py` reconciles nothing. Every
one of them exits 0.

## Why a ratchet rather than a gate

Twenty-nine scripts are in this state today. Failing the build on all of them
would make the check red from the first run, and a permanently red check is one
nobody reads. So `rls_scope_baseline.txt` records today's list and this test
fails only on movement:

  * a script that scopes nothing and is **not** in the baseline — a new one, or
    a regression;
  * a baseline entry that now **does** scope — the list must shrink or it
    outlives the problem;
  * a baseline entry whose file is **gone** — renamed or deleted.

The second rule is what makes it a ratchet rather than a permanent amnesty.

## What counts as scoping

Any use of `app.rls`'s own helpers: `tenant_context`/`tenant_context_sync` to
scope, or `bypass_rls`/`enable_rls_bypass` to opt out deliberately. **Bypassing
counts as passing** — an explicit bypass is a decision on the record, which is
the thing the silent case lacks.

This is a static check and can only see the script's own source. A script that
scopes indirectly, through a service that sets the context for it, will look
unscoped here. That is the conservative direction: it over-reports rather than
missing one, and an entry proven safe can be removed with a comment saying why.
"""

from __future__ import annotations

import ast
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
BASELINE = pathlib.Path(__file__).with_name("rls_scope_baseline.txt")

# `app.rls`'s public helpers. Scoping and bypassing both count: the failure this
# guards against is doing NEITHER.
_SCOPE_MARKERS = frozenset(
    {
        "tenant_context",
        "tenant_context_sync",
        "bypass_rls",
        "bypass_rls_sync",
        "set_current_organization",
        "set_current_organization_sync",
        "enable_rls_bypass",
        "enable_rls_bypass_sync",
        "prime_tenant_context",
    }
)


def _referenced_names(tree: ast.AST) -> set[str]:
    """Every bare name and attribute in the module.

    AST rather than a text search on purpose: a mention in a comment or a
    docstring must not count as scoping, and `db.SessionLocal` must count as
    using it.
    """
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    return names


def find_unscoped_scripts() -> list[str]:
    """Repo-relative paths of scripts that open a session and never scope it."""
    unscoped: list[str] = []
    for path in sorted(SCRIPTS_DIR.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue  # not ours to police here
        names = _referenced_names(tree)
        if "SessionLocal" not in names:
            continue
        if names & _SCOPE_MARKERS:
            continue
        unscoped.append(f"scripts/{path.name}")
    return unscoped


def _read_baseline() -> set[str]:
    return {
        line.strip()
        for line in BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def test_no_new_unscoped_scripts() -> None:
    """A new script that reads nothing must not be able to ship quietly."""
    current = set(find_unscoped_scripts())
    baseline = _read_baseline()
    new = sorted(current - baseline)
    assert not new, (
        "These scripts open a SessionLocal and never set an organization scope "
        "or an explicit bypass. Under FORCE RLS they will read zero rows and "
        "exit 0:\n  " + "\n  ".join(new) + "\n\n"
        "Wrap the work in `tenant_context_sync(db, org_id)`, or `bypass_rls_sync(db)` "
        "if it is genuinely cross-organization."
    )


def test_baseline_has_not_gone_stale() -> None:
    """A fixed script must leave the list, or the list outlives the problem."""
    current = set(find_unscoped_scripts())
    baseline = _read_baseline()
    now_scoped = sorted(baseline - current - _missing_files(baseline))
    assert not now_scoped, (
        "These scripts now scope their session and must be removed from "
        "rls_scope_baseline.txt:\n  " + "\n  ".join(now_scoped)
    )


def _missing_files(baseline: set[str]) -> set[str]:
    return {rel for rel in baseline if not (REPO_ROOT / rel).is_file()}


def test_baseline_entries_still_exist() -> None:
    """A renamed or deleted script leaves a hole for its replacement to fall into."""
    gone = sorted(_missing_files(_read_baseline()))
    assert not gone, (
        "These baseline entries no longer exist and must be removed:\n  "
        + "\n  ".join(gone)
    )


if __name__ == "__main__":  # `python -m tests.architecture.test_script_rls_scope --update`
    if "--update" not in sys.argv:
        raise SystemExit("pass --update to rewrite the baseline")
    header = BASELINE.read_text(encoding="utf-8").split("\n")
    keep = [line for line in header if line.startswith("#")]
    BASELINE.write_text("\n".join(keep + find_unscoped_scripts()) + "\n", encoding="utf-8")
    print(f"recorded {len(find_unscoped_scripts())} unscoped script(s)")
