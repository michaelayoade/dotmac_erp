"""Tests for exact Paystack customer receipt reconciliation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.finance.ar.customer_payment import (
    CustomerPayment,
    PaymentMethod,
    PaymentStatus,
)
from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    BankStatementLineMatch,
    StatementLineType,
)
from app.models.finance.banking.reconciliation_match_rule import (
    ReconciliationMatchRule,
)
from app.models.finance.gl.account import AccountType
from app.models.finance.gl.journal_entry import JournalStatus
from app.models.finance.payments.payment_intent import (
    PaymentIntent,
    PaymentDirection,
    PaymentIntentStatus,
)
from app.services.finance.banking.paystack_customer_auto_match import (
    PaystackCustomerAutoMatchError,
    PaystackCustomerAutoMatchService,
    _normalize_reference,
    _statement_reference_keys,
    _SkipCandidate,
)
from app.services.finance.banking.reconciliation_rule_service import (
    ReconciliationRuleService,
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
        date_window_days=None,
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


def _imported_payment(org_id, account_id, line):
    return SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        bank_account_id=account_id,
        reference=line.reference,
        dotmac_sub_id="imported-source-id",
        splynx_id=None,
        erpnext_id=None,
        currency_code="NGN",
        amount=line.amount,
        gross_amount=line.amount,
        wht_amount=Decimal("0"),
        payment_date=line.transaction_date,
        status=PaymentStatus.CLEARED,
        journal_entry_id=uuid4(),
    )


def _imported_case():
    org_id, account_id, actor_id = uuid4(), uuid4(), uuid4()
    line = _line()
    line.statement_id = uuid4()
    line.raw_data["paystack_id"] = line.transaction_id
    payment = _imported_payment(org_id, account_id, line)
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id)
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([]),  # no gateway payment intent
        _scalar_result([payment]),
        _scalar_result([line.statement_id]),
    ]
    db.scalar.return_value = None
    return db, org_id, account, rule, actor_id, line, payment


def test_imported_net_receipt_matches_without_gateway_intent_or_new_payment():
    db, org_id, account, rule, actor_id, line, payment = _imported_case()
    bank_line = SimpleNamespace(line_id=uuid4())
    with (
        patch.object(
            PaystackCustomerAutoMatchService,
            "_receipt_bank_line",
            return_value=bank_line,
        ) as receipt_bank_line,
        patch(
            "app.services.finance.banking.paystack_customer_auto_match.bank_reconciliation_service.match_statement_line"
        ) as match,
        patch(
            "app.services.finance.banking.paystack_customer_auto_match.CustomerPaymentService.post_payment"
        ) as post_payment,
        patch(
            "app.services.finance.banking.paystack_customer_auto_match.BasePostingAdapter.create_approve_and_post_journal"
        ) as post_journal,
    ):
        result = PaystackCustomerAutoMatchService().run(
            db, org_id, account.bank_account_id, rule.rule_id, actor_id
        )
    assert result.matched == result.matched_imported_receipts == 1
    assert result.skipped_no_intent_reference == result.fees_posted == 0
    receipt_bank_line.assert_called_once_with(db, org_id, account, payment, line.amount)
    match.assert_called_once_with(
        db,
        org_id,
        line.line_id,
        bank_line.line_id,
        matched_by=actor_id,
        force_match=False,
        source_type="CUSTOMER_PAYMENT",
        source_id=payment.payment_id,
        match_state="confirmed",
    )
    post_payment.assert_not_called()
    post_journal.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        "wrong_bank",
        "wrong_org",
        "wrong_currency",
        "pending",
        "reversed",
        "missing_journal",
        "not_imported",
        "wrong_amount",
        "wht",
        "invalid_gross",
        "bad_provider_id",
        "no_provider_id",
        "nonfinite_fee",
        "bad_fee_identity",
        "untrusted_statement",
        "consumed_bank_line",
        "wrong_journal",
    ],
)
def test_imported_receipt_safety_checks_fail_closed(failure):
    db, org_id, account, rule, actor_id, line, payment = _imported_case()
    if failure == "wrong_bank":
        payment.bank_account_id = uuid4()
    elif failure == "wrong_org":
        payment.organization_id = uuid4()
    elif failure == "wrong_currency":
        payment.currency_code = "USD"
    elif failure == "pending":
        payment.status = PaymentStatus.PENDING
    elif failure == "reversed":
        payment.status = PaymentStatus.REVERSED
    elif failure == "missing_journal":
        payment.journal_entry_id = None
    elif failure == "not_imported":
        payment.dotmac_sub_id = None
    elif failure == "wrong_amount":
        payment.amount += Decimal("1")
    elif failure == "wht":
        payment.wht_amount = Decimal("1")
    elif failure == "invalid_gross":
        payment.gross_amount += Decimal("1")
    elif failure == "bad_provider_id":
        line.raw_data["paystack_id"] = "different-id"
    elif failure == "no_provider_id":
        line.raw_data.pop("paystack_id")
    elif failure == "nonfinite_fee":
        line.raw_data["fees"] = "NaN"
    elif failure == "bad_fee_identity":
        line.raw_data["fees"] = "1"
    elif failure == "untrusted_statement":
        db.scalars.side_effect = [
            _scalar_result([line]),
            _scalar_result([]),
            _scalar_result([payment]),
            _scalar_result([]),
        ]
    elif failure == "consumed_bank_line":
        db.scalar.return_value = uuid4()
    with (
        patch.object(
            PaystackCustomerAutoMatchService,
            "_receipt_bank_line",
            side_effect=_SkipCandidate("journal")
            if failure == "wrong_journal"
            else None,
            return_value=SimpleNamespace(line_id=uuid4()),
        ),
        patch(
            "app.services.finance.banking.paystack_customer_auto_match.bank_reconciliation_service.match_statement_line"
        ) as match,
        patch(
            "app.services.finance.banking.paystack_customer_auto_match.CustomerPaymentService.post_payment"
        ) as post_payment,
        patch(
            "app.services.finance.banking.paystack_customer_auto_match.BasePostingAdapter.create_approve_and_post_journal"
        ) as post_journal,
    ):
        result = PaystackCustomerAutoMatchService().run(
            db, org_id, account.bank_account_id, rule.rule_id, actor_id
        )
    assert result.matched == 0
    assert result.failed == 0
    match.assert_not_called()
    post_payment.assert_not_called()
    post_journal.assert_not_called()


def test_imported_gross_receipt_does_not_guess_who_bears_paystack_fee():
    db, org_id, account, rule, actor_id, line, payment = _imported_case()
    payment.amount = payment.gross_amount = Decimal(line.raw_data["gross_amount"])
    with patch(
        "app.services.finance.banking.paystack_customer_auto_match.bank_reconciliation_service.match_statement_line"
    ) as match:
        result = PaystackCustomerAutoMatchService().run(
            db, org_id, account.bank_account_id, rule.rule_id, actor_id
        )
    assert result.matched == 0
    assert result.skipped_imported_fee == 1
    match.assert_not_called()


@pytest.mark.parametrize(
    "reference", ["PSK-CUST-1001-extra", "PSKCUST1001", "psk-cust-1001"]
)
def test_imported_receipt_requires_literal_reference_not_amount_or_normalized_token(
    reference,
):
    db, org_id, account, rule, actor_id, line, payment = _imported_case()
    payment.reference = reference
    with patch(
        "app.services.finance.banking.paystack_customer_auto_match.bank_reconciliation_service.match_statement_line"
    ) as match:
        result = PaystackCustomerAutoMatchService().run(
            db, org_id, account.bank_account_id, rule.rule_id, actor_id
        )
    assert result.matched == 0
    match.assert_not_called()


def test_duplicate_imported_receipts_are_ambiguous_even_on_different_banks():
    db, org_id, account, rule, actor_id, line, payment = _imported_case()
    other = _imported_payment(org_id, uuid4(), line)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([]),
        _scalar_result([payment, other]),
        _scalar_result([line.statement_id]),
    ]
    with patch(
        "app.services.finance.banking.paystack_customer_auto_match.bank_reconciliation_service.match_statement_line"
    ) as match:
        result = PaystackCustomerAutoMatchService().run(
            db, org_id, account.bank_account_id, rule.rule_id, actor_id
        )
    assert result.skipped_ambiguous_reference == 1
    match.assert_not_called()


def test_one_imported_receipt_cannot_match_duplicate_statement_references():
    db, org_id, account, rule, actor_id, line, payment = _imported_case()
    other = _line()
    other.statement_id = line.statement_id
    other.raw_data["paystack_id"] = other.transaction_id
    db.scalars.side_effect = [
        _scalar_result([line, other]),
        _scalar_result([]),
        _scalar_result([payment]),
        _scalar_result([line.statement_id]),
    ]
    with patch(
        "app.services.finance.banking.paystack_customer_auto_match.bank_reconciliation_service.match_statement_line"
    ) as match:
        result = PaystackCustomerAutoMatchService().run(
            db, org_id, account.bank_account_id, rule.rule_id, actor_id
        )
    assert result.skipped_duplicate_receipt == 2
    match.assert_not_called()


@pytest.mark.parametrize("source", ["dotmac_sub_id", "splynx_id", "erpnext_id"])
def test_imported_receipt_sources_and_existing_wht_are_preserved(source):
    db, org_id, account, rule, actor_id, line, payment = _imported_case()
    payment.dotmac_sub_id = None
    setattr(payment, source, "external-source-id")
    payment.wht_amount = Decimal("100")
    payment.gross_amount = payment.amount + payment.wht_amount
    before = dict(payment.__dict__)
    with (
        patch.object(
            PaystackCustomerAutoMatchService,
            "_receipt_bank_line",
            return_value=SimpleNamespace(line_id=uuid4()),
        ),
        patch(
            "app.services.finance.banking.paystack_customer_auto_match.bank_reconciliation_service.match_statement_line"
        ),
    ):
        result = PaystackCustomerAutoMatchService().run(
            db, org_id, account.bank_account_id, rule.rule_id, actor_id
        )
    assert result.matched_imported_receipts == 1
    assert payment.__dict__ == before


def test_different_customers_paying_same_amount_match_by_their_own_reference():
    db, org_id, account, rule, actor_id, line, payment = _imported_case()
    other_line = _line(reference="another-paystack-reference")
    other_line.statement_id = line.statement_id
    other_line.raw_data["paystack_id"] = other_line.transaction_id
    other_payment = _imported_payment(org_id, account.bank_account_id, other_line)
    db.scalars.side_effect = [
        _scalar_result([line, other_line]),
        _scalar_result([]),
        _scalar_result([other_payment, payment]),
        _scalar_result([line.statement_id]),
    ]
    with (
        patch.object(
            PaystackCustomerAutoMatchService,
            "_receipt_bank_line",
            side_effect=[
                SimpleNamespace(line_id=uuid4()),
                SimpleNamespace(line_id=uuid4()),
            ],
        ),
        patch(
            "app.services.finance.banking.paystack_customer_auto_match.bank_reconciliation_service.match_statement_line"
        ) as match,
    ):
        result = PaystackCustomerAutoMatchService().run(
            db, org_id, account.bank_account_id, rule.rule_id, actor_id
        )
    assert result.matched_imported_receipts == 2
    assert [call.kwargs["source_id"] for call in match.call_args_list] == [
        payment.payment_id,
        other_payment.payment_id,
    ]
    assert [call.args[2] for call in match.call_args_list] == [
        line.line_id,
        other_line.line_id,
    ]


def test_manual_duplicate_reference_blocks_imported_receipt():
    db, org_id, account, rule, actor_id, line, payment = _imported_case()
    other_payment = _imported_payment(org_id, account.bank_account_id, line)
    other_payment.dotmac_sub_id = None
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([]),
        _scalar_result([payment, other_payment]),
    ]
    result = PaystackCustomerAutoMatchService().run(
        db, org_id, account.bank_account_id, rule.rule_id, actor_id
    )
    assert result.matched == 0
    assert result.skipped_ambiguous_reference == 1


def test_imported_receipt_respects_configured_date_window():
    db, org_id, account, rule, actor_id, line, payment = _imported_case()
    rule.date_window_days = 1
    payment.payment_date = line.transaction_date - timedelta(days=2)
    result = PaystackCustomerAutoMatchService().run(
        db, org_id, account.bank_account_id, rule.rule_id, actor_id
    )
    assert result.matched == 0
    assert result.skipped_payment == 1


def test_ambiguous_gateway_references_never_fall_back_to_receipts():
    db, org_id, account, rule, actor_id, line, payment = _imported_case()
    intent_a = _intent(org_id, account.bank_account_id, payment.payment_id)
    intent_b = _intent(org_id, account.bank_account_id, payment.payment_id)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([intent_a, intent_b]),
    ]
    result = PaystackCustomerAutoMatchService().run(
        db, org_id, account.bank_account_id, rule.rule_id, actor_id
    )
    assert result.skipped_ambiguous_reference == 1
    assert db.scalars.call_count == 2


def test_imported_receipt_queries_scope_tenant_and_lock_all_duplicate_candidates():
    db, org_id, account, rule, actor_id, line, payment = _imported_case()
    with (
        patch.object(
            PaystackCustomerAutoMatchService,
            "_receipt_bank_line",
            return_value=SimpleNamespace(line_id=uuid4()),
        ),
        patch(
            "app.services.finance.banking.paystack_customer_auto_match.bank_reconciliation_service.match_statement_line"
        ),
    ):
        PaystackCustomerAutoMatchService().run(
            db, org_id, account.bank_account_id, rule.rule_id, actor_id
        )
    receipt_query = db.scalars.call_args_list[2].args[0]
    params = receipt_query.compile().params
    assert params["organization_id_1"] == org_id
    assert params["reference_1"] == [line.reference]
    assert receipt_query._for_update_arg is not None
    # Do not pre-filter away duplicate manual/wrong-bank/pending receipts.
    sql = str(receipt_query)
    assert "customer_payment.bank_account_id =" not in sql
    assert "customer_payment.status =" not in sql
    provider_query = db.scalars.call_args_list[3].args[0].compile().params
    assert provider_query["organization_id_1"] == org_id
    assert provider_query["bank_account_id_1"] == account.bank_account_id
    assert provider_query["currency_code_1"] == "NGN"
    assert provider_query["import_source_1"] == "paystack_collections"


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "wrong_org",
        "wrong_source_id",
        "wrong_source_type",
        "not_posted",
        "wrong_currency",
        "no_bank_line",
        "two_bank_lines",
        "extra_bank_leg",
        "wrong_bank_amount",
        "bank_credit",
    ],
)
def test_imported_receipt_bank_entry_must_be_unique_posted_owned_and_same_currency(
    failure,
):
    _db, org_id, account, _rule, _actor, line, payment = _imported_case()
    journal = SimpleNamespace(
        organization_id=org_id,
        status=JournalStatus.POSTED,
        source_document_type="CUSTOMER_PAYMENT",
        source_document_id=payment.payment_id,
        currency_code="NGN",
    )
    if failure == "wrong_org":
        journal.organization_id = uuid4()
    elif failure == "wrong_source_id":
        journal.source_document_id = uuid4()
    elif failure == "wrong_source_type":
        journal.source_document_type = "OTHER"
    elif failure == "not_posted":
        journal.status = JournalStatus.DRAFT
    elif failure == "wrong_currency":
        journal.currency_code = "USD"
    db = MagicMock()
    db.get.return_value = journal
    bank_line = SimpleNamespace(
        line_id=uuid4(), debit_amount=line.amount, credit_amount=Decimal("0")
    )
    if failure == "wrong_bank_amount":
        bank_line.debit_amount += Decimal("1")
    elif failure == "bank_credit":
        bank_line.credit_amount = Decimal("1")
    extra_bank_line = SimpleNamespace(
        line_id=uuid4(), debit_amount=Decimal("0"), credit_amount=Decimal("15")
    )
    db.scalars.return_value = _scalar_result(
        []
        if failure == "no_bank_line"
        else [bank_line, extra_bank_line]
        if failure == "extra_bank_leg"
        else [bank_line, bank_line]
        if failure == "two_bank_lines"
        else [bank_line]
    )
    if failure is None:
        assert (
            PaystackCustomerAutoMatchService._receipt_bank_line(
                db, org_id, account, payment, line.amount
            )
            is bank_line
        )
        params = db.scalars.call_args.args[0].compile().params
        assert params["journal_entry_id_1"] == payment.journal_entry_id
        assert params["account_id_1"] == account.gl_account_id
        assert params["organization_id_1"] == params["organization_id_2"] == org_id
        assert db.scalars.call_args.args[0]._for_update_arg is not None
    else:
        with pytest.raises(_SkipCandidate, match="journal"):
            PaystackCustomerAutoMatchService._receipt_bank_line(
                db, org_id, account, payment, line.amount
            )


@pytest.fixture
def imported_lookup_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        execution_options={
            "schema_translate_map": {"ar": None, "banking": None, "payments": None}
        },
    )
    for model in (
        BankStatement,
        BankStatementLine,
        BankStatementLineMatch,
        CustomerPayment,
        PaymentIntent,
    ):
        # Preserve global metadata: SQLite cannot parse PostgreSQL UUID defaults.
        removed = [
            (column, column.server_default)
            for column in model.__table__.columns
            if column.server_default is not None
            and "gen_random_uuid" in str(column.server_default.arg)
        ]
        try:
            for column, _default in removed:
                column.server_default = None
            model.__table__.create(engine)
        finally:
            for column, default in removed:
                column.server_default = default
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "foreign_receipt",
        "foreign_statement",
        "wrong_bank_statement",
        "wrong_currency_statement",
        "csv_statement",
        "manual_duplicate",
    ],
)
def test_real_receipt_lookup_requires_tenant_and_provider_evidence(
    imported_lookup_db, failure
):
    _mock_db, org_id, account, rule, actor_id, line, payment = _imported_case()
    header = BankStatement(
        statement_id=line.statement_id,
        organization_id=uuid4() if failure == "foreign_statement" else org_id,
        bank_account_id=uuid4()
        if failure == "wrong_bank_statement"
        else account.bank_account_id,
        currency_code="USD" if failure == "wrong_currency_statement" else "NGN",
        import_source="CSV" if failure == "csv_statement" else "paystack_collections",
        period_start=line.transaction_date,
        period_end=line.transaction_date,
    )
    row = BankStatementLine(
        statement_id=line.statement_id,
        line_number=1,
        transaction_date=line.transaction_date,
        transaction_type=StatementLineType.credit,
        amount=line.amount,
        reference=line.reference,
        transaction_id=line.transaction_id,
        raw_data=line.raw_data,
    )
    receipt = CustomerPayment(
        payment_id=payment.payment_id,
        organization_id=uuid4() if failure == "foreign_receipt" else org_id,
        customer_id=uuid4(),
        payment_number="IMPORTED-ONE",
        payment_date=line.transaction_date,
        payment_method=PaymentMethod.BANK_TRANSFER,
        currency_code="NGN",
        amount=line.amount,
        gross_amount=line.amount,
        functional_currency_amount=line.amount,
        bank_account_id=account.bank_account_id,
        reference=line.reference,
        status=PaymentStatus.CLEARED,
        journal_entry_id=payment.journal_entry_id,
        dotmac_sub_id="source-one",
        created_by_user_id=actor_id,
    )
    imported_lookup_db.add_all([header, row, receipt])
    if failure == "manual_duplicate":
        imported_lookup_db.add(
            CustomerPayment(
                organization_id=org_id,
                customer_id=uuid4(),
                payment_number="MANUAL-DUPLICATE",
                payment_date=line.transaction_date,
                payment_method=PaymentMethod.BANK_TRANSFER,
                currency_code="NGN",
                amount=line.amount,
                gross_amount=line.amount,
                functional_currency_amount=line.amount,
                bank_account_id=account.bank_account_id,
                reference=line.reference,
                status=PaymentStatus.PENDING,
                created_by_user_id=actor_id,
            )
        )
    imported_lookup_db.flush()
    with (
        patch.object(
            PaystackCustomerAutoMatchService,
            "validate_configuration",
            return_value=(account, rule),
        ),
        patch.object(
            PaystackCustomerAutoMatchService,
            "_receipt_bank_line",
            return_value=SimpleNamespace(line_id=uuid4()),
        ),
        patch.object(ReconciliationRuleService, "log_match"),
        patch(
            "app.services.finance.banking.paystack_customer_auto_match.bank_reconciliation_service.match_statement_line"
        ) as match,
    ):
        result = PaystackCustomerAutoMatchService().run(
            imported_lookup_db, org_id, account.bank_account_id, rule.rule_id, actor_id
        )
    assert result.failed == 0
    assert result.matched_imported_receipts == (1 if failure is None else 0)
    assert match.call_count == (1 if failure is None else 0)
