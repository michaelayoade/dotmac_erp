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
        organization_id=uuid4(),
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
    stmt_line.statement = SimpleNamespace(organization_id=recon.organization_id)
    gl_line = SimpleNamespace(
        line_id=uuid4(),
        debit_amount=Decimal("50.00"),
        credit_amount=Decimal("0"),
        description="Payment REF",
    )
    gl_line.journal_entry = SimpleNamespace(organization_id=recon.organization_id)

    db.get.side_effect = [recon, stmt_line, gl_line]

    input_data = ReconciliationMatchInput(
        statement_line_id=stmt_line.line_id,
        journal_line_id=gl_line.line_id,
        match_type=ReconciliationMatchType.manual,
    )
    line = svc.add_match(
        db,
        recon.organization_id,
        recon.reconciliation_id,
        input_data,
        created_by=uuid4(),
    )

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
            recon.organization_id,
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
        recon.organization_id,
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
        recon.organization_id,
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
        recon.organization_id,
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

    result = svc.auto_match(
        db, recon.organization_id, recon.reconciliation_id, tolerance=Decimal("0.02")
    )
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

    # Base score without payee: amount(35) + date(25) + reference(25) = 85
    assert svc._calculate_match_score(stmt, gl) >= 80


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

    submitted = svc.submit_for_review(
        db, recon.organization_id, recon.reconciliation_id
    )
    assert submitted.status == ReconciliationStatus.pending_review

    recon.status = ReconciliationStatus.pending_review
    approved = svc.approve(
        db, recon.organization_id, recon.reconciliation_id, approved_by=uuid4()
    )
    assert approved.status == ReconciliationStatus.approved
    assert approved.bank_account.last_reconciled_balance == Decimal("150.00")

    recon.status = ReconciliationStatus.pending_review
    rejected = svc.reject(
        db,
        recon.organization_id,
        recon.reconciliation_id,
        rejected_by=uuid4(),
        notes="Fix",
    )
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


# =============================================================================
# Payee name scoring
# =============================================================================


class TestPayeeNameScore:
    """Tests for _calculate_payee_name_score static method."""

    svc = BankReconciliationService()

    def test_exact_match(self) -> None:
        assert self.svc._calculate_payee_name_score("Acme Corp", "Acme Corp") == 15.0

    def test_case_insensitive(self) -> None:
        assert self.svc._calculate_payee_name_score("ACME CORP", "acme corp") == 15.0

    def test_substring_match(self) -> None:
        assert self.svc._calculate_payee_name_score("Acme", "Acme Corp Ltd") == 15.0
        assert self.svc._calculate_payee_name_score("Acme Corp Ltd", "Acme") == 15.0

    def test_word_overlap_high(self) -> None:
        # "omega services" vs "omega services international" → substring match = 15
        # Use non-substring case: "omega engineering" vs "omega consulting engineering"
        # sp_significant = {"omega", "engineering"}, cn_significant = {"omega", "consulting", "engineering"}
        # overlap = 2/3 ≈ 0.67 ≥ 0.5 → 12.0
        score = self.svc._calculate_payee_name_score(
            "Omega Engineering", "Omega Consulting Engineering"
        )
        assert score == 12.0

    def test_word_overlap_low(self) -> None:
        # Only 1 significant word in common out of 3 → <50%
        score = self.svc._calculate_payee_name_score(
            "Global Logistics Inc", "Global Warehouse Corp"
        )
        assert score == 8.0

    def test_no_match(self) -> None:
        assert self.svc._calculate_payee_name_score("Alpha", "Beta") == 0.0

    def test_none_inputs(self) -> None:
        assert self.svc._calculate_payee_name_score(None, "Test") == 0.0
        assert self.svc._calculate_payee_name_score("Test", None) == 0.0
        assert self.svc._calculate_payee_name_score(None, None) == 0.0

    def test_empty_strings(self) -> None:
        assert self.svc._calculate_payee_name_score("", "Test") == 0.0
        assert self.svc._calculate_payee_name_score("  ", "Test") == 0.0

    def test_filler_words_ignored(self) -> None:
        # "The" and "Ltd" are filler — significant words are "acme" vs "acme"
        score = self.svc._calculate_payee_name_score("The Acme Ltd", "Acme Co")
        # "acme" is the only significant overlap, "co" is not in filler list
        # sp_significant = {"acme"}, cn_significant = {"acme", "co"}
        # overlap = 1/2 = 0.5 → 12.0
        assert score == 12.0


# =============================================================================
# Categorization bonus scoring
# =============================================================================


class TestCategorizationBonus:
    """Tests for _calculate_categorization_bonus static method."""

    svc = BankReconciliationService()

    def test_account_match_bonus(self) -> None:
        """Suggested account matching GL line account gives +5."""
        account_id = uuid4()
        stmt_line = SimpleNamespace(
            suggested_account_id=account_id, suggested_rule_id=None
        )
        gl_line = SimpleNamespace(
            account_id=account_id,
            journal_entry=SimpleNamespace(source_module=None),
        )
        bonus = self.svc._calculate_categorization_bonus(stmt_line, gl_line, None)
        assert bonus == 5.0

    def test_module_match_ar(self) -> None:
        """AR module + customer counterparty gives +3."""
        from app.services.finance.banking.payment_metadata import PaymentMetadata

        meta = PaymentMetadata(
            source_type="customer_payment",
            payment_id=uuid4(),
            payment_number=None,
            counterparty_name="Test",
            counterparty_id=uuid4(),
            counterparty_type="customer",
            invoice_numbers=[],
        )
        stmt_line = SimpleNamespace(suggested_account_id=None, suggested_rule_id=None)
        gl_line = SimpleNamespace(
            account_id=uuid4(),
            journal_entry=SimpleNamespace(source_module="AR"),
        )
        bonus = self.svc._calculate_categorization_bonus(stmt_line, gl_line, meta)
        assert bonus == 3.0

    def test_module_match_ap(self) -> None:
        """AP module + supplier counterparty gives +3."""
        from app.services.finance.banking.payment_metadata import PaymentMetadata

        meta = PaymentMetadata(
            source_type="supplier_payment",
            payment_id=uuid4(),
            payment_number=None,
            counterparty_name="Test",
            counterparty_id=uuid4(),
            counterparty_type="supplier",
            invoice_numbers=[],
        )
        stmt_line = SimpleNamespace(suggested_account_id=None, suggested_rule_id=None)
        gl_line = SimpleNamespace(
            account_id=uuid4(),
            journal_entry=SimpleNamespace(source_module="AP"),
        )
        bonus = self.svc._calculate_categorization_bonus(stmt_line, gl_line, meta)
        assert bonus == 3.0

    def test_no_bonus(self) -> None:
        """No categorization data gives 0 bonus."""
        stmt_line = SimpleNamespace(suggested_account_id=None, suggested_rule_id=None)
        gl_line = SimpleNamespace(
            account_id=uuid4(),
            journal_entry=SimpleNamespace(source_module=None),
        )
        bonus = self.svc._calculate_categorization_bonus(stmt_line, gl_line, None)
        assert bonus == 0.0

    def test_combined_account_and_module(self) -> None:
        """Account match (+5) + module match (+3) = +8."""
        from app.services.finance.banking.payment_metadata import PaymentMetadata

        account_id = uuid4()
        meta = PaymentMetadata(
            source_type="customer_payment",
            payment_id=uuid4(),
            payment_number=None,
            counterparty_name="Test",
            counterparty_id=uuid4(),
            counterparty_type="customer",
            invoice_numbers=[],
        )
        stmt_line = SimpleNamespace(
            suggested_account_id=account_id, suggested_rule_id=None
        )
        gl_line = SimpleNamespace(
            account_id=account_id,
            journal_entry=SimpleNamespace(source_module="AR"),
        )
        bonus = self.svc._calculate_categorization_bonus(stmt_line, gl_line, meta)
        assert bonus == 8.0


# =============================================================================
# Full match scoring with payee factor
# =============================================================================


def test_calculate_match_score_with_payee() -> None:
    """Score includes payee name factor when metadata is available."""
    from app.services.finance.banking.payment_metadata import PaymentMetadata

    svc = BankReconciliationService()
    source_doc_id = uuid4()
    meta = PaymentMetadata(
        source_type="customer_payment",
        payment_id=uuid4(),
        payment_number="PAY-001",
        counterparty_name="Acme Corp",
        counterparty_id=uuid4(),
        counterparty_type="customer",
        invoice_numbers=[],
    )
    gl_metadata = {source_doc_id: meta}

    stmt = SimpleNamespace(
        signed_amount=Decimal("100.00"),
        transaction_date=date(2024, 1, 5),
        reference="ABC",
        description="ABC Payment",
        payee_payer="Acme Corp",
        suggested_account_id=None,
        suggested_rule_id=None,
    )
    gl = SimpleNamespace(
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0"),
        description="ABC Payment",
        account_id=uuid4(),
        journal_entry=SimpleNamespace(
            entry_date=date(2024, 1, 5),
            source_document_type="customer_payment",
            source_document_id=source_doc_id,
            source_module=None,
        ),
    )

    # Without payee metadata
    score_no_payee = svc._calculate_match_score(stmt, gl)

    # With payee metadata
    score_with_payee = svc._calculate_match_score(
        stmt, gl, db=MagicMock(), gl_metadata=gl_metadata
    )

    # Payee adds 15 points for exact match
    assert score_with_payee > score_no_payee
    assert score_with_payee >= 90  # 35 (amount) + 25 (date) + 25 (ref) + 15 (payee)


def test_calculate_match_score_no_payee_match() -> None:
    """Score excludes payee points when payee names don't match."""
    from app.services.finance.banking.payment_metadata import PaymentMetadata

    svc = BankReconciliationService()
    source_doc_id = uuid4()
    meta = PaymentMetadata(
        source_type="customer_payment",
        payment_id=uuid4(),
        payment_number=None,
        counterparty_name="Totally Different Name",
        counterparty_id=uuid4(),
        counterparty_type="customer",
        invoice_numbers=[],
    )
    gl_metadata = {source_doc_id: meta}

    stmt = SimpleNamespace(
        signed_amount=Decimal("100.00"),
        transaction_date=date(2024, 1, 5),
        reference="ABC",
        description="ABC Payment",
        payee_payer="Acme Corp",
        suggested_account_id=None,
        suggested_rule_id=None,
    )
    gl = SimpleNamespace(
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0"),
        description="ABC Payment",
        account_id=uuid4(),
        journal_entry=SimpleNamespace(
            entry_date=date(2024, 1, 5),
            source_document_type="customer_payment",
            source_document_id=source_doc_id,
            source_module=None,
        ),
    )

    score = svc._calculate_match_score(
        stmt, gl, db=MagicMock(), gl_metadata=gl_metadata
    )

    # Amount 35 + date 25 + reference 25 + payee 0 = 85
    assert score == 85.0


# =============================================================================
# Match suggestions
# =============================================================================


def test_get_match_suggestions_empty_when_no_lines() -> None:
    """Returns empty dict when no unmatched lines exist."""
    svc = BankReconciliationService()
    db = MagicMock()
    recon = _make_reconciliation()
    recon.period_start = date(2024, 1, 1)
    recon.period_end = date(2024, 1, 31)
    recon.bank_account_id = uuid4()
    recon.bank_account = SimpleNamespace(gl_account_id=uuid4())
    db.get.return_value = recon

    # Both queries return empty
    db.execute.return_value.scalars.return_value.all.return_value = []

    result = svc.get_match_suggestions(
        db, recon.organization_id, recon.reconciliation_id
    )
    assert result == {}


def test_get_match_suggestions_finds_best() -> None:
    """Returns the best match per statement line above min_confidence."""
    svc = BankReconciliationService()
    db = MagicMock()

    recon = _make_reconciliation()
    recon.period_start = date(2024, 1, 1)
    recon.period_end = date(2024, 1, 31)
    recon.bank_account = SimpleNamespace(gl_account_id=uuid4())

    stmt = SimpleNamespace(
        line_id=uuid4(),
        signed_amount=Decimal("100.00"),
        transaction_date=date(2024, 1, 10),
        description="Payment ABC",
        reference="ABC",
        payee_payer=None,
        is_matched=False,
        suggested_account_id=None,
        suggested_rule_id=None,
    )
    gl = SimpleNamespace(
        line_id=uuid4(),
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0"),
        description="Payment ABC",
        account_id=uuid4(),
        journal_entry=SimpleNamespace(
            entry_date=date(2024, 1, 10),
            source_document_type=None,
            source_document_id=None,
            source_module=None,
        ),
    )

    db.get.return_value = recon

    call_count = 0

    def _execute_side(*args, **kwargs):
        nonlocal call_count
        result = MagicMock()
        if call_count == 0:
            result.scalars.return_value.all.return_value = [stmt]
        else:
            result.scalars.return_value.all.return_value = [gl]
        call_count += 1
        return result

    db.execute.side_effect = _execute_side

    suggestions = svc.get_match_suggestions(
        db, recon.organization_id, recon.reconciliation_id, min_confidence=30.0
    )

    assert stmt.line_id in suggestions
    suggestion = suggestions[stmt.line_id]
    assert suggestion.journal_line_id == gl.line_id
    assert suggestion.confidence >= 80  # exact amount + date + ref match


def test_get_match_suggestions_below_threshold() -> None:
    """Lines with scores below min_confidence are excluded."""
    svc = BankReconciliationService()
    db = MagicMock()

    recon = _make_reconciliation()
    recon.period_start = date(2024, 1, 1)
    recon.period_end = date(2024, 1, 31)
    recon.bank_account = SimpleNamespace(gl_account_id=uuid4())

    # Statement line with amount that doesn't match any GL line
    stmt = SimpleNamespace(
        line_id=uuid4(),
        signed_amount=Decimal("999.99"),
        transaction_date=date(2024, 1, 10),
        description="Unique Payment",
        reference=None,
        payee_payer=None,
        is_matched=False,
        suggested_account_id=None,
        suggested_rule_id=None,
    )
    gl = SimpleNamespace(
        line_id=uuid4(),
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0"),
        description="Completely Different",
        account_id=uuid4(),
        journal_entry=SimpleNamespace(
            entry_date=date(2024, 1, 20),
            source_document_type=None,
            source_document_id=None,
            source_module=None,
        ),
    )

    db.get.return_value = recon

    call_count = 0

    def _execute_side(*args, **kwargs):
        nonlocal call_count
        result = MagicMock()
        if call_count == 0:
            result.scalars.return_value.all.return_value = [stmt]
        else:
            result.scalars.return_value.all.return_value = [gl]
        call_count += 1
        return result

    db.execute.side_effect = _execute_side

    # High threshold — amounts don't match so score will be low
    suggestions = svc.get_match_suggestions(
        db, recon.organization_id, recon.reconciliation_id, min_confidence=80.0
    )
    assert suggestions == {}


# =============================================================================
# Multi-match
# =============================================================================


def test_add_multi_match_happy_path() -> None:
    """Multi-match creates lines for each stmt×GL pair."""
    svc = BankReconciliationService()
    db = MagicMock()

    recon = _make_reconciliation()
    stmt1 = SimpleNamespace(
        line_id=uuid4(),
        signed_amount=Decimal("60.00"),
        transaction_date=date(2024, 1, 10),
        description="Part 1",
        reference="REF-1",
        is_matched=False,
        matched_at=None,
        matched_by=None,
    )
    stmt2 = SimpleNamespace(
        line_id=uuid4(),
        signed_amount=Decimal("40.00"),
        transaction_date=date(2024, 1, 11),
        description="Part 2",
        reference="REF-2",
        is_matched=False,
        matched_at=None,
        matched_by=None,
    )
    gl = SimpleNamespace(
        line_id=uuid4(),
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0"),
        description="Full Payment",
    )

    lookup = {
        recon.reconciliation_id: recon,
        stmt1.line_id: stmt1,
        stmt2.line_id: stmt2,
        gl.line_id: gl,
    }
    db.get.side_effect = lambda _model, key: lookup.get(key)

    lines = svc.add_multi_match(
        db,
        recon.organization_id,
        recon.reconciliation_id,
        statement_line_ids=[stmt1.line_id, stmt2.line_id],
        journal_line_ids=[gl.line_id],
        tolerance=Decimal("0.01"),
    )

    # 2 statement lines × 1 GL line = 2 recon lines
    assert len(lines) == 2
    assert all(isinstance(l, BankReconciliationLine) for l in lines)

    # Statement lines marked as matched
    assert stmt1.is_matched is True
    assert stmt2.is_matched is True

    # Reconciliation total updated
    assert recon.total_matched == Decimal("100.00")


def test_add_multi_match_amount_mismatch() -> None:
    """Raises 400 when statement total doesn't match GL total."""
    svc = BankReconciliationService()
    db = MagicMock()

    recon = _make_reconciliation()
    stmt = SimpleNamespace(
        line_id=uuid4(),
        signed_amount=Decimal("100.00"),
    )
    gl = SimpleNamespace(
        line_id=uuid4(),
        debit_amount=Decimal("80.00"),
        credit_amount=Decimal("0"),
    )

    lookup = {
        recon.reconciliation_id: recon,
        stmt.line_id: stmt,
        gl.line_id: gl,
    }
    db.get.side_effect = lambda _model, key: lookup.get(key)

    with pytest.raises(HTTPException) as excinfo:
        svc.add_multi_match(
            db,
            recon.organization_id,
            recon.reconciliation_id,
            statement_line_ids=[stmt.line_id],
            journal_line_ids=[gl.line_id],
            tolerance=Decimal("0.01"),
        )

    assert excinfo.value.status_code == 400
    assert "Amount mismatch" in excinfo.value.detail


def test_add_multi_match_invalid_status() -> None:
    """Cannot multi-match on an approved reconciliation."""
    svc = BankReconciliationService()
    db = MagicMock()

    recon = _make_reconciliation(status=ReconciliationStatus.approved)
    db.get.return_value = recon

    with pytest.raises(HTTPException) as excinfo:
        svc.add_multi_match(
            db,
            recon.organization_id,
            recon.reconciliation_id,
            statement_line_ids=[uuid4()],
            journal_line_ids=[uuid4()],
        )

    assert excinfo.value.status_code == 400


def test_add_multi_match_within_tolerance() -> None:
    """Multi-match succeeds when difference is within tolerance."""
    svc = BankReconciliationService()
    db = MagicMock()

    recon = _make_reconciliation()
    stmt = SimpleNamespace(
        line_id=uuid4(),
        signed_amount=Decimal("100.00"),
        transaction_date=date(2024, 1, 10),
        description="Payment",
        reference="REF",
        is_matched=False,
        matched_at=None,
        matched_by=None,
    )
    gl = SimpleNamespace(
        line_id=uuid4(),
        debit_amount=Decimal("100.005"),
        credit_amount=Decimal("0"),
    )

    lookup = {
        recon.reconciliation_id: recon,
        stmt.line_id: stmt,
        gl.line_id: gl,
    }
    db.get.side_effect = lambda _model, key: lookup.get(key)

    lines = svc.add_multi_match(
        db,
        recon.organization_id,
        recon.reconciliation_id,
        statement_line_ids=[stmt.line_id],
        journal_line_ids=[gl.line_id],
        tolerance=Decimal("0.01"),
    )

    assert len(lines) == 1


def test_add_multi_match_statement_not_found() -> None:
    """Raises 404 when a statement line doesn't exist."""
    svc = BankReconciliationService()
    db = MagicMock()

    recon = _make_reconciliation()
    missing_id = uuid4()

    def _get(_model, key):
        if key == recon.reconciliation_id:
            return recon
        return None  # Statement line not found

    db.get.side_effect = _get

    with pytest.raises(HTTPException) as excinfo:
        svc.add_multi_match(
            db,
            recon.organization_id,
            recon.reconciliation_id,
            statement_line_ids=[missing_id],
            journal_line_ids=[uuid4()],
        )

    assert excinfo.value.status_code == 404


# =============================================================================
# _check_rule_payee_link helper
# =============================================================================


def test_check_rule_payee_link_matching_customer() -> None:
    """Returns 10.0 when rule's payee has matching customer_id."""
    from app.services.finance.banking.bank_reconciliation import _check_rule_payee_link

    db = MagicMock()
    rule_id = uuid4()
    payee_id = uuid4()
    customer_id = uuid4()

    rule = SimpleNamespace(payee_id=payee_id)
    payee = SimpleNamespace(customer_id=customer_id, supplier_id=None)

    db.get.side_effect = [rule, payee]

    assert _check_rule_payee_link(db, rule_id, customer_id) == 10.0


def test_check_rule_payee_link_matching_supplier() -> None:
    """Returns 10.0 when rule's payee has matching supplier_id."""
    from app.services.finance.banking.bank_reconciliation import _check_rule_payee_link

    db = MagicMock()
    rule_id = uuid4()
    payee_id = uuid4()
    supplier_id = uuid4()

    rule = SimpleNamespace(payee_id=payee_id)
    payee = SimpleNamespace(customer_id=None, supplier_id=supplier_id)

    db.get.side_effect = [rule, payee]

    assert _check_rule_payee_link(db, rule_id, supplier_id) == 10.0


def test_check_rule_payee_link_no_match() -> None:
    """Returns 0.0 when payee's customer/supplier doesn't match counterparty."""
    from app.services.finance.banking.bank_reconciliation import _check_rule_payee_link

    db = MagicMock()
    rule_id = uuid4()
    payee_id = uuid4()

    rule = SimpleNamespace(payee_id=payee_id)
    payee = SimpleNamespace(customer_id=uuid4(), supplier_id=uuid4())

    db.get.side_effect = [rule, payee]

    assert _check_rule_payee_link(db, rule_id, uuid4()) == 0.0


def test_check_rule_payee_link_no_rule() -> None:
    """Returns 0.0 when rule doesn't exist."""
    from app.services.finance.banking.bank_reconciliation import _check_rule_payee_link

    db = MagicMock()
    db.get.return_value = None

    assert _check_rule_payee_link(db, uuid4(), uuid4()) == 0.0


def test_check_rule_payee_link_no_payee_id() -> None:
    """Returns 0.0 when rule has no payee_id."""
    from app.services.finance.banking.bank_reconciliation import _check_rule_payee_link

    db = MagicMock()
    rule = SimpleNamespace(payee_id=None)
    db.get.return_value = rule

    assert _check_rule_payee_link(db, uuid4(), uuid4()) == 0.0
