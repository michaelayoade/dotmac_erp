"""An unscoped script silently reads nothing once its tables acquire RLS.

ERP's RLS coverage is being added domain by domain. A script without
``app.current_organization_id`` evaluates the tenant predicate against NULL,
so a newly protected query returns zero rows without raising. Runtime code no
longer has a user-settable PostgreSQL bypass; only an explicit tenant scope is
valid here.

A batch job in that state does not fail. It processes nothing and reports
success. `allocate_splynx_fifo.py` allocates no payments; `post_unposted_ap_invoices.py`
posts no invoices; `reconcile_invoice_amount_paid.py` reconciles nothing. Every
one of them exits 0.

## Why a ratchet rather than a gate

The scripts listed in the baseline are in this state today. Failing the build
on all of them would make the check red from the first run, and a permanently
red check is one nobody reads. So `rls_scope_baseline.txt` records today's
list and this test fails only on movement:

  * a script that scopes nothing and is **not** in the baseline — a new one, or
    a regression;
  * a baseline entry that now **does** scope — the list must shrink or it
    outlives the problem;
  * a baseline entry whose file is **gone** — renamed or deleted.

The second rule is what makes it a ratchet rather than a permanent amnesty.

## What counts as scoping

Any use of `app.rls`'s tenant helpers: `tenant_context` /
`tenant_context_sync`, the direct scope setters used by infrastructure code,
or `prime_tenant_context`. Application-layer `allow_cross_org` is not a
PostgreSQL RLS scope and does not count.

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

# Tenant-scoping helpers. A user-settable PostgreSQL bypass is deliberately not
# part of this vocabulary.
_SCOPE_MARKERS = frozenset(
    {
        "tenant_context",
        "tenant_context_sync",
        "set_current_organization",
        "set_current_organization_sync",
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
        "Wrap the work in `tenant_context_sync(db, org_id)` or migrate it to "
        "the canonical per-organization session helper."
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


if (
    __name__ == "__main__"
):  # `python -m tests.architecture.test_script_rls_scope --update`
    if "--update" not in sys.argv:
        raise SystemExit("pass --update to rewrite the baseline")
    header = BASELINE.read_text(encoding="utf-8").split("\n")
    keep = [line for line in header if line.startswith("#")]
    BASELINE.write_text(
        "\n".join(keep + find_unscoped_scripts()) + "\n", encoding="utf-8"
    )
    # T201: this is the `--update` CLI path, not a test — a maintainer running
    # it needs to see the count. tests/** does not carry the T20 exemption that
    # scripts/** and tools/** do, and widening it would admit prints to every test.
    print(f"recorded {len(find_unscoped_scripts())} unscoped script(s)")  # noqa: T201
