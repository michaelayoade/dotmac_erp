"""Tests for deterministic AP invoice statement matching."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.finance.ap.supplier_payment import APPaymentStatus
from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.banking.bank_statement import StatementLineType
from app.models.finance.banking.reconciliation_match_rule import (
    ReconciliationMatchRule,
)
from app.services.finance.banking.ap_invoice_auto_match import (
    APInvoiceAutoMatchError,
    APInvoiceAutoMatchService,
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


def _rule(org_id, account_id, *, is_active: bool = True):
    return SimpleNamespace(
        rule_id=uuid4(),
        organization_id=org_id,
        bank_account_id=account_id,
        is_active=is_active,
        source_doc_type="SUPPLIER_PAYMENT",
        action_type="MATCH",
        match_debit=True,
        match_credit=False,
        conditions=[],
        match_count=0,
        last_matched_at=None,
    )


def _line(*, reference: str, amount: str = "250.00"):
    return SimpleNamespace(
        line_id=uuid4(),
        line_number=1,
        reference=reference,
        bank_reference=None,
        transaction_id=None,
        description=f"Supplier invoice {reference}",
        payee_payer=None,
        bank_category=None,
        bank_code=None,
        amount=Decimal(amount),
        transaction_date=date(2026, 9, 15),
        transaction_type=StatementLineType.debit,
    )


def _invoice(org_id, *, reference: str, amount: str = "250.00"):
    return SimpleNamespace(
        invoice_id=uuid4(),
        organization_id=org_id,
        supplier_id=uuid4(),
        invoice_number=reference,
        supplier_invoice_number=None,
        balance_due=Decimal(amount),
        currency_code="NGN",
        withholding_tax_amount=Decimal("0"),
        exchange_rate=Decimal("1"),
        journal_entry_id=uuid4(),
    )


def _get_side_effect(account, rule):
    def get(model, _id):
        if model is BankAccount:
            return account
        if model is ReconciliationMatchRule:
            return rule
        return None

    return get


def test_invoice_reference_matching_uses_complete_normalized_tokens() -> None:
    line = _line(reference="NIP transfer")
    line.description = "Payment for supplier invoice SINV-2026/0042"

    assert _normalize_reference("SINV-2026/0042") == "sinv20260042"
    assert "sinv20260042" in _statement_reference_keys(line)
    assert "supplier" not in _statement_reference_keys(line)


def test_disabled_ap_rule_is_rejected() -> None:
    org_id = uuid4()
    account_id = uuid4()
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id, is_active=False)
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)

    with pytest.raises(APInvoiceAutoMatchError, match="rule is disabled"):
        APInvoiceAutoMatchService.validate_configuration(
            db, org_id, account_id, rule.rule_id
        )


def test_exact_invoice_creates_or_reuses_payment_then_matches() -> None:
    org_id = uuid4()
    account_id = uuid4()
    actor_id = uuid4()
    line = _line(reference="SINV-2026-0042")
    invoice = _invoice(org_id, reference="SINV-2026-0042")
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id)
    payment = SimpleNamespace(
        payment_id=uuid4(),
        status=APPaymentStatus.SENT,
    )
    journal_line = SimpleNamespace(line_id=uuid4())

    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([invoice]),
    ]
    db.scalar.return_value = None

    with (
        patch.object(
            APInvoiceAutoMatchService,
            "_resolve_payment",
            return_value=(payment, True),
        ) as resolve_payment,
        patch.object(
            APInvoiceAutoMatchService,
            "_bank_journal_line",
            return_value=journal_line,
        ),
        patch(
            "app.services.finance.banking.ap_invoice_auto_match."
            "bank_reconciliation_service.match_statement_line"
        ) as match_line,
        patch(
            "app.services.finance.banking.ap_invoice_auto_match."
            "SupplierPaymentService.mark_cleared"
        ) as mark_cleared,
    ):
        result = APInvoiceAutoMatchService().run(
            db,
            org_id,
            account_id,
            rule.rule_id,
            actor_id,
        )

    assert result.scanned == 1
    assert result.matched == 1
    assert result.payments_created == 1
    resolve_payment.assert_called_once_with(
        db,
        org_id,
        account,
        line,
        invoice,
        actor_id,
    )
    match_line.assert_called_once_with(
        db,
        org_id,
        line.line_id,
        journal_line.line_id,
        matched_by=actor_id,
        force_match=False,
        source_type="SUPPLIER_PAYMENT",
        source_id=payment.payment_id,
        match_state="confirmed",
    )
    mark_cleared.assert_called_once_with(
        db,
        org_id,
        payment.payment_id,
        line.transaction_date,
    )


def test_amount_mismatch_does_not_create_payment() -> None:
    org_id = uuid4()
    account_id = uuid4()
    line = _line(reference="INV-1001", amount="249.00")
    invoice = _invoice(org_id, reference="INV-1001", amount="250.00")
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id)
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([invoice]),
    ]

    with patch.object(APInvoiceAutoMatchService, "_resolve_payment") as resolve:
        result = APInvoiceAutoMatchService().run(
            db, org_id, account_id, rule.rule_id, uuid4()
        )

    assert result.matched == 0
    assert result.skipped_amount == 1
    resolve.assert_not_called()


def test_invoice_with_planned_wht_is_left_for_manual_processing() -> None:
    org_id = uuid4()
    account_id = uuid4()
    line = _line(reference="INV-WHT-1001")
    invoice = _invoice(org_id, reference="INV-WHT-1001")
    invoice.withholding_tax_amount = Decimal("12.50")
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id)
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([invoice]),
    ]

    with patch.object(APInvoiceAutoMatchService, "_resolve_payment") as resolve:
        result = APInvoiceAutoMatchService().run(
            db, org_id, account_id, rule.rule_id, uuid4()
        )

    assert result.matched == 0
    assert result.skipped_withholding_tax == 1
    resolve.assert_not_called()


def test_payment_creation_uses_existing_ap_approval_and_posting_services() -> None:
    org_id = uuid4()
    actor_id = uuid4()
    account = _account(org_id, uuid4(), uuid4())
    line = _line(reference="INV-2002")
    invoice = _invoice(org_id, reference="INV-2002")
    payment = SimpleNamespace(
        payment_id=uuid4(),
        bank_account_id=account.bank_account_id,
        payment_date=line.transaction_date,
        amount=line.amount,
        currency_code="NGN",
        correlation_id=f"bank-ap:{invoice.invoice_id}:{line.line_id}",
        status=APPaymentStatus.DRAFT,
        journal_entry_id=None,
    )
    db = MagicMock()
    db.scalars.return_value = _scalar_result([])

    def approve(*_args, **_kwargs):
        payment.status = APPaymentStatus.APPROVED
        return payment

    def post(*_args, **_kwargs):
        payment.status = APPaymentStatus.SENT
        payment.journal_entry_id = uuid4()
        return payment

    with (
        patch(
            "app.services.finance.banking.ap_invoice_auto_match."
            "SupplierPaymentService.create_payment",
            return_value=payment,
        ) as create_payment,
        patch(
            "app.services.finance.banking.ap_invoice_auto_match."
            "SupplierPaymentService.approve_payment",
            side_effect=approve,
        ) as approve_payment,
        patch(
            "app.services.finance.banking.ap_invoice_auto_match."
            "SupplierPaymentService.post_payment",
            side_effect=post,
        ) as post_payment,
    ):
        resolved, created = APInvoiceAutoMatchService._resolve_payment(
            db,
            org_id,
            account,
            line,
            invoice,
            actor_id,
        )

    assert resolved is payment
    assert created is True
    payment_input = create_payment.call_args.args[2]
    assert payment_input.bank_account_id == account.bank_account_id
    assert payment_input.amount == invoice.balance_due
    assert payment_input.allocations[0].invoice_id == invoice.invoice_id
    assert payment_input.allocations[0].amount == invoice.balance_due
    assert payment_input.correlation_id == (
        f"bank-ap:{invoice.invoice_id}:{line.line_id}"
    )
    approve_payment.assert_called_once_with(
        db,
        org_id,
        payment.payment_id,
        actor_id,
    )
    post_payment.assert_called_once_with(
        db,
        org_id,
        payment.payment_id,
        actor_id,
        posting_date=line.transaction_date,
    )
