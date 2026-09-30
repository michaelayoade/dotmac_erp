"""Tests for exact Paystack customer receipt reconciliation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.finance.ar.customer_payment import PaymentStatus
from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.banking.bank_statement import StatementLineType
from app.models.finance.banking.reconciliation_match_rule import (
    ReconciliationMatchRule,
)
from app.models.finance.gl.account import AccountType
from app.models.finance.gl.journal_entry import JournalStatus
from app.models.finance.payments.payment_intent import (
    PaymentDirection,
    PaymentIntentStatus,
)
from app.services.finance.banking.paystack_customer_auto_match import (
    PaystackCustomerAutoMatchError,
    PaystackCustomerAutoMatchService,
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
        currency_code="NGN",
        status=BankAccountStatus.active,
    )


def _rule(org_id, account_id, *, active: bool = True, writeoff_account_id=None):
    return SimpleNamespace(
        rule_id=uuid4(),
        organization_id=org_id,
        bank_account_id=account_id,
        is_active=active,
        source_doc_type="PAYMENT_INTENT",
        action_type="MATCH",
        match_credit=True,
        match_debit=False,
        conditions=[],
        writeoff_account_id=writeoff_account_id,
        match_count=0,
        last_matched_at=None,
    )


def _line(*, reference="PSK-CUST-1001", gross="1000.00", fee="15.00"):
    gross_amount = Decimal(gross)
    fee_amount = Decimal(fee)
    return SimpleNamespace(
        line_id=uuid4(),
        line_number=1,
        reference=reference,
        bank_reference=reference,
        transaction_id="987654321",
        description=f"Payment: {reference} via card",
        payee_payer="customer@example.test",
        bank_category=None,
        bank_code=None,
        amount=gross_amount - fee_amount,
        transaction_date=date(2026, 9, 15),
        transaction_type=StatementLineType.credit,
        raw_data={
            "gross_amount": str(gross_amount),
            "fees": str(fee_amount),
        },
    )


def _intent(org_id, account_id, payment_id, *, reference="PSK-CUST-1001"):
    return SimpleNamespace(
        intent_id=uuid4(),
        organization_id=org_id,
        bank_account_id=account_id,
        paystack_reference=reference,
        paystack_transaction_id="987654321",
        amount=Decimal("1000.00"),
        currency_code="NGN",
        direction=PaymentDirection.INBOUND,
        status=PaymentIntentStatus.COMPLETED,
        source_type="INVOICE",
        source_id=uuid4(),
        customer_payment_id=payment_id,
        paid_at=datetime(2026, 9, 15, 12, tzinfo=UTC),
    )


def _get_side_effect(account, rule):
    def get(model, _id):
        if model is BankAccount:
            return account
        if model is ReconciliationMatchRule:
            return rule
        return None

    return get


def test_reference_matching_uses_complete_normalized_paystack_tokens() -> None:
    line = _line(reference="NIP transfer")
    line.description = "Customer receipt PSK-CUST/2026_1001 via Paystack"

    assert _normalize_reference("PSK-CUST/2026_1001") == "pskcust20261001"
    assert "pskcust20261001" in _statement_reference_keys(line)
    assert "customer" not in _statement_reference_keys(line)


def test_disabled_customer_rule_is_rejected() -> None:
    org_id = uuid4()
    account_id = uuid4()
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id, active=False)
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)

    with pytest.raises(PaystackCustomerAutoMatchError, match="rule is disabled"):
        PaystackCustomerAutoMatchService.validate_configuration(
            db,
            org_id,
            account_id,
            rule.rule_id,
        )


def test_net_paystack_receipt_posts_fee_and_multi_matches() -> None:
    org_id = uuid4()
    account_id = uuid4()
    actor_id = uuid4()
    payment_id = uuid4()
    line = _line()
    intent = _intent(org_id, account_id, payment_id)
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id, writeoff_account_id=uuid4())
    payment = SimpleNamespace(payment_id=payment_id)
    receipt_line = SimpleNamespace(line_id=uuid4())
    fee_line = SimpleNamespace(line_id=uuid4())
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([intent]),
    ]
    db.scalar.return_value = None

    with (
        patch.object(
            PaystackCustomerAutoMatchService,
            "_resolve_payment",
            return_value=payment,
        ),
        patch.object(
            PaystackCustomerAutoMatchService,
            "_receipt_bank_line",
            return_value=receipt_line,
        ),
        patch.object(
            PaystackCustomerAutoMatchService,
            "_resolve_fee_bank_line",
            return_value=(fee_line, True),
        ) as resolve_fee,
        patch(
            "app.services.finance.banking.paystack_customer_auto_match."
            "bank_reconciliation_service.multi_match_statement_line"
        ) as multi_match,
    ):
        result = PaystackCustomerAutoMatchService().run(
            db,
            org_id,
            account_id,
            rule.rule_id,
            actor_id,
        )

    assert result.scanned == 1
    assert result.matched == 1
    assert result.fees_posted == 1
    resolve_fee.assert_called_once_with(
        db,
        org_id,
        account,
        rule,
        line,
        intent,
        Decimal("15.00"),
        actor_id,
    )
    multi_match.assert_called_once_with(
        db,
        org_id,
        line.line_id,
        [receipt_line.line_id, fee_line.line_id],
        matched_by=actor_id,
        force_match=False,
    )


def test_zero_fee_receipt_uses_exact_direct_match() -> None:
    org_id = uuid4()
    account_id = uuid4()
    actor_id = uuid4()
    payment_id = uuid4()
    line = _line(fee="0")
    intent = _intent(org_id, account_id, payment_id)
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id)
    payment = SimpleNamespace(payment_id=payment_id)
    receipt_line = SimpleNamespace(line_id=uuid4())
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([intent]),
    ]
    db.scalar.return_value = None

    with (
        patch.object(
            PaystackCustomerAutoMatchService,
            "_resolve_payment",
            return_value=payment,
        ),
        patch.object(
            PaystackCustomerAutoMatchService,
            "_receipt_bank_line",
            return_value=receipt_line,
        ),
        patch.object(
            PaystackCustomerAutoMatchService,
            "_resolve_fee_bank_line",
        ) as resolve_fee,
        patch(
            "app.services.finance.banking.paystack_customer_auto_match."
            "bank_reconciliation_service.match_statement_line"
        ) as match_line,
    ):
        result = PaystackCustomerAutoMatchService().run(
            db,
            org_id,
            account_id,
            rule.rule_id,
            actor_id,
        )

    assert result.matched == 1
    assert result.fees_posted == 0
    resolve_fee.assert_not_called()
    match_line.assert_called_once_with(
        db,
        org_id,
        line.line_id,
        receipt_line.line_id,
        matched_by=actor_id,
        force_match=False,
        source_type="PAYMENT_INTENT",
        source_id=intent.intent_id,
        match_state="confirmed",
    )


def test_gross_net_fee_mismatch_is_skipped() -> None:
    org_id = uuid4()
    account_id = uuid4()
    line = _line()
    line.raw_data["gross_amount"] = "999.00"
    intent = _intent(org_id, account_id, uuid4())
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id)
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([intent]),
    ]

    with patch.object(PaystackCustomerAutoMatchService, "_resolve_payment") as resolve:
        result = PaystackCustomerAutoMatchService().run(
            db,
            org_id,
            account_id,
            rule.rule_id,
            uuid4(),
        )

    assert result.matched == 0
    assert result.skipped_amount == 1
    resolve.assert_not_called()


def test_transaction_id_mismatch_is_skipped() -> None:
    org_id = uuid4()
    account_id = uuid4()
    line = _line()
    intent = _intent(org_id, account_id, uuid4())
    intent.paystack_transaction_id = "different-id"
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id)
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([intent]),
    ]

    result = PaystackCustomerAutoMatchService().run(
        db,
        org_id,
        account_id,
        rule.rule_id,
        uuid4(),
    )

    assert result.matched == 0
    assert result.skipped_transaction_id == 1


def test_pending_customer_payment_uses_existing_ar_posting_service() -> None:
    org_id = uuid4()
    actor_id = uuid4()
    payment_id = uuid4()
    account = _account(org_id, uuid4(), uuid4())
    line = _line()
    intent = _intent(org_id, account.bank_account_id, payment_id)
    payment = SimpleNamespace(
        payment_id=payment_id,
        organization_id=org_id,
        bank_account_id=account.bank_account_id,
        amount=Decimal("1000.00"),
        gross_amount=Decimal("1000.00"),
        wht_amount=Decimal("0"),
        currency_code="NGN",
        reference=intent.paystack_reference,
        correlation_id=str(intent.intent_id),
        payment_date=line.transaction_date,
        status=PaymentStatus.PENDING,
        journal_entry_id=None,
    )
    posted = SimpleNamespace(**payment.__dict__)
    posted.status = PaymentStatus.CLEARED
    posted.journal_entry_id = uuid4()
    db = MagicMock()
    db.get.return_value = payment

    with patch(
        "app.services.finance.banking.paystack_customer_auto_match."
        "CustomerPaymentService.post_payment",
        return_value=posted,
    ) as post_payment:
        result = PaystackCustomerAutoMatchService._resolve_payment(
            db,
            org_id,
            account,
            line,
            intent,
            Decimal("1000.00"),
            actor_id,
        )

    assert result is posted
    post_payment.assert_called_once_with(
        db,
        org_id,
        payment_id,
        actor_id,
        posting_date=line.transaction_date,
    )


def test_fee_journal_uses_configured_postable_account() -> None:
    org_id = uuid4()
    actor_id = uuid4()
    fee_account_id = uuid4()
    account = _account(org_id, uuid4(), uuid4())
    rule = _rule(org_id, account.bank_account_id, writeoff_account_id=fee_account_id)
    line = _line()
    intent = _intent(org_id, account.bank_account_id, uuid4())
    fee_account = SimpleNamespace(
        account_id=fee_account_id,
        organization_id=org_id,
        account_type=AccountType.POSTING,
        is_active=True,
        is_posting_allowed=True,
    )
    journal = SimpleNamespace(
        journal_entry_id=uuid4(),
        status=JournalStatus.POSTED,
    )
    fee_line = SimpleNamespace(
        line_id=uuid4(),
        account_id=fee_account_id,
        debit_amount=Decimal("15.00"),
        credit_amount=Decimal("0"),
    )
    bank_line = SimpleNamespace(
        line_id=uuid4(),
        account_id=account.gl_account_id,
        debit_amount=Decimal("0"),
        credit_amount=Decimal("15.00"),
    )
    db = MagicMock()
    db.get.return_value = fee_account
    db.scalars.side_effect = [
        _scalar_result([]),
        _scalar_result([fee_line, bank_line]),
    ]
    posting_result = SimpleNamespace(success=True)

    with patch(
        "app.services.finance.banking.paystack_customer_auto_match."
        "BasePostingAdapter.create_approve_and_post_journal",
        return_value=(journal, posting_result),
    ) as post_journal:
        resolved_line, created = (
            PaystackCustomerAutoMatchService._resolve_fee_bank_line(
                db,
                org_id,
                account,
                rule,
                line,
                intent,
                Decimal("15.00"),
                actor_id,
            )
        )

    assert resolved_line is bank_line
    assert created is True
    journal_input = post_journal.call_args.args[2]
    assert journal_input.source_document_type == "PAYSTACK_COLLECTION_FEE"
    assert journal_input.source_document_id == intent.intent_id
    assert journal_input.lines[0].account_id == fee_account_id
    assert journal_input.lines[0].debit_amount == Decimal("15.00")
    assert journal_input.lines[1].account_id == account.gl_account_id
    assert journal_input.lines[1].credit_amount == Decimal("15.00")
