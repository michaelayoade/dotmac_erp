"""Do not mutate accounting when bank prerequisites are absent or incompatible."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.dotmac_sub.client import PaymentRecord
from app.services.dotmac_sub.sync._bank_mapping import (
    BankMappingMixin,
    MissingPaymentBankMappingError,
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


def test_unchanged_unposted_receipt_can_use_repaired_mapping():
    service = harness(changed=False)
    local = SimpleNamespace(
        journal_entry_id=None,
        gross_amount=Decimal("100"),
        bank_account_id=None,
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
