"""Tests for focused Paystack expense statement matching."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.banking.bank_statement import StatementLineType
from app.models.finance.banking.reconciliation_match_rule import (
    ReconciliationMatchRule,
)
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.services.finance.banking.paystack_expense_auto_match import (
    PaystackExpenseAutoMatchError,
    PaystackExpenseAutoMatchService,
    _normalize_reference,
    _statement_reference_keys,
)


def _scalar_result(values: list[object]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = values
    return result


def _account(org_id, account_id, gl_account_id):
    return SimpleNamespace(
        organization_id=org_id,
        bank_account_id=account_id,
        gl_account_id=gl_account_id,
        status=BankAccountStatus.active,
    )


def _rule(org_id, account_id, *, is_active: bool = True, conditions=None):
    return SimpleNamespace(
        rule_id=uuid4(),
        organization_id=org_id,
        bank_account_id=account_id,
        is_active=is_active,
        source_doc_type="EXPENSE",
        action_type="MATCH",
        match_debit=True,
        match_credit=False,
        conditions=conditions or [],
        match_count=0,
        last_matched_at=None,
    )


def _get_side_effect(account, rule, journal=None):
    def get(model, _id):
        if model is BankAccount:
            return account
        if model is ReconciliationMatchRule:
            return rule
        if model is JournalEntry:
            return journal
        return None

    return get


def _line(*, reference: str, amount: str = "250.00"):
    return SimpleNamespace(
        line_id=uuid4(),
        line_number=1,
        reference=reference,
        bank_reference=None,
        transaction_id=None,
        description="Paystack transfer completed",
        payee_payer=None,
        bank_category=None,
        bank_code=None,
        amount=Decimal(amount),
        transaction_date=date(2026, 9, 15),
        transaction_type=StatementLineType.debit,
    )


def _claim(*, reference: str, amount: str = "250.00"):
    return SimpleNamespace(
        claim_id=uuid4(),
        payment_reference=reference,
        net_payable_amount=Decimal(amount),
        paid_on=date(2026, 9, 15),
        reimbursement_journal_id=None,
    )


def test_reference_matching_normalizes_only_complete_reference_tokens() -> None:
    line = _line(reference="Paystack reference: TRF-ABC_123")
    line.description = "Transfer successful: trf-abc_123; beneficiary employee"

    assert _normalize_reference("TRF-ABC_123") == "trfabc123"
    assert "trfabc123" in _statement_reference_keys(line)
    assert "transfer" not in _statement_reference_keys(line)


def test_disabled_rule_is_rejected() -> None:
    org_id = uuid4()
    account_id = uuid4()
    db = MagicMock()
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id, is_active=False)
    db.get.side_effect = _get_side_effect(account, rule)

    with pytest.raises(
        PaystackExpenseAutoMatchError,
        match="automation rule is disabled",
    ):
        PaystackExpenseAutoMatchService.validate_configuration(
            db, org_id, account_id, rule.rule_id
        )


def test_rule_assigned_to_another_account_is_rejected() -> None:
    org_id = uuid4()
    account_id = uuid4()
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, uuid4())
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)

    with pytest.raises(
        PaystackExpenseAutoMatchError,
        match="rule is not assigned to this bank account",
    ):
        PaystackExpenseAutoMatchService.validate_configuration(
            db, org_id, account_id, rule.rule_id
        )


def test_statement_line_must_satisfy_selected_rule_conditions() -> None:
    org_id = uuid4()
    account_id = uuid4()
    actor_id = uuid4()
    line = _line(reference="TRF-RULE-123")
    account = _account(org_id, account_id, uuid4())
    rule = _rule(
        org_id,
        account_id,
        conditions=[
            {"field": "DESCRIPTION", "operator": "CONTAINS", "value": "payroll"}
        ],
    )
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)
    db.scalars.side_effect = [_scalar_result([line]), _scalar_result([])]

    with patch(
        "app.services.finance.banking.paystack_expense_auto_match."
        "ExpensePostingAdapter.post_expense_reimbursement"
    ) as post_reimbursement:
        result = PaystackExpenseAutoMatchService().run(
            db,
            org_id,
            account_id,
            rule.rule_id,
            actor_id,
        )

    assert result.scanned == 1
    assert result.skipped_rule_condition == 1
    assert result.matched == 0
    post_reimbursement.assert_not_called()


def test_exact_reference_amount_and_date_posts_then_matches() -> None:
    org_id = uuid4()
    account_id = uuid4()
    gl_account_id = uuid4()
    journal_id = uuid4()
    bank_gl_line_id = uuid4()
    actor_id = uuid4()
    line = _line(reference="TRF-ABC_123")
    claim = _claim(reference="trfabc123")
    account = _account(org_id, account_id, gl_account_id)
    rule = _rule(org_id, account_id)
    journal = SimpleNamespace(
        organization_id=org_id,
        status=JournalStatus.POSTED,
        source_document_type="EXPENSE_REIMBURSEMENT",
        source_document_id=claim.claim_id,
    )
    bank_gl_line = SimpleNamespace(line_id=bank_gl_line_id)

    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule, journal)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([claim]),
        _scalar_result([bank_gl_line]),
    ]
    db.scalar.return_value = None

    with (
        patch(
            "app.services.finance.banking.paystack_expense_auto_match."
            "ExpensePostingAdapter.post_expense_reimbursement",
            return_value=SimpleNamespace(
                success=True,
                journal_entry_id=journal_id,
            ),
        ) as post_reimbursement,
        patch(
            "app.services.finance.banking.paystack_expense_auto_match."
            "bank_reconciliation_service.match_statement_line"
        ) as match_line,
    ):
        result = PaystackExpenseAutoMatchService().run(
            db,
            org_id,
            account_id,
            rule.rule_id,
            actor_id,
        )

    assert result.scanned == 1
    assert result.matched == 1
    assert result.journals_created == 1
    assert result.failed == 0
    post_reimbursement.assert_called_once()
    match_line.assert_called_once_with(
        db,
        org_id,
        line.line_id,
        bank_gl_line_id,
        matched_by=actor_id,
        force_match=False,
        source_type="EXPENSE_REIMBURSEMENT",
        source_id=claim.claim_id,
        match_state="confirmed",
    )


def test_duplicate_statement_reference_is_skipped_without_posting() -> None:
    org_id = uuid4()
    account_id = uuid4()
    gl_account_id = uuid4()
    actor_id = uuid4()
    claim = _claim(reference="TRF-DUPLICATE-1")
    account = _account(org_id, account_id, gl_account_id)
    rule = _rule(org_id, account_id)
    lines = [
        _line(reference="TRF-DUPLICATE-1"),
        _line(reference="TRF-DUPLICATE-1"),
    ]
    lines[1].line_number = 2
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)
    db.scalars.side_effect = [
        _scalar_result(lines),
        _scalar_result([claim]),
    ]

    with (
        patch(
            "app.services.finance.banking.paystack_expense_auto_match."
            "ExpensePostingAdapter.post_expense_reimbursement"
        ) as post_reimbursement,
        patch(
            "app.services.finance.banking.paystack_expense_auto_match."
            "bank_reconciliation_service.match_statement_line"
        ) as match_line,
    ):
        result = PaystackExpenseAutoMatchService().run(
            db,
            org_id,
            account_id,
            rule.rule_id,
            actor_id,
        )

    assert result.scanned == 2
    assert result.matched == 0
    assert result.skipped_duplicate_claim == 2
    post_reimbursement.assert_not_called()
    match_line.assert_not_called()


def test_amount_mismatch_is_skipped_without_posting() -> None:
    org_id = uuid4()
    account_id = uuid4()
    actor_id = uuid4()
    line = _line(reference="TRF-AMOUNT-1", amount="250.00")
    claim = _claim(reference="TRF-AMOUNT-1", amount="251.00")
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id)
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([claim]),
    ]

    with (
        patch(
            "app.services.finance.banking.paystack_expense_auto_match."
            "ExpensePostingAdapter.post_expense_reimbursement"
        ) as post_reimbursement,
    ):
        result = PaystackExpenseAutoMatchService().run(
            db,
            org_id,
            account_id,
            rule.rule_id,
            actor_id,
        )

    assert result.matched == 0
    assert result.skipped_amount == 1
    post_reimbursement.assert_not_called()


def test_paid_date_mismatch_is_skipped_without_posting() -> None:
    org_id = uuid4()
    account_id = uuid4()
    actor_id = uuid4()
    line = _line(reference="TRF-DATE-123")
    claim = _claim(reference="TRF-DATE-123")
    claim.paid_on = date(2026, 9, 16)
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id)
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([claim]),
    ]

    with (
        patch(
            "app.services.finance.banking.paystack_expense_auto_match."
            "ExpensePostingAdapter.post_expense_reimbursement"
        ) as post_reimbursement,
    ):
        result = PaystackExpenseAutoMatchService().run(
            db,
            org_id,
            account_id,
            rule.rule_id,
            actor_id,
        )

    assert result.matched == 0
    assert result.skipped_date == 1
    post_reimbursement.assert_not_called()


def test_already_consumed_bank_journal_line_is_skipped() -> None:
    org_id = uuid4()
    account_id = uuid4()
    gl_account_id = uuid4()
    actor_id = uuid4()
    journal_id = uuid4()
    line = _line(reference="TRF-CONSUMED-123")
    claim = _claim(reference="TRF-CONSUMED-123")
    claim.reimbursement_journal_id = journal_id
    account = _account(org_id, account_id, gl_account_id)
    rule = _rule(org_id, account_id)
    journal = SimpleNamespace(
        organization_id=org_id,
        status=JournalStatus.POSTED,
        source_document_type="EXPENSE_REIMBURSEMENT",
        source_document_id=claim.claim_id,
    )
    bank_gl_line = SimpleNamespace(line_id=uuid4())
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule, journal)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([claim]),
        _scalar_result([bank_gl_line]),
    ]
    db.scalar.return_value = uuid4()

    with (
        patch(
            "app.services.finance.banking.paystack_expense_auto_match."
            "bank_reconciliation_service.match_statement_line"
        ) as match_line,
    ):
        result = PaystackExpenseAutoMatchService().run(
            db,
            org_id,
            account_id,
            rule.rule_id,
            actor_id,
        )

    assert result.matched == 0
    assert result.skipped_consumed_journal_line == 1
    match_line.assert_not_called()
