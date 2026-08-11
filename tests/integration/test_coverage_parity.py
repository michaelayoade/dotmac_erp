"""The Python rule and the SQL rule must give the same answer.

`coverage_of` classifies a loaded object; `coverage_case` classifies inside a
query, which is what AR aging and dunning need. That is ONE rule expressed
TWICE, and ADR-0016 exists because a rule expressed twice diverges — twelve
sites, seven rules, three tolerances.

Nothing structural prevents the divergence here: the two are ordinary Python
and ordinary SQL, and the branches overlap (a balance of zero satisfies more
than one arm), so reordering either silently changes answers only at the
boundaries. This test is the thing that prevents it, which is why it runs on
**PostgreSQL** — SQLite's looser numeric handling would let a real
`NUMERIC`-comparison difference pass.

No tables are involved: the expression is evaluated over literals, so this
tests the rule rather than any particular model's columns, and adding a third
document type later needs no change here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import literal, select
from sqlalchemy.orm import Session

from app.services.finance.coverage import (
    PAYMENT_DUST_DEFAULT,
    PaymentCoverage,
    coverage_case,
    coverage_of,
)

# The boundary matrix. Every row is a place the old rules disagreed with each
# other, plus the degenerate documents.
CASES = [
    ("100.00", "0.00"),  # unpaid
    ("100.00", "0.01"),  # dust paid — still unpaid
    ("100.00", "0.02"),  # just over dust — partial
    ("100.00", "40.00"),  # partial
    ("100.00", "99.98"),  # a hair over dust outstanding — partial
    ("100.00", "99.99"),  # exactly dust outstanding — paid
    ("100.00", "100.00"),  # paid
    ("100.00", "100.01"),  # dust overpaid — still paid
    ("100.00", "100.02"),  # just over — overpaid
    ("100.00", "150.00"),  # overpaid
    ("0.00", "0.00"),  # zero-total document
    ("-100.00", "0.00"),  # credit document
]

TOLERANCES = ["0.01", "0", "1.00"]


@pytest.mark.parametrize("dust", TOLERANCES)
@pytest.mark.parametrize(("total", "paid"), CASES)
def test_sql_agrees_with_python(db: Session, total: str, paid: str, dust: str) -> None:
    total_amount, amount_paid, tolerance = (
        Decimal(total),
        Decimal(paid),
        Decimal(dust),
    )

    in_python = coverage_of(
        total_amount=total_amount, amount_paid=amount_paid, dust=tolerance
    )
    in_sql = db.scalar(
        select(
            coverage_case(literal(total_amount), literal(amount_paid), dust=tolerance)
        )
    )

    assert in_sql == in_python.value, (
        f"total={total} paid={paid} dust={dust}: "
        f"SQL said {in_sql!r}, Python said {in_python.value!r}. "
        "The two implementations of the coverage rule have diverged — fix "
        "both, in `app/services/finance/coverage.py`."
    )


def test_the_matrix_reaches_every_member(db: Session) -> None:
    """Sensitivity: without this, a matrix that happened to produce only PAID
    would agree perfectly and prove nothing."""
    reached = {
        coverage_of(
            total_amount=Decimal(total),
            amount_paid=Decimal(paid),
            dust=PAYMENT_DUST_DEFAULT,
        )
        for total, paid in CASES
    }
    assert reached == set(PaymentCoverage)
