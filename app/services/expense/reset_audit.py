"""Present persisted reset evidence without inventing missing historical facts."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


def reset_evidence(
    reset: Any, reviewer_name: str | None, payments: list[Any]
) -> dict[str, Any]:
    """Partition the selected period's paid amounts around this reset."""
    before = after = unknown = Decimal("0")
    at: datetime = reset.reset_at
    at = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at
    for payment in payments:
        amount = payment.amount_paid or Decimal("0")
        if payment.paid_at is not None:
            paid_at = payment.paid_at
            if paid_at.tzinfo is None:
                paid_at = paid_at.replace(tzinfo=timezone.utc)
            if paid_at < at:
                before += amount
            else:
                after += amount
        elif payment.paid_on < at.date():
            before += amount
        elif payment.paid_on > at.date():
            after += amount
        elif str(getattr(payment, "claim_id", None)) in (
            getattr(reset, "legacy_paid_claim_ids", None) or []
        ):
            before += amount
        else:
            unknown += amount
    has_snapshot = reset.budget_snapshot is not None
    return {
        "reset_at": at,
        "kind": reset.reset_kind,
        "reason": reset.reset_reason,
        "reviewer": reset.reviewer_name_snapshot
        or reviewer_name
        or "Reviewer no longer available",
        "budget": reset.budget_snapshot,
        "currency": reset.currency_snapshot or "",
        "paid_before_reset": reset.paid_before_reset,
        "committed": reset.committed_at_reset,
        "remaining_before": reset.remaining_before_reset,
        "remaining_after": reset.remaining_after_reset,
        "restored": (reset.remaining_after_reset - reset.remaining_before_reset)
        if has_snapshot
        else None,
        "has_snapshot": has_snapshot,
        "period_paid_before": before,
        "period_paid_after": after,
        "period_paid_timing_unknown": unknown,
    }
