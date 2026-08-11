"""`total_amount - amount_paid` has one definition, and every copy is named.

`balance_due` was a Python `@property`, so it could not appear in a WHERE, an
ORDER BY or a SUM. Callers therefore wrote the subtraction out by hand — **75
occurrences across ~30 files** when this was measured. Every one is a place
the definition can drift, and ADR-0016 is about exactly that class of
duplication one level up (coverage stored as a status).

ADR-0016 stage 1 made `balance_due` a generated column. 63 of those call
sites have since moved onto it, and the 9 that remain are not backlog — each
one is a place the column genuinely cannot be used. So this stopped being a
ratchet (a number that shrinks) and became an ALLOWLIST (a set with reasons),
per ADR-0018: an exemption has to say why, or it is indistinguishable from
debt nobody got to.

## The one non-obvious rule

A generated column is computed by the DATABASE. `invoice.balance_due` on a
loaded instance is whatever the last SELECT returned — it does NOT update
when Python assigns to `amount_paid`. The hand-written subtraction is always
live; the column is live only after a flush and refresh.

So a read is convertible when the object is being displayed, and NOT
convertible when the same unit of work is writing `amount_paid` — an
allocation loop reading a stale balance over-applies on its second pass.
That distinction is why `_LIVE_SUBTRACTION_REQUIRED` exists below, and it is
repeated as a comment at both call sites, where a refactorer will see it.
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

# The generated-column definitions themselves.
_OWNERS = {
    "app/models/finance/ar/invoice.py",
    "app/models/finance/ap/supplier_invoice.py",
}

# The subtraction must stay live because the same unit of work is writing
# `amount_paid`. See the module docstring; the reason is also inline at both
# sites.
_LIVE_SUBTRACTION_REQUIRED = {
    "app/services/finance/ar/exact_match_allocation.py": 1,
    "app/tasks/data_health.py": 1,
}

# `CreditNote` has no `balance_due` generated column — only `ar.invoice` and
# `ap.supplier_invoice` got one in ADR-0016 stage 1. Converting these would
# reference an attribute that does not exist.
_NOT_AN_INVOICE_MODEL = {
    "app/services/finance/ar/web/credit_note_web.py": 2,
    "app/services/finance/ar/web.py": 2,
}

# Pure functions over Decimal VALUES, and one outbound DTO built from locals.
# There is no ORM instance and no column to read.
_NO_MODEL_IN_SCOPE = {
    "app/services/finance/ar/payment_status.py": 1,
    "app/services/finance/ap/payment_status.py": 1,
    "app/services/sync/sub_purchase_invoice_status.py": 1,
    # `coverage_of` takes two Decimals and returns a `PaymentCoverage`. Its SQL
    # twin `coverage_case` in the same file DOES take `balance_due` — this test
    # rejected the first draft, which recomputed the difference there, and was
    # right to. The asymmetry is the stage-1 staleness rule: in a query the
    # database computes the column now, but on a loaded instance it holds
    # whatever the last SELECT returned, so the pure function must subtract
    # live or an allocation loop classifies against a stale balance.
    "app/services/finance/coverage.py": 1,
}

_ALLOWED: dict[str, int] = {
    **_LIVE_SUBTRACTION_REQUIRED,
    **_NOT_AN_INVOICE_MODEL,
    **_NO_MODEL_IN_SCOPE,
}


def _handwritten_sites() -> dict[str, list[str]]:
    sites: dict[str, list[str]] = {}
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
                sites.setdefault(relative, []).append(f"{relative}:{number}")
    return sites


def test_no_new_hand_written_copy():
    """A file not on the allowlist may not write the subtraction at all."""
    unexpected = [
        site
        for relative, found in _handwritten_sites().items()
        if relative not in _ALLOWED
        for site in found
    ]
    assert unexpected == [], (
        "New hand-written `total_amount - amount_paid`. Use the `balance_due` "
        "generated column (ADR-0016). If the column genuinely cannot be used "
        "here, add the file to the allowlist in this test UNDER THE CATEGORY "
        "THAT EXPLAINS WHY:\n  " + "\n  ".join(unexpected)
    )


def test_an_allowed_file_does_not_quietly_grow_more_copies():
    """The allowlist exempts a stated number of sites, not a whole file."""
    found = _handwritten_sites()
    drifted = {
        relative: (len(found.get(relative, [])), expected)
        for relative, expected in _ALLOWED.items()
        if len(found.get(relative, [])) != expected
    }
    assert drifted == {}, (
        "An allowlisted file's count changed (actual, allowed). If a copy was "
        "removed, lower the number; if one was added, justify it or use the "
        f"column:\n  {drifted}"
    )


def test_every_allowlist_entry_is_still_real():
    """An exemption for a site that no longer exists is stale permission."""
    found = _handwritten_sites()
    stale = [relative for relative in _ALLOWED if relative not in found]
    assert stale == [], f"Allowlisted but no longer present — remove: {stale}"


def test_the_detector_actually_matches_both_forms():
    """Sensitivity proof: the checks above are not passing because the pattern
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
