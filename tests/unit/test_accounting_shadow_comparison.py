"""The shadow comparator, exercised without a database.

`compare_digests` is the function a cutover decision rests on: it says whether
`dotmac-accounting` reproduced ERP's posted ledger.  Everything it needs is
`LedgerFact` values, so it can be — and here is — tested exactly, including the
three failure modes it exists to tell apart:

- a whole document missing (control totals move),
- money posted to the wrong account (totals hold, accounts move),
- the same position reached by different entries (accounts hold, lines move).

The third is the one a naive "do the balances match?" check cannot see, which is
why the digest has three levels rather than one boolean.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.services.finance.gl.accounting_shadow import (
    LedgerFact,
    PeriodScope,
    ShadowComparisonError,
    compare_digests,
    digest_facts,
    normalise_amount,
)

ORG = uuid4()
SCOPE = PeriodScope(organization_id=ORG, fiscal_year_code="FY2026", period_number=2)


def fact(
    account: str,
    debit: str = "0",
    credit: str = "0",
    *,
    day: int = 7,
    journal: str = "JE-0001",
    currency: str = "NGN",
) -> LedgerFact:
    return LedgerFact(
        account_code=account,
        posting_date=date(2026, 2, day),
        journal_number=journal,
        debit=Decimal(debit),
        credit=Decimal(credit),
        currency_code=currency,
    )


BOOKS = (
    fact("1200", debit="48375.00"),
    fact("4100", credit="45000.00"),
    fact("2210", credit="3375.00"),
)


def test_identical_books_agree_at_every_level() -> None:
    left = digest_facts(SCOPE, "erp", BOOKS)
    right = digest_facts(SCOPE, "module", BOOKS)
    result = compare_digests(left, right)
    assert result.matches
    assert result.control_totals_match
    assert result.accounts_match
    assert result.line_digests_match
    assert "agree exactly" in result.explain()


def test_row_order_does_not_affect_the_digest() -> None:
    """Two stores return rows in whatever order their indexes prefer.  If that
    leaked into the digest, every comparison would report a false divergence."""
    forward = digest_facts(SCOPE, "erp", BOOKS)
    reversed_ = digest_facts(SCOPE, "module", tuple(reversed(BOOKS)))
    assert forward.ordered_line_digest == reversed_.ordered_line_digest


def test_trailing_zeros_are_the_same_money() -> None:
    """`Decimal("45000")` and `Decimal("45000.000000")` must not read as a
    divergence — they are the same amount at two scales."""
    left = digest_facts(SCOPE, "erp", (fact("4100", credit="45000"),))
    right = digest_facts(SCOPE, "module", (fact("4100", credit="45000.000000"),))
    assert compare_digests(left, right).matches


def test_a_balanced_period_reports_itself_balanced() -> None:
    assert digest_facts(SCOPE, "erp", BOOKS).is_balanced


def test_a_missing_document_moves_the_control_totals() -> None:
    left = digest_facts(SCOPE, "erp", BOOKS)
    right = digest_facts(SCOPE, "module", BOOKS[:2])
    result = compare_digests(left, right)
    assert not result.matches
    assert not result.control_totals_match
    assert result.line_count_delta == -1
    assert result.total_credit_delta == Decimal("-3375.000000")
    assert "control totals differ" in result.explain()


def test_a_mapping_error_holds_the_totals_and_moves_one_account() -> None:
    """The failure a control-total check is blind to: the right money, the wrong
    account.  Totals are identical; two accounts diverge."""
    misposted = (
        fact("1200", debit="48375.00"),
        fact("4900", credit="45000.00"),  # revenue booked to the wrong account
        fact("2210", credit="3375.00"),
    )
    result = compare_digests(
        digest_facts(SCOPE, "erp", BOOKS), digest_facts(SCOPE, "module", misposted)
    )
    assert result.control_totals_match
    assert not result.accounts_match
    assert {d.account_code for d in result.account_divergences} == {"4100", "4900"}
    assert "account(s) differ" in result.explain()


def test_same_position_different_entries_is_caught_only_by_the_line_digest() -> None:
    """Two credits on one account, the same two amounts, attached to different
    days.  Identical control totals, identical per-account totals AND counts —
    only the ordered line digest can see it."""
    erp_books = (
        fact("1200", debit="48375.00"),
        fact("4100", credit="20000.00", day=7),
        fact("4100", credit="25000.00", day=9),
        fact("2210", credit="3375.00"),
    )
    module_books = (
        fact("1200", debit="48375.00"),
        fact("4100", credit="25000.00", day=7),
        fact("4100", credit="20000.00", day=9),
        fact("2210", credit="3375.00"),
    )
    result = compare_digests(
        digest_facts(SCOPE, "erp", erp_books),
        digest_facts(SCOPE, "module", module_books),
    )
    assert result.control_totals_match
    assert result.accounts_match
    assert not result.line_digests_match
    assert not result.matches
    assert "different entries" in result.explain()


def test_splitting_one_line_into_two_shows_up_as_an_account_count_divergence() -> None:
    """Same account, same total, more lines.  The per-account line count is part
    of the account comparison precisely so this does not have to wait for the
    digest to be noticed."""
    split = (
        fact("1200", debit="48375.00"),
        fact("4100", credit="20000.00"),
        fact("4100", credit="25000.00"),
        fact("2210", credit="3375.00"),
    )
    result = compare_digests(
        digest_facts(SCOPE, "erp", BOOKS), digest_facts(SCOPE, "module", split)
    )
    assert result.control_totals_match is False  # one extra line overall
    assert not result.accounts_match
    (divergence,) = [d for d in result.account_divergences if d.account_code == "4100"]
    assert divergence.debit_delta == 0
    assert divergence.credit_delta == 0
    assert divergence.line_count_delta == 1


def test_an_account_present_on_only_one_side_is_a_divergence_not_a_skip() -> None:
    extra = BOOKS + (fact("6000", debit="100.00"), fact("1100", credit="100.00"))
    result = compare_digests(
        digest_facts(SCOPE, "erp", BOOKS), digest_facts(SCOPE, "module", extra)
    )
    codes = {d.account_code for d in result.account_divergences}
    assert {"6000", "1100"} <= codes


def test_comparing_different_periods_is_refused_not_reported() -> None:
    """A caller bug must not surface as an accounting difference — that sends
    someone hunting for a posting error that does not exist."""
    other = PeriodScope(organization_id=ORG, fiscal_year_code="FY2026", period_number=3)
    with pytest.raises(ShadowComparisonError, match="different scopes"):
        compare_digests(
            digest_facts(SCOPE, "erp", BOOKS), digest_facts(other, "module", BOOKS)
        )


def test_comparing_different_organizations_is_refused() -> None:
    other = PeriodScope(
        organization_id=uuid4(), fiscal_year_code="FY2026", period_number=2
    )
    with pytest.raises(ShadowComparisonError, match="different scopes"):
        compare_digests(
            digest_facts(SCOPE, "erp", BOOKS), digest_facts(other, "module", BOOKS)
        )


def test_comparing_across_digest_versions_is_refused() -> None:
    """A serialisation change must not read as a ledger change."""
    import dataclasses

    left = digest_facts(SCOPE, "erp", BOOKS)
    right = dataclasses.replace(
        digest_facts(SCOPE, "module", BOOKS), digest_version="erp-gl-posted-ledger.v2"
    )
    with pytest.raises(ShadowComparisonError, match="different versions"):
        compare_digests(left, right)


def test_a_float_amount_is_refused_rather_than_coerced() -> None:
    with pytest.raises(ShadowComparisonError, match="must be exact"):
        normalise_amount(1.5)  # type: ignore[arg-type]


def test_excess_precision_is_refused_rather_than_rounded() -> None:
    """Rounding here would manufacture a match between two different facts."""
    with pytest.raises(ShadowComparisonError, match="does not fit"):
        normalise_amount(Decimal("1.0000005"))


def test_scale_normalisation_is_not_rounding() -> None:
    assert normalise_amount(Decimal("1.5")) == Decimal("1.500000")
    assert normalise_amount(Decimal("1.500000")) == normalise_amount(Decimal("1.5"))


def test_the_module_side_refuses_while_composition_is_disabled() -> None:
    """At gate C the wheel is INSTALLED and the tables exist, which makes this
    refusal more important than it was, not less.

    Storage is not authority.  A shadow run that reached for module tables
    holding nothing — because no backfill has run — would compare a populated
    ledger against an empty one and report a catastrophic false divergence; one
    that quietly fell back to ERP would report perfect agreement and mean
    nothing. The flag is what refuses, and the message names it.
    """
    from app.accounting_adoption import AccountingCompositionNotReady
    from app.services.finance.gl.accounting_shadow import build_module_digest

    with pytest.raises(AccountingCompositionNotReady) as excinfo:
        build_module_digest(SCOPE)
    assert "ACCOUNTING_COMPOSITION_ENABLED is false" in str(excinfo.value)
