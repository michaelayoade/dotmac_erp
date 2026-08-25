"""Shadow comparison between ERP's posted ledger and `dotmac-accounting`'s.

A cutover is only allowed to seal a writer once the replacement has been shown
to produce the SAME accounting facts from the same inputs.  That evidence is
this module: a canonical, exact digest of one organization's posted ledger for
one fiscal period, plus a pure comparator that says precisely how two digests
differ.

## Why the shape is "facts in, digest out"

ERP can build a digest today.  The module cannot — it is not installed, and
will not be until its release tag exists.  If the comparator queried both sides
itself it could not be written, let alone tested, before then.  So both sides
produce the same normalised `LedgerFact` values and `digest_facts` turns either
stream into a `PeriodLedgerDigest`.  ERP's producer is here and is real;
the module's producer is `build_module_digest`, which refuses rather than
degrading until composition is enabled.

That split is not just convenience.  It means the comparison logic — the part a
reviewer has to trust with a decision about the general ledger — is a pure
function over exact `Decimal`s, unit-tested without a database, and identical on
the day of the cutover to the day it was written.

## What is compared, and why that is the right evidence

The POSTED ledger, not the journals.  `gl.posted_ledger_line` is the immutable
append-only evidence ERP's own GL contract calls authoritative for reporting
(`docs/gl_source_of_truth.md`), and `mod_accounting.posted_ledger_lines` is its
module counterpart.  Comparing drafts would measure intent; comparing balances
would measure a derived cache.  The posted line is the fact.

Three levels, because they fail differently and a single boolean hides which:

- **Control totals** — line count, total debit, total credit.  A mismatch here is
  a whole document missing or duplicated.
- **Per account** — debit, credit and line count by account code.  A mismatch
  here with matching control totals is a MAPPING error: the right money posted
  to the wrong account.  This is the failure a control-total check cannot see,
  and the one that is most expensive to find later.
- **Ordered line digest** — a hash over every line in canonical order.  A
  mismatch here with matching per-account totals means the same net position was
  reached by different lines: different dates, different journals, offsetting
  pairs.  Same balance, different books.

## Exactness

Money is `Decimal`, quantised to six decimal places — the scale both
`gl.posted_ledger_line.debit_amount` (`Numeric(20, 6)`) and the module's `MONEY`
type carry.  Quantising is normalisation, never rounding: a value that does not
fit in six places is a source fact this comparison must not silently alter, so
`normalise_amount` refuses it.  No float appears anywhere in this module.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from collections.abc import Iterable, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.fiscal_year import FiscalYear
from app.models.finance.gl.posted_ledger_line import PostedLedgerLine

#: The scale both sides store money at.  Not a rounding policy — see module doc.
MONEY_SCALE: int = 6
_QUANTUM = Decimal(1).scaleb(-MONEY_SCALE)
#: Bumped whenever the canonical serialisation below changes, so a digest taken
#: before the change can never be compared with one taken after and read as a
#: real divergence.
DIGEST_VERSION: str = "erp-gl-posted-ledger.v1"


class ShadowComparisonError(ValueError):
    """The two digests are not comparable — a defect, not a divergence."""


def normalise_amount(value: Decimal | int) -> Decimal:
    """Return `value` at exactly `MONEY_SCALE` places, refusing anything lossy.

    `Decimal("1.5")` and `Decimal("1.500000")` are the same money and must
    compare equal; `Decimal("1.0000005")` is a source fact with more precision
    than either store holds, and silently dropping the last digit here would
    manufacture a match.
    """
    if isinstance(value, (bool, float)):
        raise ShadowComparisonError(f"amount must be exact, got {value!r}")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ShadowComparisonError(f"amount is not a number: {value!r}") from exc
    quantised = amount.quantize(_QUANTUM)
    if quantised != amount:
        raise ShadowComparisonError(
            f"amount {amount} does not fit {MONEY_SCALE} decimal places; "
            "a source fact this comparison cannot represent must not be rounded"
        )
    return quantised


@dataclass(frozen=True, order=True)
class PeriodScope:
    """Which organization's which period a digest describes.

    Identified by BUSINESS key — organization plus fiscal year code plus period
    number — never by `fiscal_period_id`.  The module mints its own period ids,
    so a surrogate key would make every comparison structurally unequal and
    prove nothing.
    """

    organization_id: UUID
    fiscal_year_code: str
    period_number: int

    def label(self) -> str:
        return f"{self.fiscal_year_code}/P{self.period_number:02d}"


@dataclass(frozen=True, order=True)
class LedgerFact:
    """One posted ledger line, reduced to what both stores genuinely agree on.

    Deliberately excludes surrogate ids, `posting_batch_id`, `posted_by` and
    timestamps: those describe HOW each system recorded the fact, and requiring
    them to match would fail every comparison for reasons that are not
    accounting.  What remains is the entry itself.
    """

    account_code: str
    posting_date: date
    journal_number: str
    debit: Decimal
    credit: Decimal
    currency_code: str

    def canonical(self) -> str:
        """The serialised form the ordered digest hashes.

        Amounts go through `normalise_amount` HERE, not at construction: one
        store hands back `Decimal("45000")` and the other `Decimal("45000.000000")`
        for the same money, and hashing the raw `str` of either would report a
        divergence for a difference in scale rather than in value.
        """
        return "|".join(
            (
                self.account_code,
                self.posting_date.isoformat(),
                self.journal_number,
                f"{normalise_amount(self.debit):f}",
                f"{normalise_amount(self.credit):f}",
                self.currency_code,
            )
        )


@dataclass(frozen=True)
class AccountTotals:
    debit: Decimal
    credit: Decimal
    line_count: int


@dataclass(frozen=True)
class PeriodLedgerDigest:
    """An exact, comparable summary of one period's posted ledger on one side."""

    scope: PeriodScope
    source: str
    line_count: int
    total_debit: Decimal
    total_credit: Decimal
    by_account: Mapping[str, AccountTotals]
    ordered_line_digest: str
    digest_version: str = DIGEST_VERSION

    @property
    def is_balanced(self) -> bool:
        return self.total_debit == self.total_credit


def digest_facts(
    scope: PeriodScope, source: str, facts: Iterable[LedgerFact]
) -> PeriodLedgerDigest:
    """Fold a stream of posted-ledger facts into a digest.

    Sorting happens HERE rather than in either producer.  Two stores will return
    rows in whatever order their indexes prefer, and an ordered digest that
    depended on that would report a divergence for every comparison.  Sorting on
    the fact's own natural order makes the digest a property of the accounting
    content alone.
    """
    ordered = sorted(facts)
    hasher = hashlib.sha256()
    hasher.update(f"{DIGEST_VERSION}\n{scope.label()}\n".encode())
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    accumulated: dict[str, list[Decimal | int]] = {}
    for fact in ordered:
        debit = normalise_amount(fact.debit)
        credit = normalise_amount(fact.credit)
        total_debit += debit
        total_credit += credit
        bucket = accumulated.setdefault(fact.account_code, [Decimal(0), Decimal(0), 0])
        bucket[0] += debit  # type: ignore[operator]
        bucket[1] += credit  # type: ignore[operator]
        bucket[2] += 1  # type: ignore[operator]
        hasher.update(f"{fact.canonical()}\n".encode())
    return PeriodLedgerDigest(
        scope=scope,
        source=source,
        line_count=len(ordered),
        total_debit=normalise_amount(total_debit),
        total_credit=normalise_amount(total_credit),
        by_account={
            code: AccountTotals(
                debit=normalise_amount(values[0]),  # type: ignore[arg-type]
                credit=normalise_amount(values[1]),  # type: ignore[arg-type]
                line_count=int(values[2]),
            )
            for code, values in sorted(accumulated.items())
        },
        ordered_line_digest=hasher.hexdigest(),
    )


@dataclass(frozen=True)
class AccountDivergence:
    account_code: str
    debit_delta: Decimal
    credit_delta: Decimal
    line_count_delta: int


@dataclass(frozen=True)
class ShadowComparison:
    """What differs, at every level, between two sides of one period."""

    scope: PeriodScope
    left: str
    right: str
    line_count_delta: int
    total_debit_delta: Decimal
    total_credit_delta: Decimal
    account_divergences: tuple[AccountDivergence, ...]
    line_digests_match: bool

    @property
    def control_totals_match(self) -> bool:
        return (
            self.line_count_delta == 0
            and self.total_debit_delta == 0
            and self.total_credit_delta == 0
        )

    @property
    def accounts_match(self) -> bool:
        return not self.account_divergences

    @property
    def matches(self) -> bool:
        """Every level agrees.  Only this authorises advancing a cutover gate."""
        return (
            self.control_totals_match
            and self.accounts_match
            and self.line_digests_match
        )

    def explain(self) -> str:
        """A reviewer-facing summary that names the FAILING level.

        Which level failed is the whole diagnostic value: totals-only failures
        are missing documents, account-level failures are mapping errors, and a
        digest-only failure is the same net position reached by different lines.
        """
        if self.matches:
            return f"{self.scope.label()}: {self.left} and {self.right} agree exactly"
        parts: list[str] = []
        if not self.control_totals_match:
            parts.append(
                "control totals differ "
                f"(lines {self.line_count_delta:+d}, "
                f"debit {self.total_debit_delta:+f}, "
                f"credit {self.total_credit_delta:+f})"
            )
        if not self.accounts_match:
            worst = ", ".join(
                f"{d.account_code} debit {d.debit_delta:+f} credit {d.credit_delta:+f}"
                for d in self.account_divergences[:5]
            )
            more = len(self.account_divergences) - 5
            parts.append(
                f"{len(self.account_divergences)} account(s) differ: {worst}"
                + (f", and {more} more" if more > 0 else "")
            )
        if (
            not self.line_digests_match
            and self.control_totals_match
            and self.accounts_match
        ):
            parts.append(
                "same totals per account but different lines — the same position "
                "reached by different entries"
            )
        elif not self.line_digests_match:
            parts.append("line digests differ")
        return f"{self.scope.label()}: " + "; ".join(parts)


def compare_digests(
    left: PeriodLedgerDigest, right: PeriodLedgerDigest
) -> ShadowComparison:
    """Compare two digests of the SAME scope, taken at the same digest version.

    Refuses mismatched scopes and versions rather than reporting a divergence: a
    comparison of two different periods is a bug in the caller, and reporting it
    as an accounting difference would send someone hunting for a posting error
    that does not exist.
    """
    if left.scope != right.scope:
        raise ShadowComparisonError(
            f"cannot compare different scopes: {left.scope} vs {right.scope}"
        )
    if left.digest_version != right.digest_version:
        raise ShadowComparisonError(
            "cannot compare digests taken at different versions: "
            f"{left.digest_version} vs {right.digest_version}"
        )

    divergences: list[AccountDivergence] = []
    zero = AccountTotals(Decimal(0), Decimal(0), 0)
    for code in sorted(set(left.by_account) | set(right.by_account)):
        lhs = left.by_account.get(code, zero)
        rhs = right.by_account.get(code, zero)
        debit_delta = rhs.debit - lhs.debit
        credit_delta = rhs.credit - lhs.credit
        count_delta = rhs.line_count - lhs.line_count
        if debit_delta or credit_delta or count_delta:
            divergences.append(
                AccountDivergence(
                    account_code=code,
                    debit_delta=debit_delta,
                    credit_delta=credit_delta,
                    line_count_delta=count_delta,
                )
            )

    return ShadowComparison(
        scope=left.scope,
        left=left.source,
        right=right.source,
        line_count_delta=right.line_count - left.line_count,
        total_debit_delta=right.total_debit - left.total_debit,
        total_credit_delta=right.total_credit - left.total_credit,
        account_divergences=tuple(divergences),
        line_digests_match=left.ordered_line_digest == right.ordered_line_digest,
    )


class ErpLedgerDigestService:
    """Builds the ERP side of a shadow comparison.

    Read-only by construction: it opens no transaction of its own, writes
    nothing, and is safe to run against a live database while ERP remains the
    authority.  A shadow comparison that mutated the system it measures would
    not be a shadow.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def resolve_scope(
        self, organization_id: UUID, fiscal_period_id: UUID
    ) -> PeriodScope:
        """Turn ERP's surrogate period id into the business key both sides share."""
        stmt = (
            select(FiscalYear.year_code, FiscalPeriod.period_number)
            .join(FiscalYear, FiscalYear.fiscal_year_id == FiscalPeriod.fiscal_year_id)
            .where(
                FiscalPeriod.organization_id == organization_id,
                FiscalPeriod.fiscal_period_id == fiscal_period_id,
            )
        )
        row = self.db.execute(stmt).one_or_none()
        if row is None:
            raise ShadowComparisonError(
                f"fiscal period {fiscal_period_id} not found for organization "
                f"{organization_id}"
            )
        return PeriodScope(
            organization_id=organization_id,
            fiscal_year_code=row.year_code,
            period_number=row.period_number,
        )

    def ledger_facts(
        self, organization_id: UUID, fiscal_period_id: UUID
    ) -> list[LedgerFact]:
        """Every posted line ERP holds for that organization and period.

        `original_currency_code` is preferred over the functional currency for
        the fact's currency, because that is what the module records on the
        posted line; ERP's amounts are already functional, and the pair is what
        makes the comparison meaningful for a multi-currency organization.
        """
        stmt = select(
            PostedLedgerLine.account_code,
            PostedLedgerLine.posting_date,
            PostedLedgerLine.journal_reference,
            PostedLedgerLine.journal_entry_id,
            PostedLedgerLine.debit_amount,
            PostedLedgerLine.credit_amount,
            PostedLedgerLine.original_currency_code,
        ).where(
            PostedLedgerLine.organization_id == organization_id,
            PostedLedgerLine.fiscal_period_id == fiscal_period_id,
        )
        facts: list[LedgerFact] = []
        for row in self.db.execute(stmt):
            facts.append(
                LedgerFact(
                    account_code=row.account_code,
                    posting_date=row.posting_date,
                    journal_number=row.journal_reference or str(row.journal_entry_id),
                    debit=normalise_amount(row.debit_amount),
                    credit=normalise_amount(row.credit_amount),
                    currency_code=row.original_currency_code or "",
                )
            )
        return facts

    def build_digest(
        self, organization_id: UUID, fiscal_period_id: UUID
    ) -> PeriodLedgerDigest:
        scope = self.resolve_scope(organization_id, fiscal_period_id)
        return digest_facts(
            scope, "erp", self.ledger_facts(organization_id, fiscal_period_id)
        )


def build_module_digest(scope: PeriodScope) -> PeriodLedgerDigest:
    """The `dotmac-accounting` side — refuses until composition is enabled.

    Kept as a named, failing seam rather than left out: the shape of the
    comparison is settled now, and the only thing missing on the module side is
    the artifact.  When the tag exists this body reads `posted_ledger_lines` for
    the same scope and hands the facts to `digest_facts`.
    """
    from app.accounting_adoption import require_composition_ready

    require_composition_ready()
    raise NotImplementedError(
        "the module-side reader lands with the pin; see docs/architecture/"
        "accounting-adoption-boundary.md gate C"
    )


__all__ = [
    "AccountDivergence",
    "AccountTotals",
    "DIGEST_VERSION",
    "ErpLedgerDigestService",
    "LedgerFact",
    "MONEY_SCALE",
    "PeriodLedgerDigest",
    "PeriodScope",
    "ShadowComparison",
    "ShadowComparisonError",
    "build_module_digest",
    "compare_digests",
    "digest_facts",
    "normalise_amount",
]
