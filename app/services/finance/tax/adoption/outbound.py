"""An approved determination set → ERP's accounting-consequence path.

`docs/architecture/dotmac-tax-adoption-boundary.md` § "Typed ERP seams" defines
the job precisely: the consequence adapter "verifies the module fingerprint and
arithmetic, resolves an unambiguous effective ERP account mapping for every
component, writes the local document/tax-transaction snapshot, and calls the
accounting owner in the same transaction. Missing mappings, closed fiscal
periods, currency mismatches, duplicate source versions or changed fingerprints
refuse the whole source row. A module determination never writes GL directly."

C2 consumes the module's released public read contract and delivers the
PROJECTION half as a pure function:
:func:`project_determination_set` verifies, resolves and produces a typed
:class:`ConsequencePosting`. A postable result is complete, self-balancing and
renders into ERP's existing `JournalInput`/`JournalLineInput` — the accounting
owner's own input type. A reportable-only zero treatment carries its return-box
components and renders no journal. The projection performs no write. Writing
the snapshot and invoking
`BasePostingAdapter.create_approve_and_post_journal` is the cohort cutover
(C4), which is gated on a zero-drift shadow; supplying an adapter does not cut
anything over.

## Why the projection is pure

Three things follow from it and all three matter:

- it is testable with no database and no session;
- it cannot half-write.  A refusal happens before anything exists, so "refuse
  the whole source row" is structural rather than a `rollback()` someone has to
  remember; and
- the preconditions it cannot check itself become REQUIRED ARGUMENTS instead of
  silent assumptions.  `fiscal_period_id` is the value
  `PeriodGuardService.require_open_period()` returns, so a caller that has not
  proved the period open has nothing to pass.  `expected_fingerprint` is the
  fingerprint ERP recorded when it submitted the fact, so a set that was
  silently re-determined cannot be posted against a document priced on the
  previous one.

## Which way the ignorance runs

The module produces amounts, code/rule identity and treatment.  It is never
asked about an account, a journal, a period or a side.  Those are ERP's, and
they enter here through :class:`TaxAccountMap` — an ERP-owned, effective account
mapping KEYED BY MODULE TAX-CODE ID, which is what the boundary document's
backfill table means by "effective account mapping keyed to module tax-code id".
It is deliberately not read from `tax.tax_code.tax_collected_account_id` &c.:
those columns belong to rows the module is replacing, and their ids are a
different identifier space.

## Debit/credit, and why per-consequence rather than per-rate-sign

`AccountingConsequence` is chosen by ERP at the call site and decides the shape:

- `ar_output_tax` — tax is a liability owed to the authority: CREDIT the
  collected account per component, DEBIT the receivable counterpart.
- `ap_input_tax` — the recovery split becomes two different accounts: DEBIT the
  recoverable portion to the tax-paid (asset) account and the non-recoverable
  portion to the tax-expense account, CREDIT the payable counterpart.  This is
  the one place a single component yields two lines, and it is the reason the
  module's `recoverable_rate` has to arrive as an AMOUNT split rather than a
  ratio ERP re-applies.
- `withholding_payable` — CREDIT the collected (withholding-payable) account,
  DEBIT the counterpart, preserving `net + WHT = gross` at the document level.
- `payroll_tax_payable` — CREDIT the collected (PAYE-payable) account, DEBIT the
  payroll counterpart.

`reversal` flips every line.  It is taken from the set, which took it from the
source fact's document type; nothing here re-derives direction from a sign.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from dotmac_tax import TaxDeterminationComponentV1, TaxDeterminationSetV1

from app.services.finance.tax.adoption.contracts import (
    AccountingConsequence,
    TaxApplicationContextV1,
    TaxAdapterRefusal,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.finance.gl.journal import JournalInput

__all__ = [
    "SOURCE_MODULE",
    "ConsequencePosting",
    "ConsequencePostingLine",
    "TaxAccountMap",
    "TaxCodeAccounts",
    "project_determination_set",
]

_ZERO = Decimal("0")

#: What the journal is stamped with.  ERP's existing `TAXPostingAdapter`
#: already uses `source_module="TAX"`; reusing it keeps one module name in the
#: ledger across the cutover rather than splitting tax history in two.
SOURCE_MODULE = "TAX"

# ERP owns the accounting consequence, but it may not contradict the module's
# transaction-side evidence. This mapping is the typed seam between those two
# owners, not a second tax-policy vocabulary.
_CONSEQUENCE_BY_TRANSACTION_SIDE: dict[str, AccountingConsequence] = {
    "output": AccountingConsequence.AR_OUTPUT_TAX,
    "input": AccountingConsequence.AP_INPUT_TAX,
    "withholding": AccountingConsequence.WITHHOLDING_PAYABLE,
    "liability": AccountingConsequence.PAYROLL_TAX_PAYABLE,
}

#: Account roles, named once.  `collected` is the authority-owed liability
#: (output VAT, withholding payable, PAYE payable), `paid` the recoverable
#: input-tax asset, `expense` the irrecoverable input tax that becomes cost.
_ROLE_COLLECTED = "collected"
_ROLE_PAID = "paid"
_ROLE_EXPENSE = "expense"


@dataclass(frozen=True, slots=True)
class TaxCodeAccounts:
    """ERP's effective account choice for ONE module tax code.

    Every role is optional because a code legitimately needs only some of them
    — an output VAT code has no recoverable-input account — but a role that is
    REQUIRED by the consequence being projected and is missing here refuses the
    whole row.  An absent account is never substituted with a suspense or a
    default: that is how an unmapped tax silently posts to the wrong place and
    is discovered at a return.
    """

    tax_code_id: UUID
    collected_account_id: UUID | None = None
    paid_account_id: UUID | None = None
    expense_account_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class TaxAccountMap:
    """The effective ERP account mapping, keyed by MODULE tax-code id.

    Constructed by the caller from ERP-owned mapping rows and passed in, so the
    projection stays pure.  Two entries for one tax code is an ambiguity, not a
    last-one-wins: the boundary document lists "multiple plausible accounts"
    among the conditions that make a row `operator_adjudication_required`.
    """

    entries: tuple[TaxCodeAccounts, ...]
    _by_code: Mapping[UUID, TaxCodeAccounts] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        object.__setattr__(self, "entries", entries)
        by_code: dict[UUID, TaxCodeAccounts] = {}
        for entry in entries:
            if not isinstance(entry, TaxCodeAccounts):
                raise TaxAdapterRefusal(
                    f"account map entries must be TaxCodeAccounts, got "
                    f"{type(entry).__name__}"
                )
            if entry.tax_code_id in by_code:
                raise TaxAdapterRefusal(
                    f"tax code {entry.tax_code_id} has more than one account "
                    "mapping; an ambiguous mapping is an operator adjudication, "
                    "not a last-one-wins"
                )
            by_code[entry.tax_code_id] = entry
        object.__setattr__(self, "_by_code", by_code)

    def require(self, tax_code_id: UUID, *, role: str) -> UUID:
        entry = self._by_code.get(tax_code_id)
        if entry is None:
            raise TaxAdapterRefusal(
                f"no ERP account mapping for module tax code {tax_code_id}; "
                "refusing the whole determination set rather than posting a "
                "component to a default account"
            )
        account_id = {
            _ROLE_COLLECTED: entry.collected_account_id,
            _ROLE_PAID: entry.paid_account_id,
            _ROLE_EXPENSE: entry.expense_account_id,
        }[role]
        if account_id is None:
            raise TaxAdapterRefusal(
                f"module tax code {tax_code_id} has no {role} account mapped; "
                "this consequence requires one"
            )
        return account_id


@dataclass(frozen=True, slots=True)
class ConsequencePostingLine:
    """One proposed journal line. Exactly one side carries an amount."""

    account_id: UUID
    debit_amount: Decimal
    credit_amount: Decimal
    description: str
    component_sequence: int | None = None
    tax_code_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.debit_amount < _ZERO or self.credit_amount < _ZERO:
            raise TaxAdapterRefusal(
                "a posting line amount is never negative; direction is the "
                "debit/credit side, not a sign"
            )
        if (self.debit_amount != _ZERO) == (self.credit_amount != _ZERO):
            raise TaxAdapterRefusal(
                "a posting line carries a debit or a credit, never both and "
                f"never neither (debit={self.debit_amount}, "
                f"credit={self.credit_amount})"
            )


@dataclass(frozen=True, slots=True)
class ConsequencePosting:
    """A typed accounting or reportable-only tax consequence.

    A postable result is deliberately self-balancing, unlike the legacy
    `TAXPostingAdapter`, whose own docstring records that it emits tax lines
    alone and leaves the contra to the source document.
    `JournalService._require_balanced` demands equality at persisted scale with
    no tolerance, so a projection that cannot balance itself can only be
    validated after it has been combined with something else — which is
    precisely when a shadow comparison stops being able to attribute a
    difference.

    An all-zero configured treatment is a normal reportable-only result. It has
    no lines and therefore no `JournalInput`, while its distinct zero-rated,
    exempt or out-of-scope components remain reachable for return reporting.
    """

    organization_id: UUID
    determination_set_id: UUID
    source_ref: str
    source_version: str
    source_fingerprint: str
    result_fingerprint: str
    consequence: AccountingConsequence
    document_id: UUID
    document_type: str
    line_id: UUID | None
    entry_date: date
    posting_date: date
    fiscal_period_id: UUID
    currency_code: str
    exchange_rate: Decimal
    lines: tuple[ConsequencePostingLine, ...]
    reportable_zero_components: tuple[TaxDeterminationComponentV1, ...]
    description: str
    correlation_ref: str | None = None
    business_unit_id: UUID | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None
    segment_id: UUID | None = None

    @property
    def is_postable(self) -> bool:
        """Whether this consequence has any journal to post.

        False ONLY when every component was a configured zero treatment. That
        is a complete, correct tax answer that happens to owe nothing — an
        exempt supply — and it is distinguished here, on the returned value,
        rather than by inspecting an exception message.
        """
        return bool(self.lines)

    @property
    def total_debit(self) -> Decimal:
        return sum((line.debit_amount for line in self.lines), _ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((line.credit_amount for line in self.lines), _ZERO)

    def to_journal_input(self) -> JournalInput | None:
        """Render into the accounting owner's own input type, or `None`.

        Returns `None` for a reportable-only consequence (`is_postable` False).
        A journal with no lines must never reach `JournalService`: it would put
        a meaningless zero entry in the ledger for every exempt line, and
        `_require_balanced` would pass it, because zero equals zero.

        Imported lazily: `app.services.finance.gl.journal` pulls in the GL
        service graph, and this package is otherwise importable — and testable —
        without it.  Nothing about the module reaches this call; by the time a
        `ConsequencePosting` exists, every module value has already been turned
        into an ERP account, an ERP side and an ERP amount.
        """
        if not self.lines:
            return None

        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import JournalInput, JournalLineInput

        return JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=self.entry_date,
            posting_date=self.posting_date,
            description=self.description,
            lines=[
                JournalLineInput(
                    account_id=line.account_id,
                    debit_amount=line.debit_amount,
                    credit_amount=line.credit_amount,
                    description=line.description,
                    currency_code=self.currency_code,
                    exchange_rate=self.exchange_rate,
                    business_unit_id=self.business_unit_id,
                    cost_center_id=self.cost_center_id,
                    project_id=self.project_id,
                    segment_id=self.segment_id,
                )
                for line in self.lines
            ],
            reference=self.source_ref,
            currency_code=self.currency_code,
            exchange_rate=self.exchange_rate,
            source_module=SOURCE_MODULE,
            source_document_type=self.document_type,
            source_document_id=self.document_id,
            correlation_id=self.correlation_ref,
        )


def _require_public_set(value: object) -> TaxDeterminationSetV1:
    """Admit only the released public value type, never an ORM row or dict."""
    if not isinstance(value, TaxDeterminationSetV1):
        raise TaxAdapterRefusal(
            f"expected dotmac_tax.TaxDeterminationSetV1, got {type(value).__name__}"
        )
    return value


def _verify_inclusive_arithmetic(result: TaxDeterminationSetV1) -> None:
    """Re-derive the set's own totals rather than trusting them.

    `TaxDeterminationSetV1.__post_init__` already proves the components sum to
    the set's tax and that `net + tax == gross`. What is left, and what only
    makes sense here, is the relationship of `source_amount` to those totals,
    because it encodes the one thing ERP is trying to stop modelling as data:

    - an EXCLUSIVE set was determined ON the source amount, so `source == net`;
    - an INCLUSIVE set had its tax extracted FROM the source amount, so
      `source == gross`.

    The public a3 contract refuses an inclusive component combined with
    any other component, because the source amount is otherwise ambiguous. That
    refusal is mirrored here rather than assumed: a set that reached ERP with an
    inclusive component alongside others would be a module defect, and posting
    it would silently pick one of two incompatible readings of `source_amount`.
    """
    inclusive = [c for c in result.components if c.inclusive]
    if inclusive and len(result.components) > 1:
        raise TaxAdapterRefusal(
            "an inclusive component cannot be combined with any other "
            f"component (set has {len(result.components)}); the source amount "
            "is ambiguous and ERP will not choose a reading"
        )
    if inclusive:
        if result.source_amount != result.gross_amount:
            raise TaxAdapterRefusal(
                f"inclusive set: source {result.source_amount.amount} must equal "
                f"gross {result.gross_amount.amount}"
            )
    elif result.source_amount != result.net_amount:
        raise TaxAdapterRefusal(
            f"exclusive set: source {result.source_amount.amount} must equal net "
            f"{result.net_amount.amount}"
        )


def _component_lines(
    component: TaxDeterminationComponentV1,
    *,
    consequence: AccountingConsequence,
    accounts: TaxAccountMap,
    reversal: bool,
) -> list[ConsequencePostingLine]:
    """The account-side decision, made once, per consequence."""

    def line(
        account_id: UUID, amount: Decimal, *, credit: bool
    ) -> ConsequencePostingLine:
        if reversal:
            credit = not credit
        return ConsequencePostingLine(
            account_id=account_id,
            debit_amount=_ZERO if credit else amount,
            credit_amount=amount if credit else _ZERO,
            description=(
                f"Tax component {component.component_sequence} "
                f"({component.treatment_code})"
            ),
            component_sequence=component.component_sequence,
            tax_code_id=component.tax_code_id,
        )

    tax = component.tax_amount.amount
    if consequence is AccountingConsequence.AP_INPUT_TAX:
        lines: list[ConsequencePostingLine] = []
        recoverable = component.recoverable_amount.amount
        non_recoverable = component.non_recoverable_amount.amount
        if recoverable != _ZERO:
            lines.append(
                line(
                    accounts.require(component.tax_code_id, role=_ROLE_PAID),
                    recoverable,
                    credit=False,
                )
            )
        if non_recoverable != _ZERO:
            lines.append(
                line(
                    accounts.require(component.tax_code_id, role=_ROLE_EXPENSE),
                    non_recoverable,
                    credit=False,
                )
            )
        return lines
    return [
        line(
            accounts.require(component.tax_code_id, role=_ROLE_COLLECTED),
            tax,
            credit=True,
        )
    ]


def project_determination_set(
    result: TaxDeterminationSetV1,
    *,
    application: TaxApplicationContextV1,
    accounts: TaxAccountMap,
    expected_fingerprint: str,
    fiscal_period_id: UUID,
) -> ConsequencePosting:
    """Verify, resolve and project a public module result. No write or session.

    `expected_fingerprint` is the fingerprint ERP recorded when it SUBMITTED the
    source fact.  Comparing it here is the check the boundary document asks for
    ("changed fingerprints refuse the whole source row") and it is the reason a
    set that was quietly re-determined after the document was priced cannot be
    posted against it.

    `fiscal_period_id` is `PeriodGuardService.require_open_period(...)`'s return
    value.  It is a required argument rather than an internal lookup so that a
    pure function can still make "closed fiscal periods refuse the row" a
    precondition a caller cannot skip.
    """
    result = _require_public_set(result)
    if not isinstance(application, TaxApplicationContextV1):
        raise TaxAdapterRefusal(
            f"expected a TaxApplicationContextV1, got {type(application).__name__}"
        )
    if not isinstance(accounts, TaxAccountMap):
        raise TaxAdapterRefusal(
            f"expected a TaxAccountMap, got {type(accounts).__name__}"
        )
    if not isinstance(fiscal_period_id, UUID):
        raise TaxAdapterRefusal(
            "an open fiscal period id is required; pass the value "
            "PeriodGuardService.require_open_period() returned"
        )
    if result.tenant_id != application.organization_id:
        raise TaxAdapterRefusal(
            "determination tenant does not match the ERP posting organization "
            f"({result.tenant_id} != {application.organization_id})"
        )
    if result.source_fingerprint != expected_fingerprint:
        raise TaxAdapterRefusal(
            "determination fingerprint changed since ERP submitted the fact "
            f"({result.source_fingerprint!r} != {expected_fingerprint!r}); "
            "refusing the whole source row"
        )
    expected_consequence = _CONSEQUENCE_BY_TRANSACTION_SIDE.get(result.transaction_side)
    if expected_consequence is None:
        raise TaxAdapterRefusal(
            "determination has unsupported transaction side "
            f"{result.transaction_side!r}"
        )
    if application.consequence is not expected_consequence:
        raise TaxAdapterRefusal(
            "ERP consequence contradicts the determination transaction side: "
            f"{result.transaction_side!r} requires "
            f"{expected_consequence.value!r}, got "
            f"{application.consequence.value!r}"
        )
    _verify_inclusive_arithmetic(result)

    lines: list[ConsequencePostingLine] = []
    reportable_zero: list[TaxDeterminationComponentV1] = []
    for component in result.components:
        if component.determination_set_id != result.determination_set_id:
            raise TaxAdapterRefusal(
                f"component {component.determination_id} belongs to determination "
                f"set {component.determination_set_id}, not "
                f"{result.determination_set_id}"
            )
        if not component.has_tax_consequence:
            if not component.is_reportable_zero:
                raise TaxAdapterRefusal(
                    f"component {component.component_sequence} is "
                    f"{component.treatment_code} with zero tax; a standard-rated "
                    "component producing nothing is a determination defect, not "
                    "a line ERP may drop"
                )
            # A zero-rated / exempt / out-of-scope component still belongs in a
            # return box, so it is CARRIED rather than discarded for having
            # produced no journal line.
            reportable_zero.append(component)
            continue
        lines.extend(
            _component_lines(
                component,
                consequence=application.consequence,
                accounts=accounts,
                reversal=application.reversal,
            )
        )

    if not lines and not reportable_zero:
        raise TaxAdapterRefusal(
            "the determination set produced no postable and no reportable component"
        )

    # An all-zero set is a REPORTABLE-ONLY consequence, not a refusal (C1.1).
    #
    # Every component being `zero_rated` / `exempt` / `out_of_scope` is the most
    # ordinary answer in tax — an exempt supply — and it still belongs in a
    # return box. Raising here put that outcome on the same path as a genuine
    # defect (ambiguous account, changed fingerprint, unbalanced arithmetic) and
    # left `reportable_zero_components` unreachable, so a caller could not act on
    # the very thing this adapter carries them for. A caller writing the natural
    # `except TaxAdapterRefusal: log and skip` then silently dropped a statutory
    # obligation. The components are returned instead, with `is_postable` False
    # and no counterpart line, and `to_journal_input()` yields `None`.
    if lines:
        total_tax = result.tax_amount.amount
        counterpart_credit = (
            application.consequence is AccountingConsequence.AP_INPUT_TAX
        )
        if application.reversal:
            counterpart_credit = not counterpart_credit
        lines.append(
            ConsequencePostingLine(
                account_id=application.counterpart_account_id,
                debit_amount=_ZERO if counterpart_credit else total_tax,
                credit_amount=total_tax if counterpart_credit else _ZERO,
                description=f"Tax counterpart for {result.source_ref}",
            )
        )

    posting = ConsequencePosting(
        organization_id=application.organization_id,
        determination_set_id=result.determination_set_id,
        source_ref=result.source_ref,
        source_version=result.source_version,
        source_fingerprint=result.source_fingerprint,
        result_fingerprint=result.result_fingerprint,
        consequence=application.consequence,
        document_id=application.document_id,
        document_type=application.document_type,
        line_id=application.line_id,
        entry_date=result.occurred_on,
        posting_date=application.posting_date,
        fiscal_period_id=fiscal_period_id,
        currency_code=result.tax_amount.currency.code,
        exchange_rate=application.exchange_rate,
        lines=tuple(lines),
        reportable_zero_components=tuple(reportable_zero),
        description=(
            application.description
            or f"{application.consequence.value} for "
            f"{application.document_type} {result.source_ref}"
        ),
        correlation_ref=application.correlation_ref,
        business_unit_id=application.business_unit_id,
        cost_center_id=application.cost_center_id,
        project_id=application.project_id,
        segment_id=application.segment_id,
    )
    if posting.total_debit != posting.total_credit:
        raise TaxAdapterRefusal(
            "projected posting is unbalanced: debits "
            f"{posting.total_debit}, credits {posting.total_credit}. "
            "JournalService._require_balanced enforces equality at persisted "
            "scale with no tolerance, so this is refused here, where the "
            "component that caused it can still be named."
        )
    return posting
