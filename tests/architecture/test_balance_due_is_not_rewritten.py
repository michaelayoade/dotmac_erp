"""`total_amount - amount_paid` has one definition, and the count only shrinks.

`balance_due` was a Python `@property`, so it could not appear in a WHERE, an
ORDER BY or a SUM. Callers therefore wrote the subtraction out by hand — **75
occurrences across ~30 files** when this was measured. Every one is a place
the definition can drift, and ADR-0016 is about exactly that class of
duplication one level up (coverage stored as a status).

ADR-0016 stage 1 makes `balance_due` a generated column, so the expression now
has a single home that the ORM and SQL share. Converting 75 call sites in one
change would be unreviewable, so this is a RATCHET rather than a ban: the
count is recorded, it may only go down, and a new hand-written copy fails the
build.

Same shape as `scripts/session_context_legacy.txt` and the RLS coverage
ratchet — the pattern that has worked for every other bounded backlog in this
repo.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP = REPO_ROOT / "app"

# `X.total_amount - X.amount_paid` in either the ORM-expression form
# (`Invoice.total_amount - Invoice.amount_paid`) or the instance form
# (`inv.total_amount - inv.amount_paid`), plus the bare local-variable form.
_HANDWRITTEN = re.compile(
    r"total_amount\s*-\s*(?:[A-Za-z_][A-Za-z0-9_]*\.)?amount_paid"
)

# The two places the expression legitimately appears: the generated-column
# definitions themselves.
_OWNERS = {
    "app/models/finance/ar/invoice.py",
    "app/models/finance/ap/supplier_invoice.py",
}

# Measured 2026-08-11, after ADR-0016 stage 1 converted the four coach
# analyzer copies. LOWER THIS as call sites move onto the column. It must
# never rise: a new hand-written copy is a new place the definition can drift.
MAX_HANDWRITTEN = 72


def _handwritten_sites() -> list[str]:
    sites: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in _OWNERS:
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.lstrip().startswith("#"):
                continue
            if _HANDWRITTEN.search(line):
                sites.append(f"{relative}:{number}")
    return sites


def test_the_count_only_shrinks():
    sites = _handwritten_sites()
    assert len(sites) <= MAX_HANDWRITTEN, (
        f"{len(sites)} hand-written `total_amount - amount_paid` sites, "
        f"ratchet allows {MAX_HANDWRITTEN}.\n"
        "Use the `balance_due` generated column (ADR-0016) instead of writing "
        "the subtraction again:\n  " + "\n  ".join(sites[:15])
    )


def test_the_ratchet_is_not_stale():
    """If the count has dropped, lower MAX_HANDWRITTEN in the same change.
    A ratchet that records more debt than exists stops applying pressure."""
    count = len(_handwritten_sites())
    assert count == MAX_HANDWRITTEN, (
        f"{count} sites remain but the ratchet still allows {MAX_HANDWRITTEN} — "
        f"set MAX_HANDWRITTEN to {count} to record the progress."
    )


def test_the_detector_actually_matches_both_forms():
    """Sensitivity proof: the count above is not passing because the pattern
    stopped matching."""
    assert _HANDWRITTEN.search("Invoice.total_amount - Invoice.amount_paid")
    assert _HANDWRITTEN.search("inv.total_amount - inv.amount_paid")
    assert _HANDWRITTEN.search("total_amount - amount_paid")
    assert not _HANDWRITTEN.search("total_amount - discount_amount")


def test_the_generated_column_is_the_single_owner():
    from sqlalchemy import inspect as sa_inspect

    from app.models.finance.ap.supplier_invoice import SupplierInvoice
    from app.models.finance.ar.invoice import Invoice

    for model in (Invoice, SupplierInvoice):
        column = sa_inspect(model).columns["balance_due"]
        assert column.computed is not None, f"{model.__name__} is not generated"
        assert column.computed.persisted, "must be STORED, not VIRTUAL"


def test_balance_due_is_not_writable_by_application_code():
    """A generated column has no writer. If something assigns to it, the
    database rejects the write — but catching it here is cheaper."""
    assignments = []
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\.balance_due\s*=[^=]", stripped):
                assignments.append(f"{relative}:{number}")
    assert assignments == [], (
        "balance_due is derived by the database and cannot be assigned:\n  "
        + "\n  ".join(assignments)
    )
