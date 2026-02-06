from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.finance.banking.bank_reconciliation import (
    BankReconciliation,
    BankReconciliationLine,
    ReconciliationMatchType,
    ReconciliationStatus,
)
from app.services.finance.banking.bank_reconciliation import (
    BankReconciliationService,
    ReconciliationInput,
    ReconciliationMatchInput,
)


def _make_reconciliation(status=ReconciliationStatus.draft):
    recon = SimpleNamespace(
        reconciliation_id=uuid4(),
        bank_account_id=uuid4(),
        status=status,
        total_matched=Decimal("0"),
        total_adjustments=Decimal("0"),
        outstanding_deposits=Decimal("0"),
        outstanding_payments=Decimal("0"),
        statement_closing_balance=Decimal("100.00"),
        gl_closing_balance=Decimal("100.00"),
        reconciliation_difference=Decimal("0"),
    )

    def _calc():
        recon.reconciliation_difference = recon.statement_closing_balance - (
            recon.gl_closing_balance
            + recon.outstanding_deposits
            - recon.outstanding_payments
            + recon.total_adjustments
        )
        return recon.reconciliation_difference

    recon.calculate_difference = _calc
    return recon


def test_create_reconciliation_missing_bank_account():
    svc = BankReconciliationService()
    db = MagicMock()
    db.get.return_value = None

    with pytest.raises(HTTPException) as excinfo:
        svc.create_reconciliation(
            db,
            uuid4(),
            uuid4(),
            ReconciliationInput(
                reconciliation_date=date.today(),
                period_start=date.today(),
                period_end=date.today(),
                statement_opening_balance=Decimal("0"),
                statement_closing_balance=Decimal("0"),
            ),
        )

    assert excinfo.value.status_code == 404


def test_create_reconciliation_existing():
    svc = BankReconciliationService()
    db = MagicMock()
    bank_account = SimpleNamespace(gl_account_id=uuid4(), currency_code="NGN")
    db.get.return_value = bank_account
    existing = SimpleNamespace()
    db.execute.return_value.scalar_one_or_none.return_value = existing

    with pytest.raises(HTTPException) as excinfo:
        svc.create_reconciliation(
            db,
            uuid4(),
            uuid4(),
            ReconciliationInput(
                reconciliation_date=date.today(),
                period_start=date.today(),
                period_end=date.today(),
                statement_opening_balance=Decimal("0"),
                statement_closing_balance=Decimal("0"),
            ),
        )

    assert excinfo.value.status_code == 400


def test_create_reconciliation_success_with_prior():
    svc = BankReconciliationService()
    db = MagicMock()
    bank_account = SimpleNamespace(gl_account_id=uuid4(), currency_code="NGN")
    db.get.return_value = bank_account
    db.execute.return_value.scalar_one_or_none.return_value = None

    prior = SimpleNamespace(
        outstanding_deposits=Decimal("25.00"), outstanding_payments=Decimal("10.00")
    )

    svc._get_gl_balance = MagicMock(return_value=Decimal("100.00"))
    svc._get_prior_reconciliation = MagicMock(return_value=prior)

    def _safe_calc(self):
        self.total_adjustments = self.total_adjustments or Decimal("0")
        self.outstanding_deposits = self.outstanding_deposits or Decimal("0")
        self.outstanding_payments = self.outstanding_payments or Decimal("0")
        return Decimal("0")

    with patch.object(
        BankReconciliation, "calculate_difference", _safe_calc, create=True
    ):
        recon = svc.create_reconciliation(
            db,
            uuid4(),
            uuid4(),
            ReconciliationInput(
                reconciliation_date=date(2024, 1, 31),
                period_start=date(2024, 1, 1),
                period_end=date(2024, 1, 31),
                statement_opening_balance=Decimal("0"),
                statement_closing_balance=Decimal("100.00"),
            ),
        )

    assert recon.prior_outstanding_deposits == Decimal("25.00")
    assert recon.prior_outstanding_payments == Decimal("10.00")
    assert recon.currency_code == "NGN"


def test_list_and_count_filters():
    svc = BankReconciliationService()
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = [SimpleNamespace()]
    assert len(svc.list(db, uuid4(), bank_account_id=uuid4())) == 1

    db.execute.return_value.scalar.return_value = 5
    assert svc.count(db, uuid4(), status=ReconciliationStatus.draft) == 5


def test_add_match_happy_path():
    svc = BankReconciliationService()
    db = MagicMock()
    recon = _make_reconciliation(status=ReconciliationStatus.draft)
    stmt_line = SimpleNamespace(
        line_id=uuid4(),
        signed_amount=Decimal("50.00"),
        transaction_date=date(2024, 1, 10),
        description="Payment",
        reference="REF",
        is_matched=False,
        matched_at=None,
        matched_by=None,
        matched_journal_line_id=None,
    )
    gl_line = SimpleNamespace(
        line_id=uuid4(),
        debit_amount=Decimal("50.00"),
        credit_amount=Decimal("0"),
        description="Payment REF",
    )

    db.get.side_effect = [recon, stmt_line, gl_line]

    input_data = ReconciliationMatchInput(
        statement_line_id=stmt_line.line_id,
        journal_line_id=gl_line.line_id,
        match_type=ReconciliationMatchType.manual,
    )
    line = svc.add_match(db, recon.reconciliation_id, input_data, created_by=uuid4())

    assert isinstance(line, BankReconciliationLine)
    assert stmt_line.is_matched is True
    assert recon.total_matched == Decimal("50.00")


def test_add_match_invalid_status():
    svc = BankReconciliationService()
    db = MagicMock()
    recon = _make_reconciliation(status=ReconciliationStatus.approved)

    with pytest.raises(HTTPException) as excinfo:
        svc.add_match(
            db,
            recon.reconciliation_id,
            ReconciliationMatchInput(
                statement_line_id=uuid4(), journal_line_id=uuid4()
            ),
        )
    assert excinfo.value.status_code == 400


def test_add_adjustment_and_outstanding_items():
    svc = BankReconciliationService()
    db = MagicMock()
    recon = _make_reconciliation()
    db.get.return_value = recon

    adj = svc.add_adjustment(
        db,
        recon.reconciliation_id,
        transaction_date=date(2024, 1, 5),
        amount=Decimal("20.00"),
        description="Bank fee",
        adjustment_type="fee",
    )
    assert adj.is_adjustment is True
    assert recon.total_adjustments == Decimal("20.00")

    dep = svc.add_outstanding_item(
        db,
        recon.reconciliation_id,
        transaction_date=date(2024, 1, 6),
        amount=Decimal("30.00"),
        description="Deposit in transit",
        outstanding_type="deposit",
    )
    assert dep.is_outstanding is True
    assert recon.outstanding_deposits == Decimal("30.00")

    pay = svc.add_outstanding_item(
        db,
        recon.reconciliation_id,
        transaction_date=date(2024, 1, 7),
        amount=Decimal("15.00"),
        description="Outstanding check",
        outstanding_type="payment",
    )
    assert pay.is_outstanding is True
    assert recon.outstanding_payments == Decimal("15.00")


def test_auto_match_exact_and_fuzzy():
    svc = BankReconciliationService()
    db = MagicMock()

    recon = _make_reconciliation()
    recon.period_start = date(2024, 1, 1)
    recon.period_end = date(2024, 1, 31)
    recon.bank_account_id = uuid4()
    recon.bank_account = SimpleNamespace(gl_account_id=uuid4())

    stmt_exact = SimpleNamespace(
        line_id=uuid4(),
        signed_amount=Decimal("100.00"),
        transaction_date=date(2024, 1, 10),
        description="Vendor Payment",
        reference="INV-100",
        is_matched=False,
    )
    stmt_fuzzy = SimpleNamespace(
        line_id=uuid4(),
        signed_amount=Decimal("49.99"),
        transaction_date=date(2024, 1, 12),
        description="Office Supplies",
        reference=None,
        is_matched=False,
    )
    gl_exact = SimpleNamespace(
        line_id=uuid4(),
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0"),
        description="Payment INV-100",
        journal_entry=SimpleNamespace(entry_date=date(2024, 1, 10)),
    )
    gl_fuzzy = SimpleNamespace(
        line_id=uuid4(),
        debit_amount=Decimal("50.00"),
        credit_amount=Decimal("0"),
        description="Office Supplies Store",
        journal_entry=SimpleNamespace(entry_date=date(2024, 1, 11)),
    )

    db.get.return_value = recon

    def _execute_side_effect(*args, **kwargs):
        result = MagicMock()
        if _execute_side_effect.calls == 0:
            result.scalars.return_value.all.return_value = [stmt_exact, stmt_fuzzy]
        else:
            result.scalars.return_value.all.return_value = [gl_exact, gl_fuzzy]
        _execute_side_effect.calls += 1
        return result

    _execute_side_effect.calls = 0
    db.execute.side_effect = _execute_side_effect

    lookup = {
        stmt_exact.line_id: stmt_exact,
        stmt_fuzzy.line_id: stmt_fuzzy,
        gl_exact.line_id: gl_exact,
        gl_fuzzy.line_id: gl_fuzzy,
    }

    def _get(model, key):
        if key == recon.reconciliation_id:
            return recon
        return lookup.get(key)

    db.get.side_effect = _get

    result = svc.auto_match(db, recon.reconciliation_id, tolerance=Decimal("0.02"))
    assert result.matches_found == 2
    assert result.matches_created == 2
    assert result.unmatched_statement_lines == 0


def test_calculate_match_score():
    svc = BankReconciliationService()
    stmt = SimpleNamespace(
        signed_amount=Decimal("100.00"),
        transaction_date=date(2024, 1, 5),
        reference="ABC",
        description="ABC Payment",
    )
    gl = SimpleNamespace(
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0"),
        description="ABC Payment",
        journal_entry=SimpleNamespace(entry_date=date(2024, 1, 5)),
    )

    assert svc._calculate_match_score(stmt, gl) >= 90


def test_get_gl_balance_and_prior_reconciliation():
    svc = BankReconciliationService()
    db = MagicMock()
    db.execute.return_value.one.return_value = SimpleNamespace(
        debits=Decimal("100.00"), credits=Decimal("40.00")
    )
    assert svc._get_gl_balance(db, uuid4(), date(2024, 1, 1)) == Decimal("60.00")

    db.execute.return_value.scalar_one_or_none.return_value = "prior"
    assert svc._get_prior_reconciliation(db, uuid4(), date(2024, 1, 2)) == "prior"


def test_submit_approve_reject():
    svc = BankReconciliationService()
    db = MagicMock()
    recon = _make_reconciliation(status=ReconciliationStatus.draft)
    recon.reconciliation_date = date(2024, 1, 31)
    recon.statement_closing_balance = Decimal("150.00")
    recon.reconciliation_difference = Decimal("0")
    recon.bank_account = SimpleNamespace(
        last_reconciled_date=None,
        last_reconciled_balance=None,
    )
    db.get.return_value = recon

    submitted = svc.submit_for_review(db, recon.reconciliation_id)
    assert submitted.status == ReconciliationStatus.pending_review

    recon.status = ReconciliationStatus.pending_review
    approved = svc.approve(db, recon.reconciliation_id, approved_by=uuid4())
    assert approved.status == ReconciliationStatus.approved
    assert approved.bank_account.last_reconciled_balance == Decimal("150.00")

    recon.status = ReconciliationStatus.pending_review
    rejected = svc.reject(db, recon.reconciliation_id, rejected_by=uuid4(), notes="Fix")
    assert rejected.status == ReconciliationStatus.rejected


def test_get_reconciliation_report():
    svc = BankReconciliationService()
    db = MagicMock()
    recon = _make_reconciliation()
    recon.bank_account = SimpleNamespace()
    recon.adjusted_book_balance = Decimal("100.00")
    recon.is_reconciled = True
    recon.outstanding_deposits = Decimal("10.00")
    recon.outstanding_payments = Decimal("5.00")
    recon.lines = [
        SimpleNamespace(
            is_cleared=True,
            is_adjustment=False,
            is_outstanding=False,
            statement_amount=Decimal("25.00"),
        ),
        SimpleNamespace(
            is_cleared=False,
            is_adjustment=True,
            is_outstanding=False,
            statement_amount=Decimal("5.00"),
        ),
        SimpleNamespace(
            is_cleared=False,
            is_adjustment=False,
            is_outstanding=True,
            outstanding_type="deposit",
        ),
        SimpleNamespace(
            is_cleared=False,
            is_adjustment=False,
            is_outstanding=True,
            outstanding_type="payment",
        ),
    ]
    db.get.return_value = recon

    report = svc.get_reconciliation_report(db, recon.reconciliation_id)
    assert report["matched_items"]["count"] == 1
    assert report["adjustments"]["count"] == 1
    assert report["outstanding_deposits"]["count"] == 1
    assert report["outstanding_payments"]["count"] == 1
