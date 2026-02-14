from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from app.services.finance.banking.contra_matching import (
    ContraLineCandidate,
    build_contra_idempotency_key,
    choose_best_contra_matches,
    score_contra_pair,
)


def _candidate(
    *,
    line_id: str,
    bank_account_id: str,
    txn_date: date,
    amount: str,
    reference: str | None = None,
    description: str | None = None,
) -> ContraLineCandidate:
    return ContraLineCandidate(
        line_id=UUID(line_id),
        bank_account_id=UUID(bank_account_id),
        transaction_date=txn_date,
        amount=Decimal(amount),
        reference=reference,
        description=description,
    )


def test_build_contra_idempotency_key_is_stable() -> None:
    org_id = UUID("00000000-0000-0000-0000-000000000001")
    src_id = UUID("10000000-0000-0000-0000-000000000001")
    dst_id = UUID("20000000-0000-0000-0000-000000000001")

    first = build_contra_idempotency_key(org_id, src_id, dst_id)
    second = build_contra_idempotency_key(org_id, src_id, dst_id)

    assert first == second
    assert first.endswith(":v1")


def test_score_contra_pair_rejects_same_bank_account() -> None:
    same_bank = "30000000-0000-0000-0000-000000000001"
    source = _candidate(
        line_id="10000000-0000-0000-0000-000000000002",
        bank_account_id=same_bank,
        txn_date=date(2026, 2, 14),
        amount="1000.00",
    )
    destination = _candidate(
        line_id="20000000-0000-0000-0000-000000000002",
        bank_account_id=same_bank,
        txn_date=date(2026, 2, 14),
        amount="1000.00",
    )

    score, reasons = score_contra_pair(source, destination)

    assert score == 0
    assert reasons["rejected"] == "same_bank_account"


def test_score_contra_pair_exact_match_has_high_score() -> None:
    source = _candidate(
        line_id="10000000-0000-0000-0000-000000000003",
        bank_account_id="30000000-0000-0000-0000-000000000002",
        txn_date=date(2026, 2, 14),
        amount="15000.00",
        reference="TRF-8891",
        description="Transfer to Zenith 461",
    )
    destination = _candidate(
        line_id="20000000-0000-0000-0000-000000000003",
        bank_account_id="40000000-0000-0000-0000-000000000002",
        txn_date=date(2026, 2, 14),
        amount="15000.00",
        reference="TRF-8891",
        description="Transfer from UBA to Zenith 461",
    )

    score, reasons = score_contra_pair(
        source,
        destination,
        target_bank_hint="zenith 461",
    )

    assert score >= 90
    assert reasons["amount_score"] == 50
    assert reasons["date_score"] == 25
    assert reasons["reference_score"] == 15
    assert reasons["hint_score"] == 5


def test_choose_best_contra_matches_is_deterministic() -> None:
    sources = [
        _candidate(
            line_id="10000000-0000-0000-0000-000000000010",
            bank_account_id="30000000-0000-0000-0000-000000000010",
            txn_date=date(2026, 2, 10),
            amount="5000.00",
            reference="REF-A1",
            description="Transfer to UBA",
        ),
        _candidate(
            line_id="10000000-0000-0000-0000-000000000011",
            bank_account_id="30000000-0000-0000-0000-000000000010",
            txn_date=date(2026, 2, 10),
            amount="7000.00",
            reference="REF-B1",
            description="Transfer to Zenith",
        ),
    ]
    destinations = [
        _candidate(
            line_id="20000000-0000-0000-0000-000000000011",
            bank_account_id="40000000-0000-0000-0000-000000000010",
            txn_date=date(2026, 2, 10),
            amount="7000.00",
            reference="REF-B1",
            description="Transfer from source bank",
        ),
        _candidate(
            line_id="20000000-0000-0000-0000-000000000010",
            bank_account_id="50000000-0000-0000-0000-000000000010",
            txn_date=date(2026, 2, 10),
            amount="5000.00",
            reference="REF-A1",
            description="Transfer from source bank",
        ),
    ]

    first = choose_best_contra_matches(sources, destinations, min_score=80)
    second = choose_best_contra_matches(sources, destinations, min_score=80)

    assert [(m.source_line_id, m.destination_line_id) for m in first] == [
        (
            UUID("10000000-0000-0000-0000-000000000010"),
            UUID("20000000-0000-0000-0000-000000000010"),
        ),
        (
            UUID("10000000-0000-0000-0000-000000000011"),
            UUID("20000000-0000-0000-0000-000000000011"),
        ),
    ]
    assert [(m.source_line_id, m.destination_line_id) for m in second] == [
        (m.source_line_id, m.destination_line_id) for m in first
    ]
