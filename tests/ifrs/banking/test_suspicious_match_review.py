from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.services.finance.banking import suspicious_matches as suspicious_mod


def test_collect_suspicious_matches_filters_non_suspicious_rows(monkeypatch):
    org_id = uuid4()
    line_id = uuid4()
    statement_id = uuid4()
    journal_line_id = uuid4()
    account_id = uuid4()

    match = SimpleNamespace(
        statement_line_id=line_id,
        journal_line_id=journal_line_id,
        match_state="suggested",
        matched_at=None,
    )
    line = SimpleNamespace(
        line_id=line_id,
        transaction_date="2026-06-15",
        amount=1250,
        description="Transfer to supplier",
        reference="ABC123",
        bank_reference=None,
    )
    statement = SimpleNamespace(
        statement_id=statement_id,
        statement_number="ST-001",
        bank_account_id=account_id,
    )
    account = SimpleNamespace(bank_name="Zenith", account_number="0011223344")

    db = MagicMock()
    db.execute.return_value.all.return_value = [
        (
            match,
            line,
            statement,
            account,
            70,
            "Amount match, 2-day date offset",
        )
    ]

    rows = suspicious_mod.collect_suspicious_matches(
        db,
        org_id,
        account_id=account_id,
        match_state="suggested",
    )

    assert len(rows) == 1
    assert rows[0].is_suspicious is True
    assert rows[0].is_low_confidence is True
    assert rows[0].is_fallback_reason is True
    assert rows[0].statement_id == statement_id


def test_clear_suspicious_suggested_matches_only_deletes_suggested(monkeypatch):
    org_id = uuid4()
    suggested = suspicious_mod.SuspiciousMatch(
        statement_line_id=uuid4(),
        statement_id=uuid4(),
        journal_line_id=uuid4(),
        statement_number="ST-001",
        account_id=uuid4(),
        bank_name="GTB",
        account_number="123",
        transaction_date="2026-06-15",
        amount=100,
        description="A",
        reference=None,
        match_state="suggested",
        confidence_score=70,
        explanation="date+amount fallback",
        matched_at=None,
    )
    confirmed = suspicious_mod.SuspiciousMatch(
        statement_line_id=uuid4(),
        statement_id=uuid4(),
        journal_line_id=uuid4(),
        statement_number="ST-002",
        account_id=uuid4(),
        bank_name="UBA",
        account_number="456",
        transaction_date="2026-06-16",
        amount=200,
        description="B",
        reference=None,
        match_state="confirmed",
        confidence_score=70,
        explanation="date+amount fallback",
        matched_at=None,
    )

    monkeypatch.setattr(
        suspicious_mod,
        "collect_suspicious_matches",
        lambda *args, **kwargs: [suggested, confirmed],
    )
    db = MagicMock()

    cleared = suspicious_mod.clear_suspicious_suggested_matches(db, org_id)

    assert cleared == 1
    assert db.execute.call_count == 1
