"""Tests for deterministic AP invoice statement matching."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.finance.ap.supplier_payment import APPaymentStatus
from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.banking.bank_statement import StatementLineType
from app.models.finance.banking.reconciliation_match_rule import (
    ReconciliationMatchRule,
)
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.services.finance.banking.ap_invoice_auto_match import (
    APInvoiceAutoMatchError,
    APInvoiceAutoMatchService,
    _normalize_reference,
    _statement_reference_keys,
    _SkipCandidate,
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
        is_matched=False,
    )


def _invoice(org_id, *, reference: str, amount: str = "250.00"):
    return SimpleNamespace(
        invoice_id=uuid4(),
        organization_id=org_id,
        supplier_id=uuid4(),
        invoice_number=reference,
        status=SupplierInvoiceStatus.POSTED,
        supplier_invoice_number=None,
        balance_due=Decimal(amount),
        currency_code="NGN",
        withholding_tax_amount=Decimal("0"),
        exchange_rate=Decimal("1"),
        journal_entry_id=uuid4(),
    )


def _get_side_effect(account, rule, journal=None):
    def get(model, _id):
        if model is BankAccount:
            return account if _id == account.bank_account_id else None
        if model is ReconciliationMatchRule:
            return rule
        if model is JournalEntry:
            return journal
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
        bank_account_id=account_id,
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


def _posted_payment(org_id, account, line, *, legacy=False):
    return SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        bank_account_id=account.gl_account_id if legacy else account.bank_account_id,
        payment_date=line.transaction_date,
        amount=line.amount,
        currency_code="NGN",
        correlation_id=None,
        status=APPaymentStatus.SENT,
        journal_entry_id=uuid4(),
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_paid_invoice_reuses_posted_payment_without_reposting(legacy) -> None:
    org_id, account_id, actor_id = uuid4(), uuid4(), uuid4()
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id)
    line = _line(reference="INV-PAID-1001")
    invoice = _invoice(org_id, reference=line.reference)
    invoice.status = SupplierInvoiceStatus.PAID
    invoice.balance_due = Decimal("0")
    payment = _posted_payment(org_id, account, line, legacy=legacy)
    journal = SimpleNamespace(
        organization_id=org_id,
        status=JournalStatus.POSTED,
        source_document_type="SUPPLIER_PAYMENT",
        source_document_id=payment.payment_id,
        currency_code="NGN",
    )
    journal_line = SimpleNamespace(line_id=uuid4())
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule, journal)
    returns = [[line], [invoice], [payment]]
    if legacy:
        returns.append([account_id])
    returns.append([journal_line])
    db.scalars.side_effect = [_scalar_result(values) for values in returns]
    db.scalar.return_value = None

    def confirm(*_args, **_kwargs):
        line.is_matched = True
        return line

    with (
        patch(
            "app.services.finance.banking.ap_invoice_auto_match."
            "bank_reconciliation_service.match_statement_line",
            side_effect=confirm,
        ),
        patch(
            "app.services.finance.banking.ap_invoice_auto_match."
            "SupplierPaymentService.mark_cleared"
        ),
        patch(
            "app.services.finance.banking.ap_invoice_auto_match."
            "SupplierPaymentService.create_payment"
        ) as create,
        patch(
            "app.services.finance.banking.ap_invoice_auto_match."
            "SupplierPaymentService.approve_payment"
        ) as approve,
        patch(
            "app.services.finance.banking.ap_invoice_auto_match."
            "SupplierPaymentService.post_payment"
        ) as post,
    ):
        result = APInvoiceAutoMatchService().run(
            db, org_id, account_id, rule.rule_id, actor_id
        )

    assert result.matched == result.payments_reused == 1
    assert result.payments_created == result.failed == 0
    assert result.bank_accounts_corrected == int(legacy)
    assert payment.bank_account_id == account_id
    assert invoice.balance_due == Decimal("0")
    assert invoice.status == SupplierInvoiceStatus.PAID
    db.refresh.assert_called_once_with(invoice, with_for_update=True)
    create.assert_not_called()
    approve.assert_not_called()
    post.assert_not_called()
    query = db.scalars.call_args_list[1].args[0].compile()
    assert SupplierInvoiceStatus.PAID in query.params["status_1"]


@pytest.mark.parametrize("failure", ["journal_currency", "consumed", "unconfirmed"])
def test_legacy_bank_mapping_is_not_corrected_without_confirmed_match(failure) -> None:
    org_id, account_id = uuid4(), uuid4()
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id)
    line = _line(reference="INV-PAID-1001")
    invoice = _invoice(org_id, reference=line.reference)
    invoice.status = SupplierInvoiceStatus.PAID
    invoice.balance_due = Decimal("0")
    payment = _posted_payment(org_id, account, line, legacy=True)
    journal = SimpleNamespace(
        organization_id=org_id,
        status=JournalStatus.POSTED,
        source_document_type="SUPPLIER_PAYMENT",
        source_document_id=payment.payment_id,
        currency_code="USD" if failure == "journal_currency" else "NGN",
    )
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule, journal)
    db.scalars.side_effect = [
        _scalar_result([line]),
        _scalar_result([invoice]),
        _scalar_result([payment]),
        _scalar_result([account_id]),
        _scalar_result([SimpleNamespace(line_id=uuid4())]),
    ]
    db.scalar.return_value = uuid4() if failure == "consumed" else None
    with patch(
        "app.services.finance.banking.ap_invoice_auto_match."
        "bank_reconciliation_service.match_statement_line",
        return_value=line,
    ):
        result = APInvoiceAutoMatchService().run(
            db, org_id, account_id, rule.rule_id, uuid4()
        )
    assert result.matched == result.bank_accounts_corrected == 0
    assert result.failed == 0
    assert payment.bank_account_id == account.gl_account_id


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "wrong_bank",
        "wrong_amount",
        "wrong_date",
        "wrong_currency",
        "cross_org",
        "void",
        "unposted",
        "ambiguous",
    ],
)
def test_paid_invoice_never_creates_replacement_payment(case) -> None:
    org_id = uuid4()
    account = _account(org_id, uuid4(), uuid4())
    line = _line(reference="INV-PAID-1001")
    invoice = _invoice(org_id, reference=line.reference)
    invoice.status = SupplierInvoiceStatus.PAID
    invoice.balance_due = Decimal("0")
    payment = _posted_payment(org_id, account, line)
    values = [payment]
    if case == "missing":
        values = []
    elif case == "wrong_bank":
        payment.bank_account_id = uuid4()
    elif case == "wrong_amount":
        payment.amount += Decimal("1")
    elif case == "wrong_date":
        payment.payment_date = date(2026, 9, 14)
    elif case == "wrong_currency":
        payment.currency_code = "USD"
    elif case == "cross_org":
        payment.organization_id = uuid4()
    elif case == "void":
        payment.status = APPaymentStatus.VOID
    elif case == "unposted":
        payment.journal_entry_id = None
    elif case == "ambiguous":
        values.append(_posted_payment(org_id, account, line))
    db = MagicMock()
    db.scalars.return_value = _scalar_result(values)
    with patch(
        "app.services.finance.banking.ap_invoice_auto_match."
        "SupplierPaymentService.create_payment"
    ) as create:
        with pytest.raises(_SkipCandidate, match="payment"):
            APInvoiceAutoMatchService._resolve_payment(
                db, org_id, account, line, invoice, uuid4()
            )
    create.assert_not_called()


@pytest.mark.parametrize("change", ["currency", "unposted"])
def test_invoice_is_revalidated_under_lock_before_creating_payment(change) -> None:
    org_id = uuid4()
    account = _account(org_id, uuid4(), uuid4())
    line = _line(reference="INV-1001")
    invoice = _invoice(org_id, reference=line.reference)
    db = MagicMock()

    def changed(*_args, **_kwargs):
        if change == "currency":
            invoice.currency_code = "USD"
        else:
            invoice.journal_entry_id = None

    db.refresh.side_effect = changed
    with patch(
        "app.services.finance.banking.ap_invoice_auto_match."
        "SupplierPaymentService.create_payment"
    ) as create:
        with pytest.raises(_SkipCandidate, match="currency|journal"):
            APInvoiceAutoMatchService._resolve_payment(
                db, org_id, account, line, invoice, uuid4()
            )
    create.assert_not_called()
    db.scalars.assert_not_called()


@pytest.mark.parametrize("bank_count", [0, 1, 2])
def test_legacy_gl_mapping_requires_one_tenant_bank(bank_count) -> None:
    org_id = uuid4()
    account = _account(org_id, uuid4(), uuid4())
    db = MagicMock()
    db.get.return_value = None
    ids = [account.bank_account_id, uuid4()][:bank_count]
    db.scalars.return_value = _scalar_result(ids)
    assert APInvoiceAutoMatchService._legacy_bank_reference_allowed(
        db, org_id, account
    ) is (bank_count == 1)
    query = db.scalars.call_args.args[0].compile()
    assert query.params["organization_id_1"] == org_id
    assert query.params["gl_account_id_1"] == account.gl_account_id


def test_legacy_gl_mapping_does_not_reinterpret_physical_bank_id() -> None:
    org_id = uuid4()
    account = _account(org_id, uuid4(), uuid4())
    db = MagicMock()
    db.get.return_value = _account(org_id, account.gl_account_id, uuid4())
    assert not APInvoiceAutoMatchService._legacy_bank_reference_allowed(
        db, org_id, account
    )
    db.scalars.assert_not_called()


def test_legacy_gl_mapping_rejects_cross_tenant_account() -> None:
    db = MagicMock()
    assert not APInvoiceAutoMatchService._legacy_bank_reference_allowed(
        db, uuid4(), _account(uuid4(), uuid4(), uuid4())
    )
    db.get.assert_not_called()


def test_concurrently_paid_invoice_cannot_create_duplicate_payment() -> None:
    org_id = uuid4()
    account = _account(org_id, uuid4(), uuid4())
    line = _line(reference="INV-1001")
    invoice = _invoice(org_id, reference=line.reference)
    db = MagicMock()
    db.scalars.return_value = _scalar_result([])

    def settled(*_args, **_kwargs):
        invoice.status = SupplierInvoiceStatus.PAID
        invoice.balance_due = Decimal("0")

    db.refresh.side_effect = settled
    with patch(
        "app.services.finance.banking.ap_invoice_auto_match."
        "SupplierPaymentService.create_payment"
    ) as create:
        with pytest.raises(_SkipCandidate, match="payment"):
            APInvoiceAutoMatchService._resolve_payment(
                db, org_id, account, line, invoice, uuid4()
            )
    create.assert_not_called()


def test_duplicate_paid_invoice_statement_payment_is_not_arbitrarily_chosen() -> None:
    org_id, account_id = uuid4(), uuid4()
    account = _account(org_id, account_id, uuid4())
    rule = _rule(org_id, account_id)
    invoice = _invoice(org_id, reference="INV-PAID-1001")
    invoice.status = SupplierInvoiceStatus.PAID
    invoice.balance_due = Decimal("0")
    db = MagicMock()
    db.get.side_effect = _get_side_effect(account, rule)
    db.scalars.side_effect = [
        _scalar_result(
            [
                _line(reference=invoice.invoice_number),
                _line(reference=invoice.invoice_number),
            ]
        ),
        _scalar_result([invoice]),
    ]
    with patch.object(APInvoiceAutoMatchService, "_resolve_payment") as resolve:
        result = APInvoiceAutoMatchService().run(
            db, org_id, account_id, rule.rule_id, uuid4()
        )
    assert result.skipped_duplicate_invoice == 2
    assert result.matched == 0
    resolve.assert_not_called()


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
