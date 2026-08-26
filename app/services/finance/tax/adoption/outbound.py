"""Public tax determination result -> ERP accounting consequence.

The module owns the immutable determination result; ERP separately owns the
posting context, account mapping, fiscal-period proof and FX evidence. This
projection is pure and shadow-safe: it validates the public result, creates a
complete balanced proposal, and performs no database write or authority switch.

Purity is load-bearing: every refusal happens before a partial write, and the
preconditions this function cannot prove become required typed arguments.
``fiscal_period_id`` is evidence from ``require_open_period``;
``expected_fingerprint`` is the fingerprint recorded when ERP submitted the
fact; ``TaxAccountMap`` is keyed by module tax-code identity rather than the
legacy calculator's different identifier space. A module result never chooses
an account, period, debit/credit side or FX rate and never writes GL directly.

The consequence shapes preserve C1's accounting decisions. Output tax,
withholding and payroll tax credit the authority liability; input tax debits
the exact recoverable/non-recoverable split; the required counterpart makes the
proposal self-balancing. Reversal flips every side rather than changing the
module's non-negative determination amounts. Explicit zero treatments remain
reportable even though they correctly produce no journal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from dotmac_kernel.money import Money

from app.services.finance.tax.adoption.contracts import (
    TREATMENTS_WITHOUT_A_TAX_CONSEQUENCE,
    AccountingConsequence,
    TaxAdapterRefusal,
    TaxApplicationContextV1,
    TaxPostingFXEvidenceV1,
)
from app.services.finance.tax.adoption.fx import allocate_functional_line_amounts

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dotmac_tax import TaxDeterminationComponentV1, TaxDeterminationSetV1

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
_KNOWN_TREATMENTS = TREATMENTS_WITHOUT_A_TAX_CONSEQUENCE | {"standard_rated"}
_KNOWN_CALCULATION_BASES = frozenset({"source_amount", "source_plus_prior_tax"})

# ERP's existing tax journals already use this source module. The adoption
# path must not split one ledger history into two names.
SOURCE_MODULE = "TAX"

_ROLE_COLLECTED = "collected"
_ROLE_PAID = "paid"
_ROLE_EXPENSE = "expense"


@dataclass(frozen=True, slots=True)
class TaxCodeAccounts:
    """ERP's effective account choice for one module tax-code identity."""

    tax_code_id: UUID
    collected_account_id: UUID | None = None
    paid_account_id: UUID | None = None
    expense_account_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class TaxAccountMap:
    """Effective ERP account mappings, refusing duplicates and missing roles."""

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
                    "account map entries must be TaxCodeAccounts, got "
                    f"{type(entry).__name__}"
                )
            if entry.tax_code_id in by_code:
                raise TaxAdapterRefusal(
                    f"tax code {entry.tax_code_id} has more than one account "
                    "mapping; ambiguous mapping requires operator adjudication"
                )
            by_code[entry.tax_code_id] = entry
        object.__setattr__(self, "_by_code", by_code)

    def require(self, tax_code_id: UUID, *, role: str) -> UUID:
        role_field = {
            _ROLE_COLLECTED: "collected_account_id",
            _ROLE_PAID: "paid_account_id",
            _ROLE_EXPENSE: "expense_account_id",
        }.get(role)
        if role_field is None:
            raise TaxAdapterRefusal(f"unknown ERP tax account role {role!r}")
        entry = self._by_code.get(tax_code_id)
        if entry is None:
            raise TaxAdapterRefusal(
                f"no ERP account mapping for module tax code {tax_code_id}"
            )
        account_id = getattr(entry, role_field)
        if account_id is None:
            raise TaxAdapterRefusal(
                f"module tax code {tax_code_id} has no {role} account mapped"
            )
        return account_id


@dataclass(frozen=True, slots=True)
class ConsequencePostingLine:
    """One proposed transaction-currency journal line."""

    account_id: UUID
    debit_amount: Decimal
    credit_amount: Decimal
    description: str
    component_sequence: int | None = None
    tax_code_id: UUID | None = None

    def __post_init__(self) -> None:
        for amount, label in (
            (self.debit_amount, "debit"),
            (self.credit_amount, "credit"),
        ):
            if isinstance(amount, (bool, float)) or not isinstance(amount, Decimal):
                raise TaxAdapterRefusal(f"posting-line {label} must be Decimal")
            if not amount.is_finite() or amount < _ZERO:
                raise TaxAdapterRefusal(
                    f"posting-line {label} must be finite and non-negative"
                )
        if (self.debit_amount != _ZERO) == (self.credit_amount != _ZERO):
            raise TaxAdapterRefusal(
                "a posting line carries exactly one debit/credit side"
            )


@dataclass(frozen=True, slots=True)
class ConsequencePosting:
    """Complete ERP proposal, including report-only outcomes and FX evidence."""

    organization_id: UUID
    determination_set_id: UUID
    source_ref: str
    source_version: str
    source_fingerprint: str
    consequence: AccountingConsequence
    document_id: UUID
    document_type: str
    line_id: UUID | None
    entry_date: date
    posting_date: date
    fiscal_period_id: UUID
    fx_evidence: TaxPostingFXEvidenceV1
    lines: tuple[ConsequencePostingLine, ...]
    reportable_zero_components: tuple[TaxDeterminationComponentV1, ...]
    description: str
    correlation_ref: str | None = None
    business_unit_id: UUID | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None
    segment_id: UUID | None = None

    @property
    def currency_code(self) -> str:
        return self.fx_evidence.transaction_currency.code

    @property
    def exchange_rate(self) -> Decimal:
        return self.fx_evidence.exchange_rate

    @property
    def is_postable(self) -> bool:
        return bool(self.lines)

    @property
    def total_debit(self) -> Decimal:
        return sum((line.debit_amount for line in self.lines), _ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((line.credit_amount for line in self.lines), _ZERO)

    def to_journal_input(self) -> JournalInput | None:
        """Render ERP's journal input, or ``None`` for a report-only result."""

        if not self.lines:
            return None

        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import JournalInput, JournalLineInput

        functional = allocate_functional_line_amounts(
            [(line.debit_amount, line.credit_amount) for line in self.lines],
            evidence=self.fx_evidence,
        )
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
                    debit_amount_functional=functional_line.debit_amount,
                    credit_amount_functional=functional_line.credit_amount,
                    description=line.description,
                    currency_code=self.currency_code,
                    exchange_rate=self.exchange_rate,
                    business_unit_id=self.business_unit_id,
                    cost_center_id=self.cost_center_id,
                    project_id=self.project_id,
                    segment_id=self.segment_id,
                )
                for line, functional_line in zip(
                    self.lines, functional, strict=True
                )
            ],
            reference=self.source_ref,
            currency_code=self.currency_code,
            exchange_rate=self.exchange_rate,
            exchange_rate_type_id=self.fx_evidence.rate_type_id,
            source_module=SOURCE_MODULE,
            source_document_type=self.document_type,
            source_document_id=self.document_id,
            correlation_id=self.correlation_ref,
        )


def _require_public_set(value: object) -> TaxDeterminationSetV1:
    """Require the released read contract without a module-level dependency."""

    try:
        from dotmac_tax import TaxDeterminationSetV1
    except ImportError as exc:
        raise TaxAdapterRefusal(
            "dotmac-tax public read contract is unavailable; pin the verified "
            "0.1.0a3 release before composing this C2 consumer"
        ) from exc
    if not isinstance(value, TaxDeterminationSetV1):
        raise TaxAdapterRefusal(
            "expected dotmac_tax.TaxDeterminationSetV1, got "
            f"{type(value).__name__}"
        )
    return value


def _require_result_money(value: object, label: str) -> Money:
    if not isinstance(value, Money):
        raise TaxAdapterRefusal(f"{label} must be kernel Money")
    if not value.amount.is_finite() or value.amount < _ZERO:
        raise TaxAdapterRefusal(f"{label} must be finite and non-negative")
    return value


def _verify_public_result(result: TaxDeterminationSetV1) -> None:
    """Revalidate the untrusted public value at ERP's consequence boundary."""

    if not result.components:
        raise TaxAdapterRefusal(
            "a determination set needs a configured component; no component is "
            "not an out-of-scope determination"
        )
    set_money = {
        name: _require_result_money(getattr(result, name), name.replace("_", " "))
        for name in ("source_amount", "net_amount", "tax_amount", "gross_amount")
    }
    currency = set_money["tax_amount"].currency
    if any(money.currency != currency for money in set_money.values()):
        raise TaxAdapterRefusal("determination-set money currencies/scales differ")

    sequences: list[int] = []
    component_tax = _ZERO
    for component in result.components:
        sequences.append(component.component_sequence)
        if component.determination_set_id != result.determination_set_id:
            raise TaxAdapterRefusal(
                f"component {component.component_sequence} belongs to another set"
            )
        if component.component_sequence < 1 or component.rule_version < 1:
            raise TaxAdapterRefusal("component sequence/rule version must be positive")
        if component.treatment_code not in _KNOWN_TREATMENTS:
            raise TaxAdapterRefusal(
                f"unknown tax treatment {component.treatment_code!r}"
            )
        if component.calculation_base_code not in _KNOWN_CALCULATION_BASES:
            raise TaxAdapterRefusal(
                f"unknown calculation base {component.calculation_base_code!r}"
            )
        amounts = {
            name: _require_result_money(
                getattr(component, name),
                f"component {component.component_sequence} {name.replace('_', ' ')}",
            )
            for name in (
                "base_amount",
                "tax_amount",
                "recoverable_amount",
                "non_recoverable_amount",
            )
        }
        if any(money.currency != currency for money in amounts.values()):
            raise TaxAdapterRefusal(
                f"component {component.component_sequence} currency/scale differs"
            )
        if amounts["recoverable_amount"] + amounts["non_recoverable_amount"] != amounts["tax_amount"]:
            raise TaxAdapterRefusal(
                f"component {component.component_sequence} recovery split is invalid"
            )
        if (
            component.treatment_code in TREATMENTS_WITHOUT_A_TAX_CONSEQUENCE
            and amounts["tax_amount"].amount != _ZERO
        ):
            raise TaxAdapterRefusal(
                f"zero treatment {component.component_sequence} carries tax"
            )
        component_tax += amounts["tax_amount"].amount

    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        raise TaxAdapterRefusal(
            f"component sequence must be strictly increasing and unique: {sequences}"
        )
    if component_tax != set_money["tax_amount"].amount:
        raise TaxAdapterRefusal(
            f"components total {component_tax}, set claims {set_money['tax_amount'].amount}"
        )
    if set_money["net_amount"] + set_money["tax_amount"] != set_money["gross_amount"]:
        raise TaxAdapterRefusal("determination net plus tax does not equal gross")

    inclusive = [component for component in result.components if component.inclusive]
    if inclusive and len(result.components) > 1:
        raise TaxAdapterRefusal(
            "an inclusive component cannot be combined with another component"
        )
    if inclusive and set_money["source_amount"] != set_money["gross_amount"]:
        raise TaxAdapterRefusal("inclusive result source must equal gross")
    if not inclusive and set_money["source_amount"] != set_money["net_amount"]:
        raise TaxAdapterRefusal("exclusive result source must equal net")


def _component_lines(
    component: TaxDeterminationComponentV1,
    *,
    consequence: AccountingConsequence,
    accounts: TaxAccountMap,
    reversal: bool,
) -> list[ConsequencePostingLine]:
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

    if consequence is AccountingConsequence.AP_INPUT_TAX:
        lines: list[ConsequencePostingLine] = []
        if component.recoverable_amount.amount != _ZERO:
            lines.append(
                line(
                    accounts.require(component.tax_code_id, role=_ROLE_PAID),
                    component.recoverable_amount.amount,
                    credit=False,
                )
            )
        if component.non_recoverable_amount.amount != _ZERO:
            lines.append(
                line(
                    accounts.require(component.tax_code_id, role=_ROLE_EXPENSE),
                    component.non_recoverable_amount.amount,
                    credit=False,
                )
            )
        return lines
    return [
        line(
            accounts.require(component.tax_code_id, role=_ROLE_COLLECTED),
            component.tax_amount.amount,
            credit=True,
        )
    ]


def project_determination_set(
    determination: TaxDeterminationSetV1,
    *,
    application: TaxApplicationContextV1,
    accounts: TaxAccountMap,
    expected_fingerprint: str,
    fiscal_period_id: UUID,
) -> ConsequencePosting:
    """Validate and project the public result. No write and no authority change."""

    result = _require_public_set(determination)
    _verify_public_result(result)
    if not isinstance(application, TaxApplicationContextV1):
        raise TaxAdapterRefusal("application must be TaxApplicationContextV1")
    if not isinstance(accounts, TaxAccountMap):
        raise TaxAdapterRefusal("accounts must be TaxAccountMap")
    if not isinstance(fiscal_period_id, UUID):
        raise TaxAdapterRefusal(
            "pass the fiscal period id returned by require_open_period()"
        )
    if result.tenant_id != application.organization_id:
        raise TaxAdapterRefusal(
            "determination tenant and ERP application organization differ"
        )
    if result.source_fingerprint != expected_fingerprint:
        raise TaxAdapterRefusal(
            "determination fingerprint changed since ERP submitted the fact"
        )
    application.fx_evidence.require_transaction_currency(
        result.tax_amount.currency
    )

    lines: list[ConsequencePostingLine] = []
    reportable_zero: list[TaxDeterminationComponentV1] = []
    for component in result.components:
        if not component.has_tax_consequence:
            if not component.is_reportable_zero:
                raise TaxAdapterRefusal(
                    f"standard-rated component {component.component_sequence} "
                    "has no tax consequence"
                )
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

    if lines:
        counterpart_credit = application.consequence is AccountingConsequence.AP_INPUT_TAX
        if application.reversal:
            counterpart_credit = not counterpart_credit
        lines.append(
            ConsequencePostingLine(
                account_id=application.counterpart_account_id,
                debit_amount=_ZERO if counterpart_credit else result.tax_amount.amount,
                credit_amount=result.tax_amount.amount if counterpart_credit else _ZERO,
                description=f"Tax counterpart for {result.source_ref}",
            )
        )
    elif not reportable_zero:  # pragma: no cover - validated above
        raise TaxAdapterRefusal("determination has no postable or reportable result")

    posting = ConsequencePosting(
        organization_id=application.organization_id,
        determination_set_id=result.determination_set_id,
        source_ref=result.source_ref,
        source_version=result.source_version,
        source_fingerprint=result.source_fingerprint,
        consequence=application.consequence,
        document_id=application.document_id,
        document_type=application.document_type,
        line_id=application.line_id,
        entry_date=result.occurred_on,
        posting_date=application.posting_date,
        fiscal_period_id=fiscal_period_id,
        fx_evidence=application.fx_evidence,
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
            "projected posting is unbalanced in transaction currency: "
            f"debits {posting.total_debit}, credits {posting.total_credit}"
        )
    return posting
