"""Do not mutate accounting when bank prerequisites are absent or incompatible."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    StatementLineType,
)
from app.services.dotmac_sub.client import PaymentRecord
from app.services.dotmac_sub.sync._bank_mapping import (
    BankMappingMixin,
    MissingPaymentBankMappingError,
    PostedPaymentBankCorrectionRequiredError,
)
from app.services.dotmac_sub.sync._payments import PaymentSyncMixin
from app.services.dotmac_sub.sync._types import SyncResult


def source(amount="100"):
    return PaymentRecord(
        id="synthetic-payment",
        account_id="synthetic-account",
        billing_account_id=None,
        amount=Decimal(amount),
        currency="NGN",
        status="succeeded",
        payment_channel_id="synthetic-channel",
    )


def harness(changed=True):
    service = PaymentSyncMixin()
    service.organization_id = uuid4()
    service.db = MagicMock()
    service.db.get.return_value = SimpleNamespace(currency_code="NGN")
    service._compute_hash = MagicMock(return_value="hash")
    service._has_changed = MagicMock(return_value=changed)
    service._get_customer_for_account = MagicMock(return_value=uuid4())
    service._functional_amount = MagicMock(return_value=(Decimal("1"), Decimal("100")))
    service._get_bank_account_for_channel = MagicMock(return_value=None)
    service._find_local_payment = MagicMock()
    service._reverse_posted_payment_gl = MagicMock()
    service._apply_allocations = MagicMock()
    service._record_sync = MagicMock()
    service._ensure_synced_payment_posted = MagicMock()
    service._ensure_wht_terminal_consequence = MagicMock()
    return service


def test_missing_bank_fails_before_receipt_creation_or_reversal():
    service = harness()
    result = SyncResult(success=True, entity_type="payments")
    with pytest.raises(MissingPaymentBankMappingError) as error:
        service._sync_single_payment(source(), result, None, False)
    assert error.value.currency_code == "NGN"
    assert error.value.channel_id == "synthetic-channel"
    assert "configure the mapping" in str(error.value)
    service._find_local_payment.assert_not_called()
    service.db.add.assert_not_called()
    service._reverse_posted_payment_gl.assert_not_called()
    service._apply_allocations.assert_not_called()
    service._record_sync.assert_not_called()
    assert result.created == result.updated == 0


@pytest.mark.parametrize("existing_bank", [None, "wrong-bank"])
def test_unchanged_unposted_receipt_can_use_repaired_mapping(existing_bank):
    service = harness(changed=False)
    local = SimpleNamespace(
        journal_entry_id=None,
        gross_amount=Decimal("100"),
        bank_account_id=existing_bank,
        currency_code="NGN",
    )
    bank_id = uuid4()
    service._find_local_payment.return_value = local
    service._get_bank_account_for_channel.return_value = bank_id
    result = SyncResult(success=True, entity_type="payments")
    service._sync_single_payment(source(), result, None, True)
    assert local.bank_account_id == bank_id
    service._ensure_synced_payment_posted.assert_called_once_with(local, None)
    assert result.skipped == 1


def test_unchanged_posted_receipt_is_not_remapped():
    service = harness(changed=False)
    old_bank = uuid4()
    local = SimpleNamespace(journal_entry_id=uuid4(), bank_account_id=old_bank)
    service._find_local_payment.return_value = local
    service._sync_single_payment(
        source(), SyncResult(success=True, entity_type="payments"), None, True
    )
    assert local.bank_account_id == old_bank
    service._get_bank_account_for_channel.assert_not_called()


def test_unchanged_missing_mapping_does_not_claim_posting_success():
    service = harness(changed=False)
    local = SimpleNamespace(
        journal_entry_id=None,
        gross_amount=Decimal("100"),
        bank_account_id=None,
        currency_code="NGN",
    )
    service._find_local_payment.return_value = local
    with pytest.raises(MissingPaymentBankMappingError):
        service._sync_single_payment(
            source(), SyncResult(success=True, entity_type="payments"), None, True
        )
    assert local.bank_account_id is None
    service._ensure_synced_payment_posted.assert_not_called()


def test_zero_value_receipt_does_not_require_bank_mapping():
    service = harness(changed=False)
    local = SimpleNamespace(
        journal_entry_id=None, gross_amount=Decimal("0"), bank_account_id=None
    )
    service._find_local_payment.return_value = local
    service._sync_single_payment(
        source("0"), SyncResult(success=True, entity_type="payments"), None, True
    )
    service._get_bank_account_for_channel.assert_not_called()


def mapping():
    service = BankMappingMixin()
    service.organization_id = uuid4()
    service.db = MagicMock()
    service._bank_account_mapping = {}
    service._default_bank_account_cache = {}
    service._payment_channel_names = {"channel": "Example Bank"}
    service._bank_name_mapping = {"example": "Example"}
    return service


def test_channel_cache_is_currency_specific():
    service = mapping()
    ngn, usd = uuid4(), uuid4()
    service._match_bank_account = MagicMock(side_effect=[ngn, usd])
    assert service._get_bank_account_for_channel("channel", "NGN") == ngn
    assert service._get_bank_account_for_channel("channel", "USD") == usd
    assert service._get_bank_account_for_channel("channel", "NGN") == ngn
    assert service._match_bank_account.call_count == 2


def test_explicit_unavailable_bank_does_not_fall_back_to_another_bank():
    service = mapping()
    service._match_bank_account = MagicMock(return_value=None)
    service._get_default_bank_account = MagicMock(return_value=uuid4())
    assert service._get_bank_account_for_channel("channel", "NGN") is None
    service._get_default_bank_account.assert_not_called()


@pytest.mark.parametrize("method", ["_match_bank_account", "_get_default_bank_account"])
def test_mapping_queries_scope_currency_org_and_postable_gl(method):
    service = mapping()
    service.db.scalars.return_value.all.return_value = []
    if method == "_match_bank_account":
        service._match_bank_account("Example", "USD")
        statement = service.db.scalars.call_args.args[0]
    else:
        service._get_default_bank_account("USD")
        statement = service.db.scalar.call_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "bank_accounts.currency_code = 'USD'" in sql
    assert "bank_accounts.organization_id" in sql
    assert "account.organization_id" in sql
    assert "account.is_active IS true" in sql
    assert "account.is_posting_allowed IS true" in sql


@pytest.mark.parametrize("channel", [None, "unknown-channel", "channel"])
def test_missing_or_unmapped_channel_never_chooses_first_bank(channel):
    service = mapping()
    service._bank_name_mapping = {}
    service._get_default_bank_account = MagicMock(return_value=uuid4())
    assert service._get_bank_account_for_channel(channel, "NGN") is None
    service._get_default_bank_account.assert_not_called()


@pytest.mark.parametrize("name", ["Paystack", "Pay Stack"])
def test_paystack_channel_uses_selected_collection_account_not_opex(name):
    service = mapping()
    service._payment_channel_names = {"channel": name}
    service._bank_name_mapping = {"paystack": "paystack", "pay stack": "paystack"}
    collection_id = uuid4()
    service._get_paystack_collection_bank = MagicMock(return_value=collection_id)
    service._match_bank_account = MagicMock(return_value=uuid4())
    assert service._get_bank_account_for_channel("channel", "NGN") == collection_id
    service._get_paystack_collection_bank.assert_called_once_with("NGN")
    service._match_bank_account.assert_not_called()


def test_missing_paystack_configuration_never_uses_opex_or_default_bank():
    service = mapping()
    service._payment_channel_names = {"channel": "Paystack"}
    service._bank_name_mapping = {"paystack": "paystack"}
    service._get_paystack_collection_bank = MagicMock(return_value=None)
    service._get_default_bank_account = MagicMock(return_value=uuid4())
    service._match_bank_account = MagicMock(return_value=uuid4())
    assert service._get_bank_account_for_channel("channel", "NGN") is None
    service._match_bank_account.assert_not_called()
    service._get_default_bank_account.assert_not_called()


@pytest.mark.parametrize("configured", [None, "", "invalid-uuid"])
def test_invalid_collection_configuration_is_quarantined(configured):
    service = mapping()
    with patch(
        "app.services.dotmac_sub.sync._bank_mapping.resolve_value",
        return_value=configured,
    ):
        assert service._get_paystack_collection_bank("NGN") is None
    service.db.scalar.assert_not_called()


def test_collection_configuration_requires_same_org_currency_and_postable_gl():
    service = mapping()
    bank_id = uuid4()
    service.db.scalar.return_value = bank_id
    with patch(
        "app.services.dotmac_sub.sync._bank_mapping.resolve_value",
        return_value=str(bank_id),
    ) as resolve:
        assert service._get_paystack_collection_bank("NGN") == bank_id
    assert resolve.call_args.kwargs["organization_id"] == service.organization_id
    sql = str(
        service.db.scalar.call_args.args[0].compile(
            compile_kwargs={"literal_binds": True}
        )
    )
    for guard in (
        "bank_accounts.bank_account_id",
        "bank_accounts.organization_id",
        "bank_accounts.currency_code = 'NGN'",
        "bank_accounts.status = 'active'",
        "account.organization_id",
        "account.is_active IS true",
        "account.is_posting_allowed IS true",
    ):
        assert guard in sql


@pytest.mark.parametrize("bank_count", [0, 1, 2])
def test_bank_name_mapping_requires_one_account(bank_count):
    service = mapping()
    banks = [
        SimpleNamespace(
            bank_account_id=uuid4(), account_name="Example", bank_name="Example"
        )
        for _ in range(bank_count)
    ]
    service.db.scalars.return_value.all.return_value = banks
    expected = banks[0].bank_account_id if bank_count == 1 else None
    assert service._match_bank_account("Example", "NGN") == expected


def legacy_mapping(amount="38000"):
    service = mapping()
    bank_id = uuid4()
    service._get_paystack_collection_bank = MagicMock(return_value=bank_id)
    pay = source(amount)
    pay = replace(pay, payment_channel_id=None, external_id="unique-reference")
    line = SimpleNamespace(
        amount=Decimal("38000"),
        transaction_id="10001",
        raw_data={"paystack_id": 10001, "gross_amount": "38680.21", "fees": "680.21"},
    )
    service.db.scalars.return_value.all.return_value = [line]
    return service, bank_id, pay, line


@pytest.mark.parametrize("amount", ["38000", "38680.21"])
def test_missing_channel_resolves_only_exact_provider_statement_reference(amount):
    service, bank_id, pay, _line = legacy_mapping(amount)
    assert service._get_bank_account_for_channel(None, "NGN", payment=pay) == bank_id
    query = service.db.scalars.call_args.args[0].compile()
    assert query.params["reference_1"] == "unique-reference"
    assert query.params["organization_id_1"] == service.organization_id
    assert query.params["bank_account_id_1"] == bank_id
    assert query.params["currency_code_1"] == "NGN"
    assert query.params["import_source_1"] == "paystack_collections"
    assert query.params["param_1"] == 2
    service.db.add.assert_not_called()
    service.db.commit.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        "missing_reference",
        "no_statement",
        "duplicate_reference",
        "wrong_amount",
        "wrong_currency",
        "missing_fee",
        "inexact_fee",
        "negative_fee",
        "nonfinite_fee",
        "missing_provider_id",
        "wrong_provider_id",
    ],
)
def test_legacy_statement_evidence_fails_closed(failure):
    service, _bank_id, pay, line = legacy_mapping()
    if failure == "missing_reference":
        pay = replace(pay, external_id=None)
    elif failure == "no_statement":
        service.db.scalars.return_value.all.return_value = []
    elif failure == "duplicate_reference":
        service.db.scalars.return_value.all.return_value = [line, line]
    elif failure == "wrong_amount":
        pay = replace(pay, amount=Decimal("38001"))
    elif failure == "wrong_currency":
        pay = replace(pay, currency="USD")
    elif failure == "missing_fee":
        line.raw_data.pop("fees")
    elif failure == "inexact_fee":
        line.raw_data["fees"] = "1"
    elif failure == "negative_fee":
        line.raw_data["fees"] = "-1"
    elif failure == "nonfinite_fee":
        line.raw_data["fees"] = "NaN"
    elif failure == "missing_provider_id":
        line.raw_data.pop("paystack_id")
    elif failure == "wrong_provider_id":
        line.raw_data["paystack_id"] = 10002
    assert service._get_bank_account_for_channel(None, "NGN", payment=pay) is None
    service.db.add.assert_not_called()
    service.db.commit.assert_not_called()


def test_corrected_mapping_does_not_rewrite_already_posted_receipt():
    service = harness()
    old_bank_id, new_bank_id = uuid4(), uuid4()
    local = SimpleNamespace(journal_entry_id=uuid4(), bank_account_id=old_bank_id)
    service._get_bank_account_for_channel.return_value = new_bank_id
    service._find_local_payment.return_value = local
    service._channel_name = MagicMock(return_value="Paystack")
    result = SyncResult(success=True, entity_type="payments")
    with pytest.raises(PostedPaymentBankCorrectionRequiredError):
        service._sync_single_payment(source(), result, None, False)
    assert local.bank_account_id == old_bank_id
    service._reverse_posted_payment_gl.assert_not_called()
    service._ensure_synced_payment_posted.assert_not_called()
    service._apply_allocations.assert_not_called()
    service._record_sync.assert_not_called()
    assert result.created == result.updated == 0


@pytest.fixture
def statement_db():
    """Execute the routing query against actual rows, without a live database."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        execution_options={"schema_translate_map": {"banking": None}},
    )
    BankStatement.__table__.create(engine)
    BankStatementLine.__table__.create(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def add_statement_evidence(db, source_org, source_bank, **overrides):
    header = {
        "organization_id": source_org,
        "bank_account_id": source_bank,
        "currency_code": "NGN",
        "import_source": "paystack_collections",
        "period_start": date(2026, 9, 1),
        "period_end": date(2026, 9, 30),
    }
    line = {
        "line_number": 1,
        "transaction_date": date(2026, 9, 6),
        "transaction_type": StatementLineType.credit,
        "reference": "unique-reference",
        "transaction_id": "10001",
        "amount": Decimal("38000"),
        "raw_data": {
            "paystack_id": 10001,
            "gross_amount": "38680.21",
            "fees": "680.21",
        },
    }
    for name, value in overrides.items():
        (header if name in header else line)[name] = value
    statement = BankStatement(**header)
    db.add(statement)
    db.flush()
    db.add(BankStatementLine(statement_id=statement.statement_id, **line))
    db.flush()


@pytest.mark.parametrize(
    "failure",
    ["none", "organization", "bank", "currency", "provenance", "debit", "reference"],
)
def test_real_statement_query_rejects_unrelated_evidence(statement_db, failure):
    service, bank_id, pay, _line = legacy_mapping()
    service.db = statement_db
    overrides = {
        "none": {},
        "organization": {"organization_id": uuid4()},
        "bank": {"bank_account_id": uuid4()},
        "currency": {"currency_code": "USD"},
        "provenance": {"import_source": "CSV"},
        "debit": {"transaction_type": StatementLineType.debit},
        "reference": {"reference": "different-customer-same-amount"},
    }[failure]
    add_statement_evidence(statement_db, service.organization_id, bank_id, **overrides)
    expected = bank_id if failure == "none" else None
    assert service._get_bank_account_for_channel(None, "NGN", payment=pay) == expected


def test_same_amounts_do_not_confuse_reference_routing(statement_db):
    service, bank_id, pay, _line = legacy_mapping()
    service.db = statement_db
    add_statement_evidence(statement_db, service.organization_id, bank_id)
    add_statement_evidence(
        statement_db,
        service.organization_id,
        bank_id,
        reference="another-customer",
        transaction_id="10002",
        raw_data={"paystack_id": 10002, "gross_amount": "38680.21", "fees": "680.21"},
    )
    assert service._get_bank_account_for_channel(None, "NGN", payment=pay) == bank_id


def test_real_duplicate_reference_is_quarantined(statement_db):
    service, bank_id, pay, _line = legacy_mapping()
    service.db = statement_db
    add_statement_evidence(statement_db, service.organization_id, bank_id)
    add_statement_evidence(
        statement_db,
        service.organization_id,
        bank_id,
        transaction_id="10002",
        raw_data={"paystack_id": 10002, "gross_amount": "38680.21", "fees": "680.21"},
    )
    assert service._get_bank_account_for_channel(None, "NGN", payment=pay) is None
