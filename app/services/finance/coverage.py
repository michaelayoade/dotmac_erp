"""How much of a monetary document has been paid — derived, never stored.

ADR-0016 stage 2. A monetary document carries two independent facts:

* **Lifecycle** — where it is in its own process. DRAFT, SUBMITTED, APPROVED,
  POSTED, VOID, DISPUTED. Moved by people and workflow.
* **Coverage** — how much of it has been paid. Fully determined by
  `total_amount` and `amount_paid`.

Every ERP schema that models money stores both in one `status` column, and
ADR-0016 records what that produced: twelve code paths recomputing coverage
with seven different rules, a voided invoice reading PAID, a partial payment
erasing OVERDUE, and a repair task that exists only because a cache can drift.

This module owns the coverage half. It is deliberately NOT a status writer and
has no session-scoped state: coverage is arithmetic over two numbers plus one
configured tolerance, and arithmetic cannot drift.

## Why a closed enum here, when ADR-0008 says vocabularies are open

ADR-0008 says a vocabulary whose members belong to modules is declared by those
modules. `PaymentCoverage` is the opposite case and the one an enum is right
for: it has exactly these four members for every document in every product,
because they exhaust the arithmetic. A product cannot need a fifth.

## The Python function and the SQL expression are twins

`coverage_of` answers for a loaded object; `coverage_case` answers inside a
query, which is what aging and dunning need. Two implementations of one rule is
exactly the hazard this ADR exists to remove, so they are held together by a
parity test over the boundary values rather than by hope
(`tests/integration/test_coverage_parity.py`, which runs on PostgreSQL because
that is where the SQL half actually executes).

They take their balance from different places, on purpose. `coverage_case` uses
the stage-1 `balance_due` GENERATED column, which the database computes as part
of the same query. `coverage_of` subtracts live, because on a loaded instance
that column holds whatever the last SELECT returned and does NOT track a Python
assignment to `amount_paid` — so an allocation loop reading it would classify
against a stale balance.

## The tolerance is configuration, not a constant

Sub-cent rounding dust is a real quantity, and modules independently choosing
`Decimal("0.01")` is how they later diverge — AR and AP each declared their own
`PAYMENT_DUST` before this module existed. It is now one `SettingSpec`
(`payments.payment_dust`), read through `resolve_payment_dust`, with
`PAYMENT_DUST_DEFAULT` as the spec's default and the value used by pure callers
that have no session.

Per ADR-0016 the threshold is deliberately NOT welded into the generated
column: `balance_due` is pure subtraction, and applying policy in the query
means changing the tolerance is a setting change rather than a migration that
drops and re-adds a column.
"""

from __future__ import annotations

import enum
import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import Case, ColumnElement, case, literal
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain

__all__ = [
    "PAYMENT_DUST_DEFAULT",
    "PAYMENT_DUST_KEY",
    "PaymentCoverage",
    "coverage_case",
    "coverage_of",
    "resolve_payment_dust",
]


class PaymentCoverage(str, enum.Enum):
    """How much of a document's total has been paid.

    A `str` enum so it compares equal to its wire form and can be bound into a
    query without conversion, matching how `SettingDomain` and the lifecycle
    enums already behave in this codebase.
    """

    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"
    OVERPAID = "overpaid"


# The dust default. A balance this small is not a debt — the view AR's
# `advance_allocation` already held when it declared `DUST_THRESHOLD = 0.01`
# for what to allocate, while using an exact `>=` six lines later to decide the
# resulting status. One declaration now, and it is only the DEFAULT: the live
# value comes from the setting below.
PAYMENT_DUST_DEFAULT = Decimal("0.01")

PAYMENT_DUST_DOMAIN = SettingDomain.payments
PAYMENT_DUST_KEY = "payment_dust"


def coverage_of(
    *,
    total_amount: Decimal,
    amount_paid: Decimal,
    dust: Decimal = PAYMENT_DUST_DEFAULT,
) -> PaymentCoverage:
    """Classify one document's payment coverage.

    Pure: no session, no clock, no I/O. `dust` is passed in rather than read,
    so this stays callable from anywhere and the tolerance stays visible at the
    call site.

    A **zero-total document reads PAID**, because nothing is outstanding. That
    is the arithmetic answer and it is deliberately not a claim that a payment
    occurred — which is precisely the conflation ADR-0016 removes. Under the
    old scheme `PAID` had to mean both "settled" and "a terminal lifecycle
    state", so this case was a bug; with the two facts separated, "balance
    zero" is all it says, and aging and dunning (which key on balance) are
    right to skip it.
    """
    balance = total_amount - amount_paid

    if balance < -dust:
        return PaymentCoverage.OVERPAID
    if balance <= dust:
        return PaymentCoverage.PAID
    if amount_paid > dust:
        return PaymentCoverage.PARTIAL
    return PaymentCoverage.UNPAID


def coverage_case(
    balance_due: ColumnElement[Decimal],
    amount_paid: ColumnElement[Decimal],
    *,
    dust: Decimal = PAYMENT_DUST_DEFAULT,
) -> Case:
    """The SQL twin of `coverage_of`, for filtering and grouping in a query.

        stmt = select(Invoice).where(
            coverage_case(Invoice.balance_due, Invoice.amount_paid, dust=dust)
            == PaymentCoverage.PARTIAL.value
        )

    Takes `balance_due` — the stage-1 GENERATED column — rather than the two
    operands, so the subtraction has exactly one definition and this expression
    cannot drift from it. The first draft took `total_amount, amount_paid` and
    recomputed the difference; `test_balance_due_is_not_rewritten` rejected it,
    correctly.

    Note the asymmetry with `coverage_of`, which is deliberate. In SQL the
    column is authoritative, because the database computes it as part of the
    same query. In Python it is whatever the last SELECT returned and does NOT
    track an in-flight assignment to `amount_paid`, so `coverage_of` subtracts
    live. Same rule, different correct source, for the reason recorded in that
    test's `_LIVE_SUBTRACTION_REQUIRED`.

    The branch ORDER mirrors `coverage_of` exactly, and must keep mirroring it:
    the branches overlap (a balance of zero satisfies more than one arm), so
    this is one rule expressed twice and the parity test is what keeps the two
    honest.
    """
    balance = balance_due
    return case(
        (balance < literal(-dust), literal(PaymentCoverage.OVERPAID.value)),
        (balance <= literal(dust), literal(PaymentCoverage.PAID.value)),
        (amount_paid > literal(dust), literal(PaymentCoverage.PARTIAL.value)),
        else_=literal(PaymentCoverage.UNPAID.value),
    )


def resolve_payment_dust(db: Session, *, organization_id: uuid.UUID | str) -> Decimal:
    """The organization's configured dust tolerance.

    Stored as a string and parsed with `Decimal`, never `float`: this is money,
    and `float("0.01")` is not `0.01`. A value that will not parse falls back to
    the default and does NOT raise — a malformed tolerance must not take the AR
    aging report down, and the failure mode of the default is conservative
    (a slightly stricter threshold leaves an invoice PARTIAL, which a human
    sees, rather than marking an unpaid one PAID, which nobody chases).
    """
    from app.services.settings_cache import get_setting_value

    raw = get_setting_value(
        db,
        PAYMENT_DUST_DOMAIN,
        PAYMENT_DUST_KEY,
        str(PAYMENT_DUST_DEFAULT),
        organization_id=organization_id,
    )
    if isinstance(raw, Decimal):
        return raw
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return PAYMENT_DUST_DEFAULT
