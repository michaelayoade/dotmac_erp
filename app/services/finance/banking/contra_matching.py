"""Deterministic contra-transfer scoring and pairing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class ContraLineCandidate:
    """Minimal line shape for contra matching."""

    line_id: UUID
    bank_account_id: UUID
    transaction_date: date
    amount: Decimal
    reference: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class ContraMatch:
    """Chosen contra pair with explainability metadata."""

    source_line_id: UUID
    destination_line_id: UUID
    score: int
    date_diff_days: int
    amount_diff: Decimal
    reasons: dict[str, object]


def build_contra_idempotency_key(
    organization_id: UUID,
    source_line_id: UUID,
    destination_line_id: UUID,
    *,
    version: str = "v1",
) -> str:
    """Create deterministic idempotency key for contra posting."""
    return (
        f"org:{organization_id}:contra:{source_line_id}:{destination_line_id}:{version}"
    )


def _tokenize(value: str | None) -> set[str]:
    if not value:
        return set()
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
    return {token for token in normalized.split() if len(token) >= 3}


def score_contra_pair(
    source: ContraLineCandidate,
    destination: ContraLineCandidate,
    *,
    amount_tolerance: Decimal = Decimal("0.01"),
    date_window_days: int = 2,
    target_bank_hint: str | None = None,
) -> tuple[int, dict[str, object]]:
    """Score source/destination contra transfer candidate pair (0-100)."""
    if source.bank_account_id == destination.bank_account_id:
        return 0, {"rejected": "same_bank_account"}

    amount_diff = abs(abs(source.amount) - abs(destination.amount))
    if amount_diff > amount_tolerance:
        return 0, {
            "rejected": "amount_out_of_tolerance",
            "amount_diff": str(amount_diff),
        }

    date_diff_days = abs((destination.transaction_date - source.transaction_date).days)
    if date_diff_days > date_window_days:
        return 0, {"rejected": "date_out_of_window", "date_diff_days": date_diff_days}

    score = 0
    reasons: dict[str, object] = {
        "amount_diff": str(amount_diff),
        "date_diff_days": date_diff_days,
    }

    # 50 pts: amount closeness (exact == 50)
    if amount_tolerance > 0:
        closeness = max(Decimal("0"), Decimal("1") - (amount_diff / amount_tolerance))
    else:
        closeness = Decimal("1") if amount_diff == 0 else Decimal("0")
    amount_score = int((Decimal("50") * closeness).to_integral_value())
    score += amount_score
    reasons["amount_score"] = amount_score

    # 25 pts: date proximity (same day best)
    date_score = max(0, 25 - (date_diff_days * 8))
    score += date_score
    reasons["date_score"] = date_score

    source_ref_tokens = _tokenize(source.reference)
    dest_ref_tokens = _tokenize(destination.reference)
    ref_overlap = source_ref_tokens.intersection(dest_ref_tokens)
    ref_score = 15 if ref_overlap else 0
    score += ref_score
    reasons["reference_score"] = ref_score
    reasons["reference_overlap"] = sorted(ref_overlap)

    src_desc_tokens = _tokenize(source.description)
    dst_desc_tokens = _tokenize(destination.description)
    desc_overlap = src_desc_tokens.intersection(dst_desc_tokens)
    desc_score = min(len(desc_overlap) * 3, 10)
    score += desc_score
    reasons["description_score"] = desc_score
    reasons["description_overlap"] = sorted(desc_overlap)

    hint_score = 0
    if target_bank_hint:
        hint = target_bank_hint.strip().lower()
        dest_text = (
            f"{destination.reference or ''} {destination.description or ''}".lower()
        )
        if hint and hint in dest_text:
            hint_score = 5
            score += hint_score
    reasons["hint_score"] = hint_score

    return min(score, 100), reasons


def choose_best_contra_matches(
    sources: list[ContraLineCandidate],
    destinations: list[ContraLineCandidate],
    *,
    amount_tolerance: Decimal = Decimal("0.01"),
    date_window_days: int = 2,
    min_score: int = 90,
) -> list[ContraMatch]:
    """Greedy deterministic 1:1 contra matching."""
    candidates: list[ContraMatch] = []

    for source in sources:
        for destination in destinations:
            score, reasons = score_contra_pair(
                source,
                destination,
                amount_tolerance=amount_tolerance,
                date_window_days=date_window_days,
            )
            if score < min_score:
                continue
            candidates.append(
                ContraMatch(
                    source_line_id=source.line_id,
                    destination_line_id=destination.line_id,
                    score=score,
                    date_diff_days=abs(
                        (destination.transaction_date - source.transaction_date).days
                    ),
                    amount_diff=abs(abs(source.amount) - abs(destination.amount)),
                    reasons=reasons,
                )
            )

    candidates.sort(
        key=lambda c: (
            -c.score,
            c.date_diff_days,
            c.amount_diff,
            str(c.source_line_id),
            str(c.destination_line_id),
        )
    )

    used_sources: set[UUID] = set()
    used_destinations: set[UUID] = set()
    chosen: list[ContraMatch] = []

    for candidate in candidates:
        if candidate.source_line_id in used_sources:
            continue
        if candidate.destination_line_id in used_destinations:
            continue
        used_sources.add(candidate.source_line_id)
        used_destinations.add(candidate.destination_line_id)
        chosen.append(candidate)

    return chosen
