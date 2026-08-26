"""Deterministic functional-currency allocation for tax consequence lines.

The FX owner has already selected and persisted the rate represented by
``TaxPostingFXEvidenceV1``. This module only applies that evidence to a complete
transaction-currency posting. It reuses Finance's existing six-decimal residue
owner so the numbers validated here are the numbers the journal columns store.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.services.finance.posting.residue import (
    ResidueAllocationError,
    allocate_residue,
    quantize,
    sum_at_persisted_scale,
)
from app.services.finance.tax.adoption.contracts import (
    TaxAdapterRefusal,
    TaxPostingFXEvidenceV1,
)

__all__ = ["FunctionalPostingAmounts", "allocate_functional_line_amounts"]

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class FunctionalPostingAmounts:
    """The persisted-scale functional amounts for one consequence line."""

    debit_amount: Decimal
    credit_amount: Decimal


def _require_line_amount(value: Decimal, label: str) -> Decimal:
    if isinstance(value, (bool, float)) or not isinstance(value, Decimal):
        raise TaxAdapterRefusal(f"{label} must be an exact Decimal")
    if not value.is_finite() or value < _ZERO:
        raise TaxAdapterRefusal(f"{label} must be finite and non-negative")
    return value


def allocate_functional_line_amounts(
    lines: Sequence[tuple[Decimal, Decimal]],
    *,
    evidence: TaxPostingFXEvidenceV1,
) -> tuple[FunctionalPostingAmounts, ...]:
    """Convert a balanced posting and allocate rounding residue by side.

    The rate direction is explicit: transaction currency multiplied by
    ``evidence.exchange_rate`` produces ERP functional currency. Debit and
    credit targets are independently derived from their transaction totals;
    Finance's canonical allocator assigns any six-decimal residue to the
    largest absolute line with stable-index tie breaking.
    """

    if not isinstance(evidence, TaxPostingFXEvidenceV1):
        raise TaxAdapterRefusal("FX evidence must be TaxPostingFXEvidenceV1")

    normalized: list[tuple[Decimal, Decimal]] = []
    for index, (debit, credit) in enumerate(lines):
        debit = _require_line_amount(debit, f"line {index} debit")
        credit = _require_line_amount(credit, f"line {index} credit")
        if (debit != _ZERO) == (credit != _ZERO):
            raise TaxAdapterRefusal(
                f"line {index} must carry exactly one transaction-currency side"
            )
        normalized.append((debit, credit))

    if not normalized:
        return ()

    transaction_debit = sum_at_persisted_scale(debit for debit, _ in normalized)
    transaction_credit = sum_at_persisted_scale(credit for _, credit in normalized)
    if transaction_debit != transaction_credit:
        raise TaxAdapterRefusal(
            "transaction-currency posting is unbalanced at persisted scale: "
            f"debits {transaction_debit}, credits {transaction_credit}"
        )

    rate = evidence.exchange_rate
    raw_debits = [debit * rate for debit, _ in normalized if debit != _ZERO]
    raw_credits = [credit * rate for _, credit in normalized if credit != _ZERO]
    target_debit = quantize(transaction_debit * rate)
    target_credit = quantize(transaction_credit * rate)
    try:
        allocated_debits = iter(allocate_residue(raw_debits, target_debit))
        allocated_credits = iter(allocate_residue(raw_credits, target_credit))
    except ResidueAllocationError as exc:  # pragma: no cover - defensive
        raise TaxAdapterRefusal(f"functional FX allocation failed: {exc}") from exc

    allocated: list[FunctionalPostingAmounts] = []
    for debit, credit in normalized:
        allocated.append(
            FunctionalPostingAmounts(
                debit_amount=next(allocated_debits) if debit != _ZERO else _ZERO,
                credit_amount=next(allocated_credits) if credit != _ZERO else _ZERO,
            )
        )

    functional_debit = sum_at_persisted_scale(
        line.debit_amount for line in allocated
    )
    functional_credit = sum_at_persisted_scale(
        line.credit_amount for line in allocated
    )
    if functional_debit != functional_credit:  # pragma: no cover - defensive
        raise TaxAdapterRefusal(
            "functional-currency posting is unbalanced after allocation: "
            f"debits {functional_debit}, credits {functional_credit}"
        )
    return tuple(allocated)
