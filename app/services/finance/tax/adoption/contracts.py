"""The two ERP-owned typed seams for `dotmac-tax` (adoption ledger item C1).

`docs/architecture/dotmac-tax-adoption-boundary.md` § "Typed ERP seams" names
both of these and says what they must carry.  They exist so that:

- **ERP never hands the module a free-form payload.**  A source fact is a
  frozen, validated value object, not a dict assembled at a call site.
- **The module never learns anything about ERP.**  `dotmac_tax` sees opaque
  string references (`counterparty_ref`, `supply_ref`, `place_ref`,
  `source_ref`, `evidence_ref`) and exact money.  It is given no account, no
  journal, no ERP identity and no ERP enum.
- **ERP never learns anything about tax internals.**  Nothing in this module
  imports `dotmac_tax`; the outbound contract MIRRORS the reviewable fields of
  an approved determination set as ERP-owned types, which is what lets the
  consequence path be written, reviewed and tested before the package is ever
  pinned.

Money is kernel `Money`, built through `app.services.finance.money_boundary`
— ERP's existing, single boundary owner for exact money.  That is deliberate
reuse rather than a second validator: `to_boundary_money` already refuses
floats, refuses an unknown currency instead of guessing two decimals, and
refuses excess minor-unit precision instead of rounding it away.  A tax base is
exactly the kind of value that must not be quietly re-scaled, and the module
independently validates the fact's currency AND minor units against its
jurisdiction, so a guess here would surface as an opaque module rejection
rather than an ERP-side refusal naming the offending row.

## What is deliberately NOT on `ERPSourceTaxFactV1`

**A tax code.**  ERP's current calculator is handed `tax_code_ids` by its
caller (`app.services.finance.tax.tax_calculation.LineTaxInput`,
`InvoiceLineTaxInput`).  The module decides which codes apply from the fact
signature plus effective party/supply/place classifications, and category
selection is fail-closed.  Passing ERP's chosen codes through would reinstate
the very decision ERP is retiring, so they travel as `observed_tax_code_refs`
— evidence for the shadow comparator, never an input to determination.

**A sign.**  `dotmac_tax.TaxFact.base_amount` must be non-negative and the
module has no reversal concept.  ERP credit notes are legitimately negative
(`app/services/finance/ar/invoice.py`: "CREDIT_NOTE type is legitimately
negative").  The MAGNITUDE is the fact; the DIRECTION is an ERP accounting
consequence, carried by `reversal` here and applied by the consequence
adapter.  Normalising a credit note into a positive liability, and `abs()`-ing
an ordinary invoice line that has gone negative for some other reason, are
both wrong — so the mappers require the sign to agree with the document type
and refuse otherwise.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.money import Money

__all__ = [
    "RECOGNITION_BASIS_ACCRUAL",
    "RECOGNITION_BASIS_CASH",
    "RECOGNITION_BASIS_PAYROLL_PERIOD",
    "TREATMENTS_WITHOUT_A_TAX_CONSEQUENCE",
    "AccountingConsequence",
    "ApplyTaxDeterminationSetV1",
    "ERPSourceTaxFactV1",
    "SourceFactFamily",
    "TaxAdapterRefusal",
    "TaxDeterminationComponentV1",
    "TransactionSide",
]


class TaxAdapterRefusal(ValueError):
    """A source fact or determination set that ERP refuses to translate.

    Deliberately a refusal and not a coercion.  Every branch that raises this
    is a place where the alternative would be to guess a rate, a sign, a
    currency, an account or an order — and a guess here does not surface as an
    error.  It surfaces as a return that balances and is wrong.
    """


class SourceFactFamily(str, enum.Enum):
    """The ERP fact families this adapter covers, one per named mapper.

    Closed on ERP's side on purpose: a family is a mapper PLUS a shadow cohort
    (`docs/architecture/dotmac-tax-adoption-boundary.md` § "Shadow cohorts and
    gates"), so adding a member is a reviewed change rather than a new string
    literal at a call site.  The module-facing `fact_kind` stays an open
    string — that vocabulary belongs to the module, and ERP does not get to
    close it.
    """

    AR_INVOICE_LINE = "ar_invoice_line"
    AP_INVOICE_LINE = "ap_invoice_line"
    AR_SETTLEMENT_WITHHOLDING = "ar_settlement_withholding"
    AP_SETTLEMENT_WITHHOLDING = "ap_settlement_withholding"
    EXPENSE_ENTRY = "expense_entry"
    PAYROLL_TAXABLE_PAY = "payroll_taxable_pay"


class TransactionSide(str, enum.Enum):
    """Mirrors `ck_tax_rules_side` in the module's schema.

    An ERP enum rather than a bare string so that a typo fails at the mapper,
    instead of matching no rule and producing an empty determination that is
    indistinguishable from a legitimate out-of-scope outcome.
    """

    INPUT = "input"
    OUTPUT = "output"
    WITHHOLDING = "withholding"
    LIABILITY = "liability"


#: Module-facing recognition bases ERP declares.  Open strings on the module
#: side; named here so six mappers cannot drift into six spellings.  ERP's own
#: `TaxRecognitionBasis` already uses "accrual"/"cash" for the first two.
RECOGNITION_BASIS_ACCRUAL = "accrual"
RECOGNITION_BASIS_CASH = "cash"
RECOGNITION_BASIS_PAYROLL_PERIOD = "payroll_period"

#: Treatments that are a real, configured legal answer with no money to post.
#: `zero_rated`, `exempt` and `out_of_scope` are DISTINCT treatments that all
#: produce zero tax, and the module keeps them distinct on purpose.  ERP keeps
#: them distinct too: a zero-value component is still REPORTABLE — it belongs
#: in a return box — so the consequence adapter records it separately rather
#: than dropping it for having produced no journal line.
TREATMENTS_WITHOUT_A_TAX_CONSEQUENCE: frozenset[str] = frozenset(
    {"zero_rated", "exempt", "out_of_scope"}
)

#: The module's `ck_tax_determinations_treatment` vocabulary in full.
_KNOWN_TREATMENTS: frozenset[str] = TREATMENTS_WITHOUT_A_TAX_CONSEQUENCE | {
    "standard_rated"
}

#: The module's `ck_tax_determinations_calculation_base` vocabulary.
_KNOWN_CALCULATION_BASES: frozenset[str] = frozenset(
    {"source_amount", "source_plus_prior_tax"}
)

_ZERO = Decimal("0")


def _require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaxAdapterRefusal(f"{label} is required and must be non-empty")
    return value.strip()


def _require_optional_text(value: str | None, label: str) -> str | None:
    return None if value is None else _require_text(value, label)


def _require_money(value: Money, label: str) -> Money:
    if not isinstance(value, Money):
        raise TaxAdapterRefusal(
            f"{label} must be kernel Money built through money_boundary, "
            f"got {type(value).__name__}"
        )
    if value.amount < _ZERO:
        raise TaxAdapterRefusal(
            f"{label} must be non-negative, got {value.amount} "
            f"{value.currency.code}; direction is an ERP consequence, never a "
            "negative tax base"
        )
    return value


@dataclass(frozen=True, slots=True)
class ERPSourceTaxFactV1:
    """One exact, versioned, evidenced ERP observation offered for determination.

    Carries every field the boundary document's `ERPSourceTaxFactV1` list
    requires.  `source_ref` + `source_version` are the module's idempotency
    key: the module fingerprints the fact and refuses a reused version whose
    facts changed, so a caller that mutates a document MUST advance
    `source_version` rather than resubmit the old one.
    """

    organization_id: UUID
    jurisdiction_id: UUID
    family: SourceFactFamily
    fact_kind: str
    recognition_basis_code: str
    transaction_side: TransactionSide
    occurred_on: date
    base_amount: Money
    source_ref: str
    source_version: str
    evidence_ref: str
    document_id: UUID
    line_id: UUID | None = None
    counterparty_ref: str | None = None
    supply_ref: str | None = None
    place_ref: str | None = None
    party_category: str | None = None
    supply_category: str | None = None
    place_code: str | None = None
    correlation_ref: str | None = None
    reversal: bool = False
    observed_tax_code_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.family, SourceFactFamily):
            raise TaxAdapterRefusal("family must be a SourceFactFamily member")
        if not isinstance(self.transaction_side, TransactionSide):
            raise TaxAdapterRefusal("transaction side must be a TransactionSide member")
        object.__setattr__(
            self, "fact_kind", _require_text(self.fact_kind, "fact kind")
        )
        object.__setattr__(
            self,
            "recognition_basis_code",
            _require_text(self.recognition_basis_code, "recognition basis code"),
        )
        _require_money(self.base_amount, "base amount")
        for name in ("source_ref", "source_version", "evidence_ref"):
            object.__setattr__(
                self, name, _require_text(getattr(self, name), name.replace("_", " "))
            )
        for name in (
            "counterparty_ref",
            "supply_ref",
            "place_ref",
            "party_category",
            "supply_category",
            "place_code",
            "correlation_ref",
        ):
            object.__setattr__(
                self,
                name,
                _require_optional_text(getattr(self, name), name.replace("_", " ")),
            )
        object.__setattr__(
            self, "observed_tax_code_refs", tuple(self.observed_tax_code_refs)
        )

    @property
    def tenant_id(self) -> UUID:
        """The module tenant scope, DERIVED — never separately stored.

        ERP's same-UUID mapping (`app/tenancy.py`,
        `docs/architecture/organization-tenant-boundary.md`) is
        `tenant_id == organization_id`.  Deriving it here means a caller
        cannot pass a tenant that disagrees with the organization it already
        scoped its query by.
        """
        return self.organization_id

    @property
    def currency_code(self) -> str:
        return self.base_amount.currency.code

    @property
    def minor_units(self) -> int:
        return self.base_amount.currency.minor_units


@dataclass(frozen=True, slots=True)
class TaxDeterminationComponentV1:
    """One immutable component of an approved determination set.

    A MIRROR of `mod_tax.tax_determinations`' reviewable columns, not an
    import of them: ERP holds no opinion about how the module stored the row,
    and the module is never asked about an account.
    """

    component_sequence: int
    tax_code_id: UUID
    rule_id: UUID
    rule_version: int
    treatment_code: str
    calculation_base_code: str
    inclusive: bool
    base_amount: Money
    tax_amount: Money
    recoverable_amount: Money
    non_recoverable_amount: Money
    party_classification_id: UUID | None = None
    supply_classification_id: UUID | None = None
    place_classification_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.component_sequence < 1:
            raise TaxAdapterRefusal(
                f"component sequence must be positive, got {self.component_sequence}"
            )
        if self.rule_version < 1:
            raise TaxAdapterRefusal(
                f"rule version must be positive, got {self.rule_version}"
            )
        if self.treatment_code not in _KNOWN_TREATMENTS:
            raise TaxAdapterRefusal(
                f"unknown treatment {self.treatment_code!r}; ERP refuses to "
                f"post a treatment it cannot classify (known: "
                f"{sorted(_KNOWN_TREATMENTS)})"
            )
        if self.calculation_base_code not in _KNOWN_CALCULATION_BASES:
            raise TaxAdapterRefusal(
                f"unknown calculation base {self.calculation_base_code!r} "
                f"(known: {sorted(_KNOWN_CALCULATION_BASES)})"
            )
        for name in (
            "base_amount",
            "tax_amount",
            "recoverable_amount",
            "non_recoverable_amount",
        ):
            _require_money(getattr(self, name), name.replace("_", " "))
        if (
            self.recoverable_amount.currency != self.tax_amount.currency
            or self.non_recoverable_amount.currency != self.tax_amount.currency
        ):
            raise TaxAdapterRefusal(
                "component recovery split must be in the component's tax currency"
            )
        split = self.recoverable_amount + self.non_recoverable_amount
        if split != self.tax_amount:
            raise TaxAdapterRefusal(
                "component recovery split does not equal its tax amount: "
                f"{self.recoverable_amount.amount} + "
                f"{self.non_recoverable_amount.amount} != {self.tax_amount.amount}"
            )
        if (
            self.treatment_code in TREATMENTS_WITHOUT_A_TAX_CONSEQUENCE
            and self.tax_amount.amount != _ZERO
        ):
            raise TaxAdapterRefusal(
                f"{self.treatment_code} component carries tax "
                f"{self.tax_amount.amount}; a zero treatment holding money is a "
                "determination defect, not an ERP rounding question"
            )

    @property
    def has_tax_consequence(self) -> bool:
        """Whether this component produces journal lines at all.

        Note this is about MONEY, not about reportability: a `zero_rated`
        component still belongs in a return box.  The consequence adapter
        records those separately instead of discarding them.
        """
        return self.tax_amount.amount != _ZERO


class AccountingConsequence(str, enum.Enum):
    """What ERP intends to post — chosen by ERP, never by the module.

    The module produces amounts and identities; which accounts they land on,
    and on which side, is the decision ERP keeps (boundary doc § "Outcome":
    "ERP remains the accounting owner").
    """

    AR_OUTPUT_TAX = "ar_output_tax"
    AP_INPUT_TAX = "ap_input_tax"
    WITHHOLDING_PAYABLE = "withholding_payable"
    PAYROLL_TAX_PAYABLE = "payroll_tax_payable"


@dataclass(frozen=True, slots=True)
class ApplyTaxDeterminationSetV1:
    """An approved determination set, plus the ERP target it applies to.

    `source_fingerprint` is CARRIED and re-checked rather than trusted: the
    consequence adapter refuses a set whose fingerprint does not match the one
    ERP recorded when it submitted the fact.  That is what stops a silently
    re-determined set from being posted against a document that was priced on
    the previous one.

    `counterpart_account_id` is required because ERP's journal service demands
    exact balance (`JournalService._require_balanced` — equality at persisted
    scale, not a tolerance).  The existing `TAXPostingAdapter` emits tax lines
    alone and leaves the contra to "the source document"; requiring the
    counterpart here means this projection is a COMPLETE, balanced posting
    request that can be validated on its own.
    """

    organization_id: UUID
    determination_set_id: UUID
    source_ref: str
    source_version: str
    source_fingerprint: str
    occurred_on: date
    posting_date: date
    consequence: AccountingConsequence
    components: tuple[TaxDeterminationComponentV1, ...]
    source_amount: Money
    net_amount: Money
    tax_amount: Money
    gross_amount: Money
    document_id: UUID
    document_type: str
    counterpart_account_id: UUID
    line_id: UUID | None = None
    exchange_rate: Decimal = Decimal("1.0")
    reversal: bool = False
    correlation_ref: str | None = None
    description: str | None = None
    business_unit_id: UUID | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None
    segment_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.consequence, AccountingConsequence):
            raise TaxAdapterRefusal("consequence must be an AccountingConsequence")
        for name in (
            "source_ref",
            "source_version",
            "source_fingerprint",
            "document_type",
        ):
            object.__setattr__(
                self, name, _require_text(getattr(self, name), name.replace("_", " "))
            )
        object.__setattr__(
            self,
            "correlation_ref",
            _require_optional_text(self.correlation_ref, "correlation ref"),
        )
        for name in ("source_amount", "net_amount", "tax_amount", "gross_amount"):
            _require_money(getattr(self, name), name.replace("_", " "))
        object.__setattr__(self, "components", tuple(self.components))
        if not self.components:
            raise TaxAdapterRefusal(
                "a determination set with no components is not an empty tax "
                "answer — a configured zero treatment is. Refusing rather than "
                "posting nothing silently."
            )

        currency = self.tax_amount.currency
        for name in ("source_amount", "net_amount", "gross_amount"):
            if getattr(self, name).currency != currency:
                raise TaxAdapterRefusal(
                    f"{name.replace('_', ' ')} is {getattr(self, name).currency.code} "
                    f"but the set is {currency.code}"
                )
        for component in self.components:
            if component.tax_amount.currency != currency:
                raise TaxAdapterRefusal(
                    f"component {component.component_sequence} is "
                    f"{component.tax_amount.currency.code} but the set is "
                    f"{currency.code}"
                )

        sequences = [component.component_sequence for component in self.components]
        if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
            raise TaxAdapterRefusal(
                "components must arrive in a strictly increasing, unique "
                f"calculation order, got {sequences}; a compound tax computed "
                "out of order is arithmetically wrong, not merely untidy"
            )

        component_tax = sum(
            (component.tax_amount.amount for component in self.components), _ZERO
        )
        if component_tax != self.tax_amount.amount:
            raise TaxAdapterRefusal(
                f"components total {component_tax} but the set claims "
                f"{self.tax_amount.amount}"
            )
        if self.net_amount + self.tax_amount != self.gross_amount:
            raise TaxAdapterRefusal(
                f"net {self.net_amount.amount} + tax {self.tax_amount.amount} "
                f"does not equal gross {self.gross_amount.amount}"
            )

    @property
    def currency_code(self) -> str:
        return self.tax_amount.currency.code
