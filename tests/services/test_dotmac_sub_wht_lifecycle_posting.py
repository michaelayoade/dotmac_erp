"""ERP consequences for Sub-owned WHT lifecycle decisions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.services.dotmac_sub.client import PaymentRecord
from app.services.dotmac_sub.sync._payments import PaymentSyncMixin


class _Harness(PaymentSyncMixin):
    def __init__(self, db: MagicMock, organization_id) -> None:
        self.db = db
        self.organization_id = organization_id

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None


def _source(status: str, *, resolved_at: str | None = "2026-07-03T09:00:00+00:00"):
    return PaymentRecord(
        id="sub-payment-1",
        account_id="sub-account-1",
        billing_account_id=None,
        amount=Decimal("100"),
        currency="NGN",
        status="succeeded",
        gross_amount=Decimal("100"),
        net_amount=Decimal("95"),
        wht_amount=Decimal("5"),
        wht_rate=Decimal("5"),
        wht_status=status,
        wht_record_id="sub-wht-1",
        wht_certificate_reference="CERT-1",
        wht_resolved_at=resolved_at,
    )


def _payment(organization_id, tax_code_id):
    return SimpleNamespace(
        payment_id=uuid4(),
        payment_number="PMT-1",
        wht_amount=Decimal("5"),
        wht_code_id=tax_code_id,
        exchange_rate=Decimal("1"),
        currency_code="NGN",
        correlation_id="sub-payment-1",
        created_by_user_id=uuid4(),
    )


def _tax_code(organization_id, tax_code_id):
    return SimpleNamespace(
        tax_code_id=tax_code_id,
        organization_id=organization_id,
        tax_type=TaxType.WITHHOLDING,
        tax_paid_account_id=uuid4(),
        tax_collected_account_id=uuid4(),
        tax_expense_account_id=uuid4(),
        jurisdiction_id=uuid4(),
        tax_rate=Decimal("0.05"),
    )


def _capture_journal(monkeypatch, db, tax_code):
    captured = {}
    journal = SimpleNamespace(journal_entry_id=uuid4())
    db.get.side_effect = lambda model, record_id: (
        tax_code if model is TaxCode and record_id == tax_code.tax_code_id else None
    )
    monkeypatch.setattr(
        "app.services.finance.posting.idempotency.PostingIdempotencyService.source_journal_exists",
        lambda *_args, **_kwargs: False,
    )

    def create_and_post(_db, _org, journal_input, _user, **_kwargs):
        captured["input"] = journal_input
        return journal, SimpleNamespace(success=True, message="posted")

    monkeypatch.setattr(
        "app.services.finance.posting.base.BasePostingAdapter.create_approve_and_post_journal",
        create_and_post,
    )
    return captured, journal


def test_reclaimed_wht_clears_receivable_against_erp_tax_liability(
    monkeypatch,
) -> None:
    org_id = uuid4()
    code_id = uuid4()
    db = MagicMock()
    code = _tax_code(org_id, code_id)
    captured, _journal = _capture_journal(monkeypatch, db, code)
    payment = _payment(org_id, code_id)

    _Harness(db, org_id)._ensure_wht_terminal_consequence(
        payment,
        _source("reclaimed"),
        created_by_user_id=None,
    )

    journal_input = captured["input"]
    assert journal_input.posting_date.isoformat() == "2026-07-03"
    assert journal_input.source_document_type == "CUSTOMER_PAYMENT_WHT_RECLAIM"
    assert [line.account_id for line in journal_input.lines] == [
        code.tax_collected_account_id,
        code.tax_paid_account_id,
    ]
    assert [
        (line.debit_amount, line.credit_amount) for line in journal_input.lines
    ] == [(Decimal("5"), Decimal("0")), (Decimal("0"), Decimal("5"))]


def test_written_off_wht_moves_receivable_to_expense_and_negates_tax_credit(
    monkeypatch,
) -> None:
    org_id = uuid4()
    code_id = uuid4()
    db = MagicMock()
    code = _tax_code(org_id, code_id)
    captured, journal = _capture_journal(monkeypatch, db, code)
    payment = _payment(org_id, code_id)
    recorded = {}
    harness = _Harness(db, org_id)

    def record_reversal(_payment, _pay, **kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr(harness, "_record_wht_write_off_tax_reversal", record_reversal)

    harness._ensure_wht_terminal_consequence(
        payment,
        _source("written_off"),
        created_by_user_id=None,
    )

    journal_input = captured["input"]
    assert journal_input.source_document_type == "CUSTOMER_PAYMENT_WHT_WRITE_OFF"
    assert journal_input.lines[0].account_id == code.tax_expense_account_id
    assert journal_input.lines[1].account_id == code.tax_paid_account_id
    assert recorded["journal_entry_id"] == journal.journal_entry_id
    assert recorded["posting_date"].isoformat() == "2026-07-03"


def test_terminal_wht_fails_closed_without_source_resolution_time(
    monkeypatch,
) -> None:
    org_id = uuid4()
    code_id = uuid4()
    db = MagicMock()
    code = _tax_code(org_id, code_id)
    _capture_journal(monkeypatch, db, code)

    with pytest.raises(ValueError, match="resolution timestamp"):
        _Harness(db, org_id)._ensure_wht_terminal_consequence(
            _payment(org_id, code_id),
            _source("reclaimed", resolved_at=None),
            created_by_user_id=None,
        )


@pytest.mark.parametrize(
    ("status", "missing_field", "message"),
    [
        ("reclaimed", "tax_collected_account_id", "tax-liability"),
        ("written_off", "tax_expense_account_id", "expense"),
    ],
)
def test_terminal_wht_fails_closed_without_erp_account_mapping(
    monkeypatch, status, missing_field, message
) -> None:
    org_id = uuid4()
    code_id = uuid4()
    db = MagicMock()
    code = _tax_code(org_id, code_id)
    setattr(code, missing_field, None)
    _capture_journal(monkeypatch, db, code)

    with pytest.raises(ValueError, match=message):
        _Harness(db, org_id)._ensure_wht_terminal_consequence(
            _payment(org_id, code_id),
            _source(status),
            created_by_user_id=None,
        )


def test_terminal_wht_journal_is_idempotent(monkeypatch) -> None:
    org_id = uuid4()
    code_id = uuid4()
    db = MagicMock()
    monkeypatch.setattr(
        "app.services.finance.posting.idempotency.PostingIdempotencyService.source_journal_exists",
        lambda *_args, **_kwargs: True,
    )
    create = MagicMock()
    monkeypatch.setattr(
        "app.services.finance.posting.base.BasePostingAdapter.create_approve_and_post_journal",
        create,
    )

    _Harness(db, org_id)._ensure_wht_terminal_consequence(
        _payment(org_id, code_id),
        _source("reclaimed"),
        created_by_user_id=None,
    )

    create.assert_not_called()
    db.get.assert_not_called()


def test_written_off_tax_transaction_is_negative_and_linked(monkeypatch) -> None:
    org_id = uuid4()
    code_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = None
    code = _tax_code(org_id, code_id)
    period = SimpleNamespace(fiscal_period_id=uuid4())
    tax_transaction = SimpleNamespace(journal_entry_id=None)
    captured = {}
    monkeypatch.setattr(
        "app.services.finance.gl.period_guard.PeriodGuardService.get_period_for_date",
        lambda *_args, **_kwargs: period,
    )

    def create_transaction(**kwargs):
        captured["input"] = kwargs["input"]
        return tax_transaction

    monkeypatch.setattr(
        "app.services.finance.tax.tax_transaction.tax_transaction_service.create_transaction",
        create_transaction,
    )
    journal_id = uuid4()

    _Harness(db, org_id)._record_wht_write_off_tax_reversal(
        _payment(org_id, code_id),
        _source("written_off"),
        tax_code=code,
        posting_date=datetime(2026, 7, 3).date(),
        journal_entry_id=journal_id,
    )

    tax_input = captured["input"]
    assert tax_input.tax_amount == Decimal("-5")
    assert tax_input.functional_tax_amount == Decimal("-5")
    assert tax_input.source_document_type == "CUSTOMER_PAYMENT_WHT_WRITE_OFF"
    assert tax_transaction.journal_entry_id == journal_id
