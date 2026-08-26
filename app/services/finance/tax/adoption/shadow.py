"""Field-complete, policy-neutral C3 tax shadow comparison.

Legacy identities enter only after policy/classification backfill has mapped and
adjudicated them to canonical module identities. This comparator never chooses
that mapping. A foreign legal base is retained as an adjudication outcome; it
is neither translated using an invoice-header scalar nor silently excluded.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from itertools import zip_longest
from typing import TYPE_CHECKING
from uuid import UUID

from dotmac_kernel.money import Currency, Money

from app.services.finance.tax.adoption.contracts import TaxAdapterRefusal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dotmac_tax import TaxDeterminationComponentV1, TaxDeterminationSetV1

__all__ = [
    "LegacyTaxComponentProjectionV1",
    "LegacyTaxProjectionV1",
    "ShadowComparedField",
    "ShadowComparisonOutcomeV1",
    "ShadowComparisonStatus",
    "ShadowMismatchV1",
    "ShadowNonComparableReason",
    "compare_shadow_determination",
]


class ShadowComparisonStatus(str, enum.Enum):
    MATCH = "match"
    DRIFT = "drift"
    ADJUDICATION_REQUIRED = "adjudication_required"


class ShadowNonComparableReason(str, enum.Enum):
    FOREIGN_LEGAL_BASE_CURRENCY = "foreign_legal_base_currency"


class ShadowComparedField(str, enum.Enum):
    COMPONENT_COUNT = "component_count"
    COMPONENT_SEQUENCE = "component_sequence"
    TAX_CODE_ID = "tax_code_id"
    RULE_ID = "rule_id"
    RULE_VERSION = "rule_version"
    PARTY_CATEGORY = "party_category"
    SUPPLY_CATEGORY = "supply_category"
    PLACE_CODE = "place_code"
    PARTY_CLASSIFICATION_ID = "party_classification_id"
    SUPPLY_CLASSIFICATION_ID = "supply_classification_id"
    PLACE_CLASSIFICATION_ID = "place_classification_id"
    TREATMENT_CODE = "treatment_code"
    CALCULATION_BASE_CODE = "calculation_base_code"
    INCLUSIVE = "inclusive"
    BASE_AMOUNT = "base_amount"
    COMPONENT_TAX_AMOUNT = "component_tax_amount"
    RECOVERABLE_AMOUNT = "recoverable_amount"
    NON_RECOVERABLE_AMOUNT = "non_recoverable_amount"
    SOURCE_AMOUNT = "source_amount"
    NET_AMOUNT = "net_amount"
    SET_TAX_AMOUNT = "set_tax_amount"
    GROSS_AMOUNT = "gross_amount"


@dataclass(frozen=True, slots=True)
class LegacyTaxComponentProjectionV1:
    """One legacy answer after its identities were explicitly adjudicated.

    ``tax_code_id``, ``rule_id`` and classification ids are canonical module
    identities supplied by the backfill owner, never mapped by the comparator.
    """

    component_sequence: int
    tax_code_id: UUID
    rule_id: UUID
    rule_version: int
    treatment_code: str
    calculation_base_code: str
    inclusive: bool
    party_category: str | None
    supply_category: str | None
    place_code: str | None
    party_classification_id: UUID | None
    supply_classification_id: UUID | None
    place_classification_id: UUID | None
    base_amount: Money
    tax_amount: Money
    recoverable_amount: Money
    non_recoverable_amount: Money


@dataclass(frozen=True, slots=True)
class LegacyTaxProjectionV1:
    """One complete legacy line projection admitted to C3 comparison."""

    source_ref: str
    source_amount: Money
    net_amount: Money
    tax_amount: Money
    gross_amount: Money
    components: tuple[LegacyTaxComponentProjectionV1, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise TaxAdapterRefusal("legacy projection source ref is required")
        object.__setattr__(self, "source_ref", self.source_ref.strip())
        object.__setattr__(self, "components", tuple(self.components))
        for value, label in (
            (self.source_amount, "source amount"),
            (self.net_amount, "net amount"),
            (self.tax_amount, "tax amount"),
            (self.gross_amount, "gross amount"),
        ):
            if not isinstance(value, Money):
                raise TaxAdapterRefusal(f"legacy {label} must be kernel Money")
        if any(
            money.currency != self.source_amount.currency
            for money in (self.net_amount, self.tax_amount, self.gross_amount)
        ):
            raise TaxAdapterRefusal("legacy projection totals differ in currency/scale")
        sequences = [component.component_sequence for component in self.components]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise TaxAdapterRefusal(
                "legacy components must be in unique increasing sequence"
            )


@dataclass(frozen=True, slots=True)
class ShadowMismatchV1:
    """One exact differing field, attributable to a component when applicable."""

    field: ShadowComparedField
    component_sequence: int | None
    legacy_value: str
    module_value: str


@dataclass(frozen=True, slots=True)
class ShadowComparisonOutcomeV1:
    """One source-line comparison or retained non-comparability decision."""

    source_ref: str
    status: ShadowComparisonStatus
    admitted_base_currency: Currency
    jurisdiction_currency: Currency
    determination_set_id: UUID | None
    mismatches: tuple[ShadowMismatchV1, ...] = ()
    reason: ShadowNonComparableReason | None = None

    @property
    def is_comparable(self) -> bool:
        return self.status is not ShadowComparisonStatus.ADJUDICATION_REQUIRED


def _render(value: object) -> str:
    if isinstance(value, Money):
        return (
            f"{value.amount}|{value.currency.code}|"
            f"{value.currency.minor_units}"
        )
    if value is None:
        return "<none>"
    return str(value)


def _add_mismatch(
    mismatches: list[ShadowMismatchV1],
    *,
    field: ShadowComparedField,
    legacy: object,
    module: object,
    component_sequence: int | None = None,
) -> None:
    if legacy != module:
        mismatches.append(
            ShadowMismatchV1(
                field=field,
                component_sequence=component_sequence,
                legacy_value=_render(legacy),
                module_value=_render(module),
            )
        )


def _require_public_set(value: object) -> TaxDeterminationSetV1:
    try:
        from dotmac_tax import TaxDeterminationSetV1
    except ImportError as exc:
        raise TaxAdapterRefusal(
            "dotmac-tax public read contract is unavailable for C3 shadowing"
        ) from exc
    if not isinstance(value, TaxDeterminationSetV1):
        raise TaxAdapterRefusal(
            "module result must be dotmac_tax.TaxDeterminationSetV1"
        )
    return value


def _compare_component(
    legacy: LegacyTaxComponentProjectionV1,
    module: TaxDeterminationComponentV1,
    mismatches: list[ShadowMismatchV1],
) -> None:
    sequence = legacy.component_sequence
    comparisons = (
        (ShadowComparedField.COMPONENT_SEQUENCE, sequence, module.component_sequence),
        (ShadowComparedField.TAX_CODE_ID, legacy.tax_code_id, module.tax_code_id),
        (ShadowComparedField.RULE_ID, legacy.rule_id, module.rule_id),
        (ShadowComparedField.RULE_VERSION, legacy.rule_version, module.rule_version),
        (ShadowComparedField.PARTY_CATEGORY, legacy.party_category, module.party_category),
        (ShadowComparedField.SUPPLY_CATEGORY, legacy.supply_category, module.supply_category),
        (ShadowComparedField.PLACE_CODE, legacy.place_code, module.place_code),
        (
            ShadowComparedField.PARTY_CLASSIFICATION_ID,
            legacy.party_classification_id,
            module.party_classification_id,
        ),
        (
            ShadowComparedField.SUPPLY_CLASSIFICATION_ID,
            legacy.supply_classification_id,
            module.supply_classification_id,
        ),
        (
            ShadowComparedField.PLACE_CLASSIFICATION_ID,
            legacy.place_classification_id,
            module.place_classification_id,
        ),
        (ShadowComparedField.TREATMENT_CODE, legacy.treatment_code, module.treatment_code),
        (
            ShadowComparedField.CALCULATION_BASE_CODE,
            legacy.calculation_base_code,
            module.calculation_base_code,
        ),
        (ShadowComparedField.INCLUSIVE, legacy.inclusive, module.inclusive),
        (ShadowComparedField.BASE_AMOUNT, legacy.base_amount, module.base_amount),
        (
            ShadowComparedField.COMPONENT_TAX_AMOUNT,
            legacy.tax_amount,
            module.tax_amount,
        ),
        (
            ShadowComparedField.RECOVERABLE_AMOUNT,
            legacy.recoverable_amount,
            module.recoverable_amount,
        ),
        (
            ShadowComparedField.NON_RECOVERABLE_AMOUNT,
            legacy.non_recoverable_amount,
            module.non_recoverable_amount,
        ),
    )
    for field, legacy_value, module_value in comparisons:
        _add_mismatch(
            mismatches,
            field=field,
            legacy=legacy_value,
            module=module_value,
            component_sequence=sequence,
        )


def compare_shadow_determination(
    *,
    legacy: LegacyTaxProjectionV1,
    jurisdiction_currency: Currency,
    module_result: object | None,
) -> ShadowComparisonOutcomeV1:
    """Compare every C3 line field or retain foreign-base adjudication evidence."""

    if not isinstance(legacy, LegacyTaxProjectionV1):
        raise TaxAdapterRefusal("legacy must be LegacyTaxProjectionV1")
    if not isinstance(jurisdiction_currency, Currency):
        raise TaxAdapterRefusal("jurisdiction currency must be kernel Currency")
    if legacy.source_amount.currency != jurisdiction_currency:
        return ShadowComparisonOutcomeV1(
            source_ref=legacy.source_ref,
            status=ShadowComparisonStatus.ADJUDICATION_REQUIRED,
            admitted_base_currency=legacy.source_amount.currency,
            jurisdiction_currency=jurisdiction_currency,
            determination_set_id=None,
            reason=ShadowNonComparableReason.FOREIGN_LEGAL_BASE_CURRENCY,
        )

    result = _require_public_set(module_result)
    if result.source_ref != legacy.source_ref:
        raise TaxAdapterRefusal("legacy and module source refs differ")
    mismatches: list[ShadowMismatchV1] = []
    for field, legacy_value, module_value in (
        (ShadowComparedField.SOURCE_AMOUNT, legacy.source_amount, result.source_amount),
        (ShadowComparedField.NET_AMOUNT, legacy.net_amount, result.net_amount),
        (ShadowComparedField.SET_TAX_AMOUNT, legacy.tax_amount, result.tax_amount),
        (ShadowComparedField.GROSS_AMOUNT, legacy.gross_amount, result.gross_amount),
    ):
        _add_mismatch(
            mismatches,
            field=field,
            legacy=legacy_value,
            module=module_value,
        )
    _add_mismatch(
        mismatches,
        field=ShadowComparedField.COMPONENT_COUNT,
        legacy=len(legacy.components),
        module=len(result.components),
    )
    for legacy_component, module_component in zip_longest(
        legacy.components, result.components
    ):
        if legacy_component is None or module_component is None:
            sequence = (
                legacy_component.component_sequence
                if legacy_component is not None
                else module_component.component_sequence
            )
            _add_mismatch(
                mismatches,
                field=ShadowComparedField.COMPONENT_SEQUENCE,
                legacy=(legacy_component.component_sequence if legacy_component else None),
                module=(module_component.component_sequence if module_component else None),
                component_sequence=sequence,
            )
            continue
        _compare_component(legacy_component, module_component, mismatches)

    return ShadowComparisonOutcomeV1(
        source_ref=legacy.source_ref,
        status=(
            ShadowComparisonStatus.DRIFT
            if mismatches
            else ShadowComparisonStatus.MATCH
        ),
        admitted_base_currency=legacy.source_amount.currency,
        jurisdiction_currency=jurisdiction_currency,
        determination_set_id=result.determination_set_id,
        mismatches=tuple(mismatches),
    )
