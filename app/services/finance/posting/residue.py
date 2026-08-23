"""Deterministic allocation of a rounding residue across journal lines.

A document total that splits into non-terminating fractions cannot be
represented exactly by any set of fixed-scale lines. Rounding each line
independently then leaves the lines summing to something other than the total —
and the difference, however small, is an unbalanced journal.

This module owns the one decision that fixes it: **which line absorbs the
residue**.

## The defect this exists to prevent

Three AR invoices to one customer posted one micro-unit out of balance
(`JE202604-40653`, `JE202604-40818`, `JE202604-42111`). Their revenue split was:

```
1,761,375/17 + 335,500/17 + 83,875/34  =  4,277,625/34  =  125,812.500000
```

Every one of the three lines rounded UP at six places
(`.294117647… → .294118`, `.911764705… → .911765`), so the stored lines summed
to `125,812.500001`. The debit and the VAT lines were exact, so the whole
difference sat in the revenue allocation.

Nothing rebalanced it, and the posting boundary of the day admitted a difference
of exactly one micro-unit, so it reached the ledger.

## The policy

1. Round each line normally.
2. Apply the balancing residue to the **largest absolute** line.
3. Break ties by **stable line index** — the earliest wins.

Largest-absolute is chosen because the residue is proportionally smallest there,
so the line whose reported value moves is the one least distorted by moving it.
The tie-break exists so the same input always produces the same output: an
allocator that picked arbitrarily among equal candidates would make journals
irreproducible, and a reproducible ledger is worth more than a marginally
prettier split.

**This is not a tolerance.** Nothing is being permitted to be out of balance —
the residue is assigned to a specific line so the journal balances exactly.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal

#: The scale ledger amounts are persisted at (`NUMERIC(20, 6)`).
PERSISTED_SCALE = Decimal("0.000001")

#: PostgreSQL rounds a `numeric` cast half AWAY FROM ZERO, while Python's
#: `Decimal` default is half-to-even. Rounding here with anything other than
#: `ROUND_HALF_UP` would mean the numbers a balance check validates are not the
#: numbers the column stores — the check would pass on a set of lines the
#: database then writes out of balance.
PERSISTED_ROUNDING = ROUND_HALF_UP


class ResidueAllocationError(ValueError):
    """The residue cannot be allocated — a caller defect, not a rounding issue."""


def quantize(amount: Decimal) -> Decimal:
    """Round to the scale — and with the rounding mode — the ledger stores."""
    return Decimal(amount).quantize(PERSISTED_SCALE, rounding=PERSISTED_ROUNDING)


def sum_at_persisted_scale(amounts: Iterable[Decimal]) -> Decimal:
    """Total these amounts the way the DATABASE will: quantise, then add.

    Summing first and quantising the total answers a different question, about
    a precision no column preserves. Two lines of `100.0000005` sum to
    `200.000001` that way; stored, they are `100.000001` each and total
    `200.000002`. A balance check has to ask about the stored rows.
    """
    return sum((quantize(amount) for amount in amounts), Decimal("0"))


def allocate_residue(
    amounts: Sequence[Decimal],
    target_total: Decimal,
) -> list[Decimal]:
    """Return `amounts` adjusted so they sum EXACTLY to `target_total`.

    Every value is first quantised to persisted scale, then the whole difference
    against `target_total` is applied to the largest-absolute entry (earliest
    index on a tie).

    Raises rather than guessing when there is nothing to adjust: an empty
    sequence with a non-zero target is a caller defect, and silently returning
    an unbalanced result is how the original defect reached production.
    """
    target = quantize(target_total)
    rounded = [quantize(amount) for amount in amounts]

    if not rounded:
        if target != Decimal("0"):
            raise ResidueAllocationError(
                f"cannot allocate a residue of {target} across zero lines"
            )
        return []

    residue = target - sum(rounded, Decimal("0"))
    if residue == 0:
        return rounded

    index = _largest_absolute_index(rounded)
    rounded[index] = quantize(rounded[index] + residue)

    # Belt and braces: the invariant this function exists to guarantee.
    if sum(rounded, Decimal("0")) != target:  # pragma: no cover - defensive
        raise ResidueAllocationError(
            f"allocation failed to reach the target: {sum(rounded, Decimal('0'))} != {target}"
        )
    return rounded


def _largest_absolute_index(amounts: Sequence[Decimal]) -> int:
    """Index of the largest-absolute value; earliest index wins a tie.

    Written as an explicit scan rather than `max(..., key=abs)` so the tie-break
    is visible and cannot change with a library's iteration order. `>` (not
    `>=`) is what makes the FIRST of equal candidates win.
    """
    best = 0
    for index in range(1, len(amounts)):
        if abs(amounts[index]) > abs(amounts[best]):
            best = index
    return best


__all__ = [
    "PERSISTED_ROUNDING",
    "PERSISTED_SCALE",
    "ResidueAllocationError",
    "allocate_residue",
    "quantize",
    "sum_at_persisted_scale",
]
