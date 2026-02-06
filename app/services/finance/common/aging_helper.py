"""
Shared aging helpers for AR/AP aging services.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Iterable, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class AgingBucket:
    """Represents an aging bucket."""

    bucket_name: str
    min_days: int
    max_days: Optional[int]


AGING_BUCKETS: list[AgingBucket] = [
    AgingBucket("Current", 0, 30),
    AgingBucket("31-60 Days", 31, 60),
    AgingBucket("61-90 Days", 61, 90),
    AgingBucket("Over 90 Days", 91, None),
]

BUCKET_ATTRS = [
    ("Current", "current"),
    ("31-60 Days", "days_31_60"),
    ("61-90 Days", "days_61_90"),
    ("Over 90 Days", "over_90"),
]


def compute_aging_totals(
    items: Iterable[T],
    ref_date: date,
    *,
    due_date: Callable[[T], date],
    balance: Callable[[T], Decimal],
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Compute standard 0-30/31-60/61-90/90+ buckets."""
    current = Decimal("0")
    days_31_60 = Decimal("0")
    days_61_90 = Decimal("0")
    over_90 = Decimal("0")

    for item in items:
        days_overdue = (ref_date - due_date(item)).days
        amount = balance(item)

        if days_overdue <= 30:
            current += amount
        elif days_overdue <= 60:
            days_31_60 += amount
        elif days_overdue <= 90:
            days_61_90 += amount
        else:
            over_90 += amount

    return current, days_31_60, days_61_90, over_90
