"""ERP-owned inputs around the public ``dotmac-tax`` contracts.

`docs/architecture/dotmac-tax-adoption-boundary.md` § "Typed ERP seams" names
the boundaries and says what they must carry. They exist so that:

- **ERP never hands the module a free-form payload.**  A source fact is a
  frozen, validated value object, not a dict assembled at a call site.
- **The module never learns anything about ERP.**  `dotmac_tax` sees opaque
  string references (`counterparty_ref`, `supply_ref`, `place_ref`,
  `source_ref`, `evidence_ref`) and exact money.  It is given no account, no
  journal, no ERP identity and no ERP enum.
- **ERP consumes only the module's published read contract.** The determination
  result is ``dotmac_tax.TaxDeterminationSetV1``. ERP does not mirror it and
  never imports the module's ORM. ``TaxApplicationContextV1`` carries the
  separate ERP-only posting intent.
- **FX remains an ERP consequence.** ``TaxPostingFXEvidenceV1`` wraps an
  already-selected immutable rate observation for determination-currency to
  functional-currency posting. It does not select a rate or convert the legal
  tax base supplied to the module.

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
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.money import Currency, ExchangeRate, Money

from app.services.finance.money_boundary import boundary_currency

__all__ = [
    "RECOGNITION_BASIS_ACCRUAL",
    "RECOGNITION_BASIS_CASH",
    "RECOGNITION_BASIS_PAYROLL_PERIOD",
    "TREATMENTS_WITHOUT_A_TAX_CONSEQUENCE",
    "AccountingConsequence",
    "ERPSourceTaxFactV1",
    "SourceFactFamily",
    "TaxApplicationContextV1",
    "TaxAdapterRefusal",
    "TaxPostingFXEvidenceV1",
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
    facts changed.

    `source_version` is therefore a CONTENT digest, produced by
    `inbound._content_source_version` — never a row's `version` column, which is
    an optimistic-locking counter that both under- and over-counts relative to
    the tax-relevant content.  Read that function's docstring before changing
    how a version is derived: the over-count direction fails silently and
    creates duplicate statutory evidence.
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
class TaxPostingFXEvidenceV1:
    """ERP-owned, immutable evidence for transaction -> functional conversion.

    This is deliberately a posting consequence, never a ``TaxFact`` field. The
    tax owner determines in its jurisdiction currency; ERP's FX owner selects a
    persisted observation before this value is constructed. No lookup, rate
    type choice or legal-tax-base conversion happens here.

    A same-currency posting is the one evidence-free case: its rate is exactly
    one and no invented exchange-rate row or type is allowed. A cross-currency
    posting requires the persisted observation id, the rate type id, its
    effective timestamp and source label. A bare decimal from an invoice header
    cannot satisfy this contract.
    """

    transaction_currency: Currency
    functional_currency: Currency
    exchange_rate: Decimal
    rate_observation_id: UUID | None
    rate_type_id: UUID | None
    observed_at: datetime | None
    source_label: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.transaction_currency, "transaction currency"),
            (self.functional_currency, "functional currency"),
        ):
            if not isinstance(value, Currency):
                raise TaxAdapterRefusal(f"{label} must be kernel Currency")
            provisioned = boundary_currency(value.code, field=label)
            if value != provisioned:
                raise TaxAdapterRefusal(
                    f"{label} {value.code} carries {value.minor_units} minor units; "
                    f"ERP provisions {provisioned.minor_units}"
                )
        if isinstance(self.exchange_rate, (bool, float)) or not isinstance(
            self.exchange_rate, Decimal
        ):
            raise TaxAdapterRefusal("exchange rate must be an exact Decimal")
        if not self.exchange_rate.is_finite() or self.exchange_rate <= _ZERO:
            raise TaxAdapterRefusal(
                f"exchange rate must be finite and positive, got {self.exchange_rate}"
            )

        identity = self.transaction_currency == self.functional_currency
        if identity:
            if self.exchange_rate != Decimal("1"):
                raise TaxAdapterRefusal("same-currency FX evidence must use rate 1")
            if any(
                value is not None
                for value in (
                    self.rate_observation_id,
                    self.rate_type_id,
                    self.observed_at,
                )
            ):
                raise TaxAdapterRefusal(
                    "same-currency FX evidence must not invent a rate observation"
                )
            if self.source_label != "identity":
                raise TaxAdapterRefusal(
                    "same-currency FX evidence source must be 'identity'"
                )
            return

        if not isinstance(self.rate_observation_id, UUID):
            raise TaxAdapterRefusal(
                "cross-currency FX evidence requires a persisted rate observation id"
            )
        if not isinstance(self.rate_type_id, UUID):
            raise TaxAdapterRefusal(
                "cross-currency FX evidence requires an exchange-rate type id"
            )
        if not isinstance(self.observed_at, datetime) or (
            self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise TaxAdapterRefusal(
                "cross-currency FX evidence requires a timezone-aware observed_at"
            )
        object.__setattr__(
            self, "source_label", _require_text(self.source_label, "FX source label")
        )

    @classmethod
    def identity(cls, currency_code: str) -> TaxPostingFXEvidenceV1:
        """Build the only valid same-currency evidence: rate one, no fake row."""

        currency = boundary_currency(currency_code, field="functional currency")
        return cls(
            transaction_currency=currency,
            functional_currency=currency,
            exchange_rate=Decimal("1"),
            rate_observation_id=None,
            rate_type_id=None,
            observed_at=None,
            source_label="identity",
        )

    @classmethod
    def from_snapshot(
        cls,
        snapshot: ExchangeRate,
        *,
        rate_type_id: UUID,
    ) -> TaxPostingFXEvidenceV1:
        """Wrap an already-selected immutable snapshot; never resolve a rate."""

        if not isinstance(snapshot, ExchangeRate):
            raise TaxAdapterRefusal("FX snapshot must be kernel ExchangeRate")
        try:
            observation_id = UUID(snapshot.rate_id or "")
        except (ValueError, TypeError) as exc:
            raise TaxAdapterRefusal(
                "FX snapshot must name its persisted ERP rate observation id"
            ) from exc
        return cls(
            transaction_currency=snapshot.base,
            functional_currency=snapshot.quote,
            exchange_rate=snapshot.rate,
            rate_observation_id=observation_id,
            rate_type_id=rate_type_id,
            observed_at=snapshot.as_of,
            source_label=snapshot.source,
        )

    def require_transaction_currency(self, currency: Currency) -> None:
        """Refuse a rate whose source pair/scale differs from a determination."""

        if currency != self.transaction_currency:
            raise TaxAdapterRefusal(
                "FX transaction currency does not match determination currency: "
                f"{self.transaction_currency.code}/"
                f"{self.transaction_currency.minor_units} vs "
                f"{currency.code}/{currency.minor_units}"
            )


@dataclass(frozen=True, slots=True)
class TaxApplicationContextV1:
    """ERP's posting intent for one public module determination result.

    The module result owns tax identity, treatment and exact amounts. This
    separate value owns only ERP consequences: target document, accounting
    side, counterpart, posting date, dimensions, reversal and attributable FX.
    Keeping them separate lets ``project_determination_set`` consume
    ``dotmac_tax.TaxDeterminationSetV1`` directly without recreating it as an ERP
    mirror or teaching the module about a general ledger.
    """

    organization_id: UUID
    posting_date: date
    consequence: AccountingConsequence
    document_id: UUID
    document_type: str
    counterpart_account_id: UUID
    fx_evidence: TaxPostingFXEvidenceV1
    line_id: UUID | None = None
    reversal: bool = False
    correlation_ref: str | None = None
    description: str | None = None
    business_unit_id: UUID | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None
    segment_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.organization_id, UUID):
            raise TaxAdapterRefusal("organization id must be a UUID")
        if not isinstance(self.posting_date, date):
            raise TaxAdapterRefusal("posting date must be a date")
        if not isinstance(self.consequence, AccountingConsequence):
            raise TaxAdapterRefusal("consequence must be an AccountingConsequence")
        if not isinstance(self.document_id, UUID):
            raise TaxAdapterRefusal("document id must be a UUID")
        if not isinstance(self.counterpart_account_id, UUID):
            raise TaxAdapterRefusal("counterpart account id must be a UUID")
        if not isinstance(self.fx_evidence, TaxPostingFXEvidenceV1):
            raise TaxAdapterRefusal("fx evidence must be TaxPostingFXEvidenceV1")
        object.__setattr__(
            self, "document_type", _require_text(self.document_type, "document type")
        )
        object.__setattr__(
            self,
            "correlation_ref",
            _require_optional_text(self.correlation_ref, "correlation ref"),
        )
        object.__setattr__(
            self,
            "description",
            _require_optional_text(self.description, "description"),
        )
