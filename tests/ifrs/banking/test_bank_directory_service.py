from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.finance.banking.bank_directory import BankDirectoryService


def test_lookup_bank_code_returns_none_for_blank_name():
    service = BankDirectoryService(MagicMock())
    assert service.lookup_bank_code("") is None
    assert service.lookup_bank_code("   ") is None


def test_lookup_bank_code_uses_exact_match_first(monkeypatch):
    service = BankDirectoryService(MagicMock())
    monkeypatch.setattr(
        service,
        "get_by_name",
        lambda _name: SimpleNamespace(bank_code="057"),
    )

    assert service.lookup_bank_code("Zenith") == "057"


def test_lookup_bank_code_falls_back_to_alias_then_partial(monkeypatch):
    db = MagicMock()
    service = BankDirectoryService(db)
    monkeypatch.setattr(service, "get_by_name", lambda _name: None)

    db.scalar.side_effect = [
        SimpleNamespace(bank_code="044"),
        SimpleNamespace(bank_code="011"),
    ]

    assert service.lookup_bank_code("Access") == "044"
    # Second call: alias miss, partial hit
    db.scalar.side_effect = [None, SimpleNamespace(bank_code="011")]
    assert service.lookup_bank_code("First") == "011"


def test_lookup_bank_returns_full_record_when_code_found(monkeypatch):
    service = BankDirectoryService(MagicMock())
    monkeypatch.setattr(service, "lookup_bank_code", lambda _name: "057")
    monkeypatch.setattr(
        service,
        "get_by_code",
        lambda code: SimpleNamespace(bank_code=code, bank_name="Zenith"),
    )

    bank = service.lookup_bank("Zenith")
    assert bank.bank_code == "057"
    assert bank.bank_name == "Zenith"


def test_search_banks_blank_query_returns_active_limited(monkeypatch):
    service = BankDirectoryService(MagicMock())
    monkeypatch.setattr(
        service,
        "list_active_banks",
        lambda: [
            SimpleNamespace(bank_name="A"),
            SimpleNamespace(bank_name="B"),
            SimpleNamespace(bank_name="C"),
        ],
    )

    results = service.search_banks("", limit=2)
    assert [r.bank_name for r in results] == ["A", "B"]
