"""Stand-ins for an invoice row, with the derived columns actually derived.

Several web-layer tests build a fake invoice as a bare `SimpleNamespace` and
list every field by hand. That was fine while `balance_due` was a Python
`@property` — the stub simply did not have one and nothing asked. ADR-0016
stage 1 made it a generated column, so the read path now asks, and three
tests failed with `'types.SimpleNamespace' object has no attribute
'balance_due'`.

The tempting fix is to add `balance_due=Decimal("110")` to each stub. Do not:
a hand-written balance in a fixture is the exact drift the generated column
was introduced to remove, just relocated into the test suite where it is
harder to notice.

`invoice_stub` instead does what the DATABASE does — derives `balance_due`
from the two operands — so a stub cannot disagree with a real row, and the
next test that adds one gets it for free.

Note what this deliberately does NOT model: on a real instance the column is
only as fresh as the last SELECT, so it does not track an in-flight write to
`amount_paid`. Code that mutates and re-reads in one unit of work must keep
the live subtraction, and is exempted in
`tests/architecture/test_balance_due_is_not_rewritten.py`. A stub is a
snapshot; if a test needs the stale-read behaviour, it should say so
explicitly rather than lean on this helper.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

_ZERO = Decimal("0")


def invoice_stub(**fields: Any) -> SimpleNamespace:
    """A `SimpleNamespace` invoice whose derived columns are derived.

    Pass `total_amount` and `amount_paid` as usual; `balance_due` is computed
    unless the caller sets it explicitly (which a test may legitimately want
    in order to simulate a stale read).
    """
    if "balance_due" not in fields:
        total = fields.get("total_amount", _ZERO)
        paid = fields.get("amount_paid", _ZERO)
        fields["balance_due"] = total - paid
    return SimpleNamespace(**fields)
