from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.finance.banking import BankStatement, BankStatementLine
from app.services.finance.banking.bank_account import BankAccountService
from app.services.finance.banking.mono_client import (
    MonoAccountInfo,
    MonoClient,
    MonoConfig,
    MonoDataRefreshResult,
    MonoError,
    MonoExchangeResult,
    MonoTransaction,
)
from app.services.finance.banking.mono_sync import (
    MonoSyncResult,
    MonoSyncService,
)


def _account(**overrides):
    values = {
        "bank_account_id": uuid4(),
        "organization_id": uuid4(),
        "mono_account_id": "mono-account-1",
        "mono_sync_from_date": date(2026, 3, 1),
        "mono_last_transaction_date": None,
        "mono_sync_buffer_days": 7,
        "last_statement_date": None,
        "last_statement_balance": None,
        "mono_last_synced_at": None,
        "mono_last_sync_error": None,
        "currency_code": "NGN",
        "bank_name": "Test Bank",
        "bank_code": "058",
        "account_number": "1234567890",
        "display_name": "Test Bank - Operations (1234)",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _mono_account_info(**overrides):
    """MonoAccountInfo that by default matches the _account() defaults."""
    values = {
        "id": "mono-account-new",
        "name": "Operations",
        "account_number": "1234567890",
        "currency": "NGN",
        "balance": 500000,
        "type": "SAVINGS",
        "institution_name": "Test Bank",
        "bank_code": "058",
    }
    values.update(overrides)
    return MonoAccountInfo(**values)


def test_incremental_sync_uses_newest_statement_line_as_cursor() -> None:
    """Window floor is max(BankStatementLine.transaction_date) across every
    source — manual or Mono. A manual statement import through 2026-04-10
    means the next Mono sync pulls [2026-04-10, today], which is how both
    paths share one cursor without a separate cutover field.
    """
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account()
    captured: dict[str, date] = {}

    def _sync_window(_account, from_date, to_date, *, user_id=None):
        captured["from_date"] = from_date
        captured["to_date"] = to_date
        return MonoSyncResult(success=True)

    with (
        patch.object(
            svc,
            "_get_newest_line_date",
            return_value=date(2026, 4, 10),
        ),
        patch.object(svc, "_sync_window", side_effect=_sync_window),
    ):
        result = svc.sync_account_incremental(account)

    assert result.success is True
    assert captured["from_date"] == date(2026, 4, 10)
    assert captured["to_date"] == date.today()


def test_incremental_sync_falls_back_to_90_days_when_no_lines_exist() -> None:
    """A brand-new account with no statement history yet: first-ever sync
    pulls the last 90 days so Mono's own backfill has something to catch.
    """
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account()
    captured: dict[str, date] = {}

    def _sync_window(_account, from_date, to_date, *, user_id=None):
        captured["from_date"] = from_date
        return MonoSyncResult(success=True)

    with (
        patch.object(svc, "_get_newest_line_date", return_value=None),
        patch.object(svc, "_sync_window", side_effect=_sync_window),
    ):
        result = svc.sync_account_incremental(account)

    assert result.success is True
    assert captured["from_date"] == date.today() - timedelta(days=90)


def test_sync_account_by_id_raises_joined_result_errors() -> None:
    db = MagicMock()
    org_id = uuid4()
    account = _account(organization_id=org_id)
    db.get.return_value = account
    svc = MonoSyncService(db)

    with (
        patch.object(
            svc,
            "sync_account_via_refresh",
            return_value=MonoSyncResult(
                success=False,
                message="fallback",
                errors=["provider failed", "cursor unchanged"],
            ),
        ),
        pytest.raises(RuntimeError, match="provider failed; cursor unchanged"),
    ):
        svc.sync_account_by_id(org_id, account.bank_account_id)


def test_duplicate_only_window_does_not_create_empty_statement() -> None:
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account(
        mono_last_transaction_date=date(2026, 4, 10),
        last_statement_date=date(2026, 4, 10),
        mono_last_sync_error="previous failure",
    )
    txn = MonoTransaction(
        id="txn-1",
        narration="Existing transaction",
        amount=10000,
        type="debit",
        balance=12345,
        date="2026-04-10T12:00:00Z",
    )
    client = MagicMock()
    client.get_account_info.return_value = MonoAccountInfo(
        id="mono-account-1",
        name="Operations",
        account_number="1234567890",
        currency="NGN",
        balance=12345,
    )
    client.get_all_transactions.return_value = [txn]
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
        patch.object(svc, "_get_existing_transaction_ids", return_value={"mono_txn-1"}),
        patch.object(svc, "_get_or_create_statement") as get_or_create,
        patch.object(svc, "_get_max_line_number") as max_line,
    ):
        result = svc._sync_window(
            account,
            date(2026, 4, 3),
            date(2026, 4, 15),
        )

    assert result.success is True
    assert result.statement_id is None
    assert result.transactions_synced == 0
    assert result.duplicates_skipped == 1
    assert account.last_statement_balance == Decimal("123.45")
    assert account.mono_last_sync_error is None
    get_or_create.assert_not_called()
    max_line.assert_not_called()
    db.add.assert_not_called()
    db.flush.assert_called()


def test_sync_rejects_transactions_with_empty_id() -> None:
    """
    Mono transactions arriving with empty/None ``id`` must be rejected with
    MonoError, not silently dropped via dedupe collision on ``"mono_None"``.

    The partial unique index on ``transaction_id LIKE 'mono_%'`` would
    otherwise accept the first such transaction and silently drop the rest
    as duplicates — data loss disguised as dedupe.
    """
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account()
    txn_no_id = MonoTransaction(
        id="",
        narration="Bad payload",
        amount=10000,
        type="credit",
        balance=12345,
        date="2026-04-10T12:00:00Z",
    )
    client = MagicMock()
    client.get_account_info.return_value = MonoAccountInfo(
        id="mono-account-1",
        name="Operations",
        account_number="1234567890",
        currency="NGN",
        balance=12345,
    )
    client.get_all_transactions.return_value = [txn_no_id]
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
        patch.object(svc, "_get_existing_transaction_ids", return_value=set()),
    ):
        result = svc._sync_window(
            account,
            date(2026, 4, 3),
            date(2026, 4, 15),
        )

    assert result.success is False, "Sync accepted a transaction without an id"
    assert any(
        "id" in err.lower() or "missing" in err.lower() for err in result.errors
    ), f"Expected error mentioning missing id; got {result.errors}"
    db.add.assert_not_called()


def test_link_account_rejects_mono_id_already_linked_elsewhere() -> None:
    db = MagicMock()
    org_id = uuid4()
    account = _account(organization_id=org_id, mono_account_id=None)
    db.get.return_value = account
    db.scalar.return_value = _account(mono_account_id="mono-account-duplicate")
    svc = MonoSyncService(db)
    client = MagicMock()
    client.exchange_token.return_value = MonoExchangeResult(
        account_id="mono-account-duplicate"
    )
    client.get_account_info.return_value = _mono_account_info(
        id="mono-account-duplicate"
    )
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(svc, "is_configured", return_value=True),
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
        pytest.raises(ValueError) as exc,
    ):
        svc.link_account(org_id, account.bank_account_id, "widget-code")

    assert "already linked" in str(exc.value)
    assert account.mono_account_id is None
    db.flush.assert_not_called()


def test_link_account_clears_stale_sync_health_on_success() -> None:
    db = MagicMock()
    org_id = uuid4()
    account = _account(
        organization_id=org_id,
        mono_account_id="old-mono-account",
        mono_sync_from_date=date(2026, 1, 1),
        mono_last_transaction_date=date(2026, 3, 31),
        mono_last_synced_at=datetime(2026, 4, 1, 9, 0, 0),
        mono_last_sync_error="provider down",
    )
    db.get.return_value = account
    db.scalar.return_value = None
    svc = MonoSyncService(db)
    client = MagicMock()
    client.exchange_token.return_value = MonoExchangeResult(
        account_id="new-mono-account"
    )
    client.get_account_info.return_value = _mono_account_info(id="new-mono-account")
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(svc, "is_configured", return_value=True),
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
    ):
        result = svc.link_account(org_id, account.bank_account_id, "widget-code")

    assert result["status"] == "success"
    assert account.mono_account_id == "new-mono-account"
    assert account.mono_last_synced_at is None
    assert account.mono_last_sync_error is None
    db.flush.assert_called_once()


def test_link_account_refuses_mismatched_institution() -> None:
    """The Zenith/UBA incident: a user on the UBA USD row clicks Connect via
    Mono, picks Zenith in the widget, and the link must be refused — not
    silently written — because the stored bank_code/account_number don't
    match Mono's authoritative identity."""
    db = MagicMock()
    org_id = uuid4()
    account = _account(
        organization_id=org_id,
        mono_account_id=None,
        bank_name="United Bank for Africa",
        bank_code="033",
        account_number="3004154294",
        currency_code="USD",
    )
    db.get.return_value = account
    db.scalar.return_value = None
    svc = MonoSyncService(db)
    client = MagicMock()
    client.exchange_token.return_value = MonoExchangeResult(account_id="zenith-mono-id")
    client.get_account_info.return_value = _mono_account_info(
        id="zenith-mono-id",
        institution_name="Zenith Bank",
        bank_code="057",
        account_number="5070061296",
        currency="USD",
    )
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(svc, "is_configured", return_value=True),
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
        pytest.raises(ValueError) as exc,
    ):
        svc.link_account(org_id, account.bank_account_id, "widget-code")

    message = str(exc.value)
    assert "does not match" in message
    assert "bank_code" in message
    assert "033" in message and "057" in message
    assert "account_number" in message
    assert "5070061296" in message
    # Crucially: the row must not be touched.
    assert account.mono_account_id is None
    db.flush.assert_not_called()


def test_link_account_refuses_currency_mismatch() -> None:
    """A USD row must not accept a Mono account reporting a different
    currency, even if bank details happen to line up."""
    db = MagicMock()
    org_id = uuid4()
    account = _account(
        organization_id=org_id,
        mono_account_id=None,
        bank_code="057",
        account_number="5070061296",
        currency_code="USD",
    )
    db.get.return_value = account
    db.scalar.return_value = None
    svc = MonoSyncService(db)
    client = MagicMock()
    client.exchange_token.return_value = MonoExchangeResult(account_id="ngn-mono-id")
    client.get_account_info.return_value = _mono_account_info(
        id="ngn-mono-id",
        bank_code="057",
        account_number="5070061296",
        currency="NGN",
    )
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(svc, "is_configured", return_value=True),
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
        pytest.raises(ValueError, match="currency"),
    ):
        svc.link_account(org_id, account.bank_account_id, "widget-code")


def test_link_account_tolerates_formatted_account_number() -> None:
    """Operator-entered account numbers with dashes/spaces should still
    match Mono's digit-only representation after normalisation."""
    db = MagicMock()
    org_id = uuid4()
    account = _account(
        organization_id=org_id,
        mono_account_id=None,
        bank_code="057",
        account_number="507-006-1296",
        currency_code="USD",
    )
    db.get.return_value = account
    db.scalar.return_value = None
    svc = MonoSyncService(db)
    client = MagicMock()
    client.exchange_token.return_value = MonoExchangeResult(account_id="zenith-mono-id")
    client.get_account_info.return_value = _mono_account_info(
        id="zenith-mono-id",
        bank_code="057",
        account_number="5070061296",
        currency="USD",
    )
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(svc, "is_configured", return_value=True),
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
    ):
        result = svc.link_account(org_id, account.bank_account_id, "widget-code")

    assert result["status"] == "success"
    assert account.mono_account_id == "zenith-mono-id"


def test_unlink_mono_clears_all_tracking_fields() -> None:
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    account = _account(
        organization_id=org_id,
        mono_account_id="mono-account-1",
        mono_sync_from_date=date(2026, 1, 1),
        mono_last_transaction_date=date(2026, 3, 31),
        mono_last_synced_at=datetime(2026, 4, 1, 9, 0, 0),
        mono_last_sync_error="provider down",
    )
    db.get.return_value = account

    result = BankAccountService().unlink_mono(
        db,
        org_id,
        account.bank_account_id,
        require_linked=True,
        updated_by=user_id,
    )

    assert result is account
    assert account.mono_account_id is None
    assert account.mono_sync_from_date is None
    assert account.mono_last_transaction_date is None
    assert account.mono_last_synced_at is None
    assert account.mono_last_sync_error is None
    assert account.updated_by == user_id
    db.flush.assert_called_once()


def test_unlink_mono_requires_linked_account_with_domain_error() -> None:
    db = MagicMock()
    org_id = uuid4()
    account = _account(organization_id=org_id, mono_account_id=None)
    db.get.return_value = account

    with pytest.raises(ValueError, match="not linked to Mono"):
        BankAccountService().unlink_mono(
            db,
            org_id,
            account.bank_account_id,
            require_linked=True,
        )

    db.flush.assert_not_called()


def test_webhook_logs_redact_sensitive_account_payload(caplog) -> None:
    db = MagicMock()
    payload = {
        "event": "mono.events.account_updated",
        "data": {
            "account": {
                "_id": "mono-account-1",
                "accountNumber": "0100000062",
                "bvn": "22000000003",
            },
            "meta": {
                "data_status": "AVAILABLE",
                "sync_status": "SUCCESSFUL",
                "job_id": "job-1",
                "has_new_data": True,
            },
        },
    }
    caplog.set_level(
        logging.INFO,
        logger="app.services.finance.banking.mono_sync",
    )

    with (
        patch(
            "app.services.finance.banking.mono_sync.resolve_value",
            return_value="webhook-secret",
        ),
        patch("app.services.finance.banking.mono_sync.MonoClient") as mono_client,
        patch("app.tasks.finance.sync_mono_account.delay") as enqueue,
    ):
        mono_client.return_value.verify_webhook.return_value = True
        result = MonoSyncService(db).process_webhook(
            "webhook-secret",
            json.dumps(payload).encode(),
        )

    assert result["status"] == "success"
    enqueue.assert_called_once_with("mono-account-1")
    assert "22000000003" not in caplog.text
    assert "0100000062" not in caplog.text


def test_webhook_failed_status_records_error_on_linked_account() -> None:
    """Mono ``data_status=FAILED`` must surface in ``mono_last_sync_error``.

    Without this, the next manual sync hits Mono's cached ``/accounts/{id}``
    endpoint, returns 200 with zero txns, and the user sees a false-positive
    success — see the UBA USD account that triggered this fix.
    """
    db = MagicMock()
    linked_account = _account(
        mono_account_id="mono-account-failed",
        mono_last_sync_error=None,
    )
    db.scalar.return_value = linked_account
    payload = {
        "event": "mono.events.account_updated",
        "data": {
            "account": {"_id": "mono-account-failed"},
            "meta": {
                "data_status": "FAILED",
                "sync_status": "FAILED",
                "job_id": "job-42",
                "has_new_data": False,
            },
        },
    }

    with (
        patch(
            "app.services.finance.banking.mono_sync.resolve_value",
            return_value="webhook-secret",
        ),
        patch("app.services.finance.banking.mono_sync.MonoClient") as mono_client,
        patch("app.tasks.finance.sync_mono_account.delay") as enqueue,
    ):
        mono_client.return_value.verify_webhook.return_value = True
        result = MonoSyncService(db).process_webhook(
            "webhook-secret",
            json.dumps(payload).encode(),
        )

    assert result["status"] == "success"
    enqueue.assert_not_called()
    assert linked_account.mono_last_sync_error is not None
    assert "Mono data refresh failed" in linked_account.mono_last_sync_error
    assert "job-42" in linked_account.mono_last_sync_error
    db.flush.assert_called()


def test_webhook_sync_status_failed_with_data_status_available_records_error() -> None:
    """When Mono reports ``sync_status=FAILED`` alongside ``data_status=AVAILABLE``,
    the bank scrape failed but Mono is still serving cached data from a prior
    successful scrape. Without this branch the handler sees ``AVAILABLE`` and
    silently enqueues a sync that reads the stale cache, returns zero new,
    and clears any previously recorded error — so the operator never sees
    that reauthorisation is needed.

    Exact shape observed for the UBA account on 2026-04-20: ``data_status=AVAILABLE
    sync_status=FAILED retrieved_data=['balance','transactions']``.
    """
    db = MagicMock()
    linked_account = _account(
        mono_account_id="mono-uba-failed",
        mono_last_sync_error=None,
    )
    db.scalar.return_value = linked_account
    payload = {
        "event": "mono.events.account_updated",
        "data": {
            "account": {"_id": "mono-uba-failed"},
            "meta": {
                "data_status": "AVAILABLE",
                "sync_status": "FAILED",
                "job_id": "job-uba-42",
                "has_new_data": False,
                "retrieved_data": ["balance", "transactions"],
                "data_request_id": "REQ-UBA-42",
            },
        },
    }

    with (
        patch(
            "app.services.finance.banking.mono_sync.resolve_value",
            return_value="webhook-secret",
        ),
        patch("app.services.finance.banking.mono_sync.MonoClient") as mono_client,
        patch("app.tasks.finance.sync_mono_account.delay") as enqueue,
    ):
        mono_client.return_value.verify_webhook.return_value = True
        result = MonoSyncService(db).process_webhook(
            "webhook-secret",
            json.dumps(payload).encode(),
        )

    assert result["status"] == "success"
    # Critical: the sync must NOT be enqueued. A zero-transaction sync on
    # the stale cache would wipe the error banner we just set.
    enqueue.assert_not_called()
    assert linked_account.mono_last_sync_error is not None
    assert "Mono data refresh failed" in linked_account.mono_last_sync_error
    assert "job-uba-42" in linked_account.mono_last_sync_error
    db.flush.assert_called()


def test_webhook_sync_status_reauthorisation_required_records_actionable_error() -> (
    None
):
    """``sync_status=REAUTHORISATION_REQUIRED`` with ``data_status=AVAILABLE``
    is what Mono sends once bank-side OAuth has expired — the cache is still
    served but no fresh transactions can be fetched. Earlier handler logic
    only deny-listed ``sync_status == "FAILED"`` and let reauth-required
    fall through to the AVAILABLE branch, queuing a sync that dedupe-pulled
    the stale cache, succeeded with zero new lines, and race-wiped any
    prior error banner. Users then saw "up to date" for weeks while their
    bank link was dead. The fix allow-lists known-success values so any
    non-success sync_status (incl. future Mono states) is treated as a
    failure that must not be silently synced past.
    """
    db = MagicMock()
    linked_account = _account(
        mono_account_id="mono-zenith-reauth",
        mono_last_sync_error=None,
    )
    db.scalar.return_value = linked_account
    payload = {
        "event": "mono.events.account_updated",
        "data": {
            "account": {"_id": "mono-zenith-reauth"},
            "meta": {
                "data_status": "AVAILABLE",
                "sync_status": "REAUTHORISATION_REQUIRED",
                "job_id": "job-reauth-7",
                "has_new_data": False,
                "retrieved_data": ["balance", "transactions"],
                "data_request_id": "REQ-REAUTH-7",
            },
        },
    }

    with (
        patch(
            "app.services.finance.banking.mono_sync.resolve_value",
            return_value="webhook-secret",
        ),
        patch("app.services.finance.banking.mono_sync.MonoClient") as mono_client,
        patch("app.tasks.finance.sync_mono_account.delay") as enqueue,
    ):
        mono_client.return_value.verify_webhook.return_value = True
        result = MonoSyncService(db).process_webhook(
            "webhook-secret",
            json.dumps(payload).encode(),
        )

    assert result["status"] == "success"
    enqueue.assert_not_called()
    assert linked_account.mono_last_sync_error is not None
    # Actionable copy: the user can fix this themselves by reauthorising
    # in the Mono Connect widget. The message must say so plainly.
    assert "Reauthorise" in linked_account.mono_last_sync_error
    assert "Mono Connect widget" in linked_account.mono_last_sync_error
    assert "REQ-REAUTH-7" in linked_account.mono_last_sync_error
    db.flush.assert_called()


def test_webhook_failed_status_with_balance_only_retrieval_reports_partner_limit() -> (
    None
):
    """When Mono reports ``retrieved_data=["balance"]`` without
    ``transactions``, the recorded error must tell the operator (a) this is
    a partner-bank limitation, (b) manual upload is the fallback, and (c)
    quote the ``data_request_id`` for Mono support — so users stop trying
    to re-sync a link that will never produce history.

    This is the exact shape Mono returned for the Zenith USD domiciliary
    account on 2026-04-15 (data_request_id=ALFI0PBHD2E2).
    """
    db = MagicMock()
    linked_account = _account(
        mono_account_id="mono-zenith-usd",
        mono_last_sync_error=None,
    )
    db.scalar.return_value = linked_account
    payload = {
        "event": "mono.events.account_updated",
        "data": {
            "account": {"_id": "mono-zenith-usd"},
            "meta": {
                "data_status": "FAILED",
                "auth_method": "internet_banking",
                "data_request_id": "ALFI0PBHD2E2",
                "retrieved_data": ["balance"],
            },
        },
    }

    with (
        patch(
            "app.services.finance.banking.mono_sync.resolve_value",
            return_value="webhook-secret",
        ),
        patch("app.services.finance.banking.mono_sync.MonoClient") as mono_client,
        patch("app.tasks.finance.sync_mono_account.delay") as enqueue,
    ):
        mono_client.return_value.verify_webhook.return_value = True
        MonoSyncService(db).process_webhook(
            "webhook-secret",
            json.dumps(payload).encode(),
        )

    enqueue.assert_not_called()
    error = linked_account.mono_last_sync_error
    assert error is not None
    # Actionable hints the error must contain:
    assert "balance" in error and "transactions" in error
    assert "partner-bank limitation" in error
    assert "manual statement upload" in error
    assert "ALFI0PBHD2E2" in error
    assert "support@mono.co" in error


def test_webhook_failed_enriches_missing_data_request_id_from_account_info() -> None:
    """When the webhook omits ``data_request_id`` (observed on relink
    follow-ups), ``_record_webhook_failure`` must fall back to
    ``GET /v2/accounts/{id}`` so the error banner still carries the
    reference operators need for Mono support tickets."""
    db = MagicMock()
    linked_account = _account(
        mono_account_id="mono-zenith-usd-new",
        mono_last_sync_error=None,
    )
    db.scalar.return_value = linked_account
    payload = {
        "event": "mono.events.account_updated",
        "data": {
            "account": {"_id": "mono-zenith-usd-new"},
            "meta": {
                # No data_request_id here — this is the bug Mono's webhook has
                "data_status": "FAILED",
                "retrieved_data": ["balance"],
            },
        },
    }
    enriched_info = _mono_account_info(
        id="mono-zenith-usd-new",
        data_request_id="ALZG45DGZZQG",
        data_status="FAILED",
        retrieved_data=["balance"],
    )

    with (
        patch(
            "app.services.finance.banking.mono_sync.resolve_value",
            return_value="webhook-secret",
        ),
        patch("app.services.finance.banking.mono_sync.MonoClient") as mono_client_cls,
        patch("app.tasks.finance.sync_mono_account.delay"),
    ):
        # The MonoClient is constructed twice in this flow: once for
        # webhook verification, once inside _record_webhook_failure for the
        # fallback get_account_info call. Both use the same mock class.
        verifier = MagicMock()
        verifier.verify_webhook.return_value = True
        enricher = MagicMock()
        enricher.get_account_info.return_value = enriched_info
        enricher_cm = MagicMock()
        enricher_cm.__enter__.return_value = enricher
        mono_client_cls.side_effect = [verifier, enricher_cm]

        MonoSyncService(db).process_webhook(
            "webhook-secret",
            json.dumps(payload).encode(),
        )

    enricher.get_account_info.assert_called_once_with("mono-zenith-usd-new")
    error = linked_account.mono_last_sync_error
    assert error is not None
    assert "ALZG45DGZZQG" in error
    assert "unknown" not in error


def test_webhook_failed_enrichment_tolerates_mono_error() -> None:
    """If the fallback ``get_account_info`` call fails, we still record the
    failure with ``data_request_id=unknown`` rather than raising and losing
    the signal entirely."""
    db = MagicMock()
    linked_account = _account(
        mono_account_id="mono-zenith-usd-new",
        mono_last_sync_error=None,
    )
    db.scalar.return_value = linked_account
    payload = {
        "event": "mono.events.account_updated",
        "data": {
            "account": {"_id": "mono-zenith-usd-new"},
            "meta": {"data_status": "FAILED", "retrieved_data": ["balance"]},
        },
    }

    with (
        patch(
            "app.services.finance.banking.mono_sync.resolve_value",
            return_value="webhook-secret",
        ),
        patch("app.services.finance.banking.mono_sync.MonoClient") as mono_client_cls,
        patch("app.tasks.finance.sync_mono_account.delay"),
    ):
        verifier = MagicMock()
        verifier.verify_webhook.return_value = True
        enricher = MagicMock()
        enricher.get_account_info.side_effect = MonoError("provider down")
        enricher_cm = MagicMock()
        enricher_cm.__enter__.return_value = enricher
        mono_client_cls.side_effect = [verifier, enricher_cm]

        MonoSyncService(db).process_webhook(
            "webhook-secret",
            json.dumps(payload).encode(),
        )

    error = linked_account.mono_last_sync_error
    assert error is not None
    # Still records the partial-retrieval message, just without the reference
    assert "partner-bank limitation" in error
    assert "data_request_id=unknown" in error


def test_webhook_jobs_update_event_is_logged_not_unhandled(caplog) -> None:
    """``mono.accounts.jobs.update`` carries async indexer job state changes.
    We don't act on them (the authoritative outcome is ``account_updated``)
    but they must NOT show up as 'Unhandled Mono event'."""
    db = MagicMock()
    payload = {
        "event": "mono.accounts.jobs.update",
        "data": {
            "account": {"_id": "mono-zenith-usd-new"},
            "status": "RUNNING",
            "job_id": "job-77",
            "data_request_id": "ALZG45DGZZQG",
        },
    }
    caplog.set_level(
        logging.INFO,
        logger="app.services.finance.banking.mono_sync",
    )

    with (
        patch(
            "app.services.finance.banking.mono_sync.resolve_value",
            return_value="webhook-secret",
        ),
        patch("app.services.finance.banking.mono_sync.MonoClient") as mono_client,
    ):
        mono_client.return_value.verify_webhook.return_value = True
        MonoSyncService(db).process_webhook(
            "webhook-secret",
            json.dumps(payload).encode(),
        )

    assert "Unhandled" not in caplog.text
    assert "Mono job update" in caplog.text
    assert "RUNNING" in caplog.text
    assert "job-77" in caplog.text
    assert "ALZG45DGZZQG" in caplog.text


def test_mono_client_get_account_info_captures_data_request_meta() -> None:
    """``get_account_info`` must surface ``data_request_id`` /
    ``retrieved_data`` / ``data_status`` from ``data.meta`` so the webhook
    fallback enrichment has something to read."""
    client = MonoClient(MonoConfig(secret_key="secret", public_key="public"))
    with patch.object(
        client,
        "_request",
        return_value={
            "status": "successful",
            "data": {
                "account": {
                    "id": "mono-1",
                    "name": "DOTMAC",
                    "account_number": "5070061296",
                    "currency": "USD",
                    "balance": 41983,
                    "type": "CURRENT",
                    "institution": {"name": "Zenith Bank", "bank_code": "057"},
                },
                "meta": {
                    "data_status": "FAILED",
                    "data_request_id": "ALFI0PBHD2E2",
                    "retrieved_data": ["balance"],
                },
            },
        },
    ):
        info = client.get_account_info("mono-1")

    assert info.data_request_id == "ALFI0PBHD2E2"
    assert info.data_status == "FAILED"
    assert info.retrieved_data == ["balance"]


def test_webhook_reauthorized_event_is_logged_not_unhandled(caplog) -> None:
    """Mono fires ``account_reauthorized`` when the user completes the
    Connect widget with a reauth_token. It's informational — the real signal
    comes in the follow-up ``account_updated`` — but it must NOT show up in
    logs as 'Unhandled Mono event'."""
    db = MagicMock()
    payload = {
        "event": "mono.events.account_reauthorized",
        "data": {"account": {"_id": "mono-zenith-usd"}},
    }
    caplog.set_level(
        logging.INFO,
        logger="app.services.finance.banking.mono_sync",
    )

    with (
        patch(
            "app.services.finance.banking.mono_sync.resolve_value",
            return_value="webhook-secret",
        ),
        patch("app.services.finance.banking.mono_sync.MonoClient") as mono_client,
    ):
        mono_client.return_value.verify_webhook.return_value = True
        MonoSyncService(db).process_webhook(
            "webhook-secret",
            json.dumps(payload).encode(),
        )

    assert "Unhandled" not in caplog.text
    assert "account_reauthorized" in caplog.text
    assert "mono-zenith-usd" in caplog.text


def test_webhook_failed_status_unlinked_account_is_noop() -> None:
    db = MagicMock()
    db.scalar.return_value = None
    payload = {
        "event": "mono.events.account_updated",
        "data": {
            "account": {"_id": "mono-orphan"},
            "meta": {"data_status": "FAILED", "job_id": "job-99"},
        },
    }

    with (
        patch(
            "app.services.finance.banking.mono_sync.resolve_value",
            return_value="webhook-secret",
        ),
        patch("app.services.finance.banking.mono_sync.MonoClient") as mono_client,
    ):
        mono_client.return_value.verify_webhook.return_value = True
        MonoSyncService(db).process_webhook(
            "webhook-secret",
            json.dumps(payload).encode(),
        )

    db.flush.assert_not_called()


def test_sync_advances_last_statement_date_when_balance_refreshes() -> None:
    """When Mono returns a fresh balance with zero new txns, the as-of pair
    (``last_statement_date`` + ``last_statement_balance``) must advance
    together so the dashboard doesn't show today's balance dated months ago.
    """
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account(
        currency_code="USD",
        last_statement_date=date(2026, 1, 26),
        last_statement_balance=Decimal("100.00"),
        mono_sync_from_date=date(2026, 4, 15),
        mono_last_transaction_date=None,
    )
    client = MagicMock()
    client.get_account_info.return_value = MonoAccountInfo(
        id="mono-account-1",
        name="UBA USD",
        account_number="1234567890",
        currency="USD",
        balance=41983,
    )
    client.get_all_transactions.return_value = []
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
    ):
        result = svc._sync_window(
            account,
            date(2026, 4, 15),
            date(2026, 4, 15),
        )

    assert result.success is True
    assert account.last_statement_balance == Decimal("419.83")
    # Pair must advance together — not stay at 2026-01-26
    assert account.last_statement_date == date.today()


def test_sync_does_not_regress_last_statement_date_on_zero_txn_sync() -> None:
    """Forward-only: a future last_statement_date (set by a manual import
    covering future-dated value dates) must not be pulled back to today."""
    db = MagicMock()
    svc = MonoSyncService(db)
    future = date.today() + timedelta(days=30)
    account = _account(
        currency_code="NGN",
        last_statement_date=future,
        last_statement_balance=Decimal("100.00"),
        mono_sync_from_date=date(2026, 4, 1),
        mono_last_transaction_date=date(2026, 4, 1),
    )
    client = MagicMock()
    client.get_account_info.return_value = MonoAccountInfo(
        id="mono-account-1",
        name="Ops",
        account_number="1234567890",
        currency="NGN",
        balance=500000,
    )
    client.get_all_transactions.return_value = []
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
    ):
        svc._sync_window(account, date(2026, 4, 1), date(2026, 4, 15))

    assert account.last_statement_date == future


def test_sync_by_mono_account_id_warns_when_unlinked(caplog) -> None:
    db = MagicMock()
    db.scalar.return_value = None
    caplog.set_level(
        logging.WARNING,
        logger="app.services.finance.banking.mono_sync",
    )

    result = MonoSyncService(db).sync_by_mono_account_id("mono-missing")

    assert result.success is False
    assert "No bank account linked" in result.message
    assert "Mono webhook for unlinked account mono-missing" in caplog.text


def test_parse_date_rejects_missing_or_invalid_dates() -> None:
    with pytest.raises(MonoError, match="missing"):
        MonoSyncService._parse_date("")

    with pytest.raises(MonoError, match="invalid"):
        MonoSyncService._parse_date("not-a-date")


def test_invalid_mono_transaction_date_marks_sync_failed_without_watermark_move() -> (
    None
):
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account(
        mono_last_transaction_date=date(2026, 4, 1),
        last_statement_date=date(2026, 4, 1),
    )
    txn = MonoTransaction(
        id="txn-bad-date",
        narration="Bad date transaction",
        amount=10000,
        type="credit",
        balance=20000,
        date="not-a-date",
    )
    client = MagicMock()
    client.get_account_info.return_value = MonoAccountInfo(
        id="mono-account-1",
        name="Operations",
        account_number="1234567890",
        currency="NGN",
        balance=20000,
    )
    client.get_all_transactions.return_value = [txn]
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
        patch.object(svc, "_get_existing_transaction_ids", return_value=set()),
    ):
        result = svc._sync_window(
            account,
            date(2026, 4, 1),
            date(2026, 4, 15),
        )

    assert result.success is False
    assert result.transactions_synced == 0
    assert "invalid transaction date" in result.message
    assert "transaction_id=txn-bad-date" in result.message
    assert result.errors == [
        "Mono transaction has invalid transaction date: 'not-a-date' "
        "(transaction_id=txn-bad-date)"
    ]
    assert account.mono_last_transaction_date == date(2026, 4, 1)
    assert account.last_statement_date == date(2026, 4, 1)
    assert account.last_statement_balance is None
    assert "invalid transaction date" in account.mono_last_sync_error
    assert "transaction_id=txn-bad-date" in account.mono_last_sync_error
    db.add.assert_not_called()


def test_sync_advances_watermark_from_newest_returned_transaction_date() -> None:
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account(
        mono_last_transaction_date=date(2026, 4, 1),
        last_statement_date=date(2026, 4, 1),
    )
    statement = SimpleNamespace(
        statement_id=uuid4(),
        total_credits=Decimal("0"),
        total_debits=Decimal("0"),
        total_lines=0,
        unmatched_lines=0,
        closing_balance=Decimal("0"),
    )
    transactions = [
        MonoTransaction(
            id="txn-newer",
            narration="Newer",
            amount=10000,
            type="credit",
            balance=30000,
            date="2026-04-12T09:00:00Z",
        ),
        MonoTransaction(
            id="txn-older",
            narration="Older",
            amount=5000,
            type="debit",
            balance=25000,
            date="2026-04-05T09:00:00Z",
        ),
    ]
    client = MagicMock()
    client.get_account_info.return_value = MonoAccountInfo(
        id="mono-account-1",
        name="Operations",
        account_number="1234567890",
        currency="NGN",
        balance=25000,
    )
    client.get_all_transactions.return_value = transactions
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
        patch.object(svc, "_get_existing_transaction_ids", return_value=set()),
        patch.object(svc, "_get_or_create_statement", return_value=statement),
        patch.object(svc, "_get_max_line_number", return_value=0),
    ):
        result = svc._sync_window(
            account,
            date(2026, 4, 1),
            date(2026, 4, 15),
        )

    assert result.success is True
    assert result.transactions_synced == 2
    # mono_last_transaction_date is the Mono-specific sync cursor — it tracks
    # the newest imported txn date.
    assert account.mono_last_transaction_date == date(2026, 4, 12)
    # last_statement_date is the as-of date for last_statement_balance. When
    # account_info refreshes the balance, the pair advances to today.
    assert account.last_statement_date == date.today()


def test_sync_clamps_future_dated_watermark_to_today() -> None:
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account(
        mono_last_transaction_date=date(2026, 4, 1),
        last_statement_date=date(2026, 4, 1),
    )
    statement = SimpleNamespace(
        statement_id=uuid4(),
        total_credits=Decimal("0"),
        total_debits=Decimal("0"),
        total_lines=0,
        unmatched_lines=0,
        closing_balance=None,
    )
    txn = MonoTransaction(
        id="txn-future",
        narration="Future dated",
        amount=10000,
        type="credit",
        balance=30000,
        date="2999-01-01T09:00:00Z",
    )
    client = MagicMock()
    client.get_account_info.return_value = MonoAccountInfo(
        id="mono-account-1",
        name="Operations",
        account_number="1234567890",
        currency="NGN",
        balance=30000,
    )
    client.get_all_transactions.return_value = [txn]
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
        patch.object(svc, "_get_existing_transaction_ids", return_value=set()),
        patch.object(svc, "_get_or_create_statement", return_value=statement),
        patch.object(svc, "_get_max_line_number", return_value=0),
    ):
        result = svc._sync_window(
            account,
            date(2026, 4, 1),
            date(2026, 4, 15),
        )

    assert result.success is True
    assert account.mono_last_transaction_date == date.today()
    assert account.last_statement_date == date.today()


def test_sync_buckets_mono_statements_by_transaction_month() -> None:
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account(
        mono_last_transaction_date=date(2026, 4, 1),
        last_statement_date=date(2026, 4, 1),
    )
    april_statement = SimpleNamespace(
        statement_id=uuid4(),
        total_credits=Decimal("0"),
        total_debits=Decimal("0"),
        total_lines=0,
        unmatched_lines=0,
        closing_balance=None,
    )
    may_statement = SimpleNamespace(
        statement_id=uuid4(),
        total_credits=Decimal("0"),
        total_debits=Decimal("0"),
        total_lines=0,
        unmatched_lines=0,
        closing_balance=None,
    )
    transactions = [
        MonoTransaction(
            id="txn-april",
            narration="April",
            amount=10000,
            type="credit",
            balance=30000,
            date="2026-04-30T09:00:00Z",
        ),
        MonoTransaction(
            id="txn-may",
            narration="May",
            amount=5000,
            type="debit",
            balance=25000,
            date="2026-05-01T09:00:00Z",
        ),
    ]
    client = MagicMock()
    client.get_account_info.return_value = MonoAccountInfo(
        id="mono-account-1",
        name="Operations",
        account_number="1234567890",
        currency="NGN",
        balance=25000,
    )
    client.get_all_transactions.return_value = transactions
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
        patch.object(svc, "_get_existing_transaction_ids", return_value=set()),
        patch.object(
            svc,
            "_get_or_create_statement",
            side_effect=[april_statement, may_statement],
        ) as get_or_create,
        patch.object(svc, "_get_max_line_number", return_value=0),
    ):
        result = svc._sync_window(
            account,
            date(2026, 4, 25),
            date(2026, 5, 2),
        )

    assert result.success is True
    assert get_or_create.call_args_list[0].kwargs["period_start"] == date(2026, 4, 1)
    assert get_or_create.call_args_list[0].kwargs["period_end"] == date(2026, 4, 30)
    assert get_or_create.call_args_list[1].kwargs["period_start"] == date(2026, 5, 1)
    assert get_or_create.call_args_list[1].kwargs["period_end"] == date(2026, 5, 31)


def test_sync_formats_messages_with_account_currency() -> None:
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account(
        currency_code="USD",
        mono_last_transaction_date=date(2026, 4, 10),
        last_statement_date=date(2026, 4, 10),
    )
    client = MagicMock()
    client.get_account_info.return_value = MonoAccountInfo(
        id="mono-account-1",
        name="Operations",
        account_number="1234567890",
        currency="USD",
        balance=41983,
    )
    client.get_all_transactions.return_value = []
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
    ):
        result = svc._sync_window(
            account,
            date(2026, 4, 3),
            date(2026, 4, 15),
        )

    assert result.success is True
    assert "USD 419.83" in result.message
    assert "₦" not in result.message


def test_sync_logs_when_narration_is_truncated(caplog) -> None:
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account(
        mono_last_transaction_date=date(2026, 4, 1),
        last_statement_date=date(2026, 4, 1),
    )
    statement = SimpleNamespace(
        statement_id=uuid4(),
        total_credits=Decimal("0"),
        total_debits=Decimal("0"),
        total_lines=0,
        unmatched_lines=0,
        closing_balance=None,
    )
    txn = MonoTransaction(
        id="txn-long-narration",
        narration="x" * 501,
        amount=10000,
        type="credit",
        balance=30000,
        date="2026-04-12T09:00:00Z",
    )
    client = MagicMock()
    client.get_account_info.return_value = MonoAccountInfo(
        id="mono-account-1",
        name="Operations",
        account_number="1234567890",
        currency="NGN",
        balance=30000,
    )
    client.get_all_transactions.return_value = [txn]
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client
    caplog.set_level(
        logging.DEBUG,
        logger="app.services.finance.banking.mono_sync",
    )

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
        patch.object(svc, "_get_existing_transaction_ids", return_value=set()),
        patch.object(svc, "_get_or_create_statement", return_value=statement),
        patch.object(svc, "_get_max_line_number", return_value=0),
    ):
        result = svc._sync_window(
            account,
            date(2026, 4, 1),
            date(2026, 4, 15),
        )

    assert result.success is True
    added_line = db.add.call_args.args[0]
    assert added_line.description == "x" * 500
    assert "Truncated Mono narration" in caplog.text
    assert "txn-long-narration" in caplog.text


def test_sync_counts_only_inserted_lines_when_duplicate_insert_races() -> None:
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account(
        mono_last_transaction_date=date(2026, 4, 1),
        last_statement_date=date(2026, 4, 1),
    )
    statement = SimpleNamespace(
        statement_id=uuid4(),
        total_credits=Decimal("0"),
        total_debits=Decimal("0"),
        total_lines=0,
        unmatched_lines=0,
        closing_balance=Decimal("0"),
    )
    transactions = [
        MonoTransaction(
            id="txn-raced",
            narration="Raced duplicate",
            amount=10000,
            type="credit",
            balance=30000,
            date="2026-04-12T09:00:00Z",
        ),
        MonoTransaction(
            id="txn-inserted",
            narration="Inserted",
            amount=5000,
            type="debit",
            balance=25000,
            date="2026-04-12T10:00:00Z",
        ),
    ]
    client = MagicMock()
    client.get_account_info.return_value = MonoAccountInfo(
        id="mono-account-1",
        name="Operations",
        account_number="1234567890",
        currency="NGN",
        balance=25000,
    )
    client.get_all_transactions.return_value = transactions
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
        patch.object(svc, "_get_existing_transaction_ids", return_value=set()),
        patch.object(svc, "_get_or_create_statement", return_value=statement),
        patch.object(svc, "_get_max_line_number", return_value=0),
        patch.object(svc, "_add_statement_line_once", side_effect=[False, True]),
    ):
        result = svc._sync_window(
            account,
            date(2026, 4, 1),
            date(2026, 4, 15),
        )

    assert result.success is True
    assert result.transactions_synced == 1
    assert result.duplicates_skipped == 1
    assert result.total_credits == Decimal("0")
    assert result.total_debits == Decimal("50")
    assert statement.total_lines == 1
    assert statement.unmatched_lines == 1
    assert statement.closing_balance is None


def test_mono_error_sets_error_and_success_clears_it() -> None:
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account(mono_last_sync_error=None)
    failing_client = MagicMock()
    failing_client.get_account_info.side_effect = MonoError("provider down")
    failing_cm = MagicMock()
    failing_cm.__enter__.return_value = failing_client

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=failing_cm,
        ),
    ):
        failed = svc._sync_window(account, date(2026, 4, 1), date(2026, 4, 15))

    assert failed.success is False
    assert account.mono_last_sync_error == "provider down"

    successful_client = MagicMock()
    successful_client.get_account_info.return_value = MonoAccountInfo(
        id="mono-account-1",
        name="Operations",
        account_number="1234567890",
        currency="NGN",
        balance=20000,
    )
    successful_client.get_all_transactions.return_value = []
    successful_cm = MagicMock()
    successful_cm.__enter__.return_value = successful_client

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="secret", public_key="public"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=successful_cm,
        ),
    ):
        succeeded = svc._sync_window(account, date(2026, 4, 1), date(2026, 4, 15))

    assert succeeded.success is True
    assert account.mono_last_sync_error is None


def _refresh_client(
    *,
    has_new_data: bool,
    job_status: str | None,
    balance: int = 500000,
    refresh_error: MonoError | None = None,
):
    """MagicMock MonoClient set up for sync_account_via_refresh tests.

    Stubs trigger_data_refresh + get_account_info but deliberately leaves
    get_all_transactions unset — these tests assert the refresh-path
    NEVER pulls transactions directly; that's the whole point.
    """
    client = MagicMock()
    if refresh_error is not None:
        client.trigger_data_refresh.side_effect = refresh_error
    else:
        client.trigger_data_refresh.return_value = MonoDataRefreshResult(
            has_new_data=has_new_data,
            job_id="job-123",
            job_status=job_status,
        )
    client.get_account_info.return_value = MonoAccountInfo(
        id="mono-account-1",
        name="Operations",
        account_number="1234567890",
        currency="NGN",
        balance=balance,
    )
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client
    return client, client_cm


def test_via_refresh_defers_to_webhook_on_processing() -> None:
    """When Mono reports the bank pull is in progress, the scheduled
    task must NOT pull transactions — the account_updated webhook will
    arrive later with a fresh cache and drive ingest from there.
    Balance + freshness still advance from the authoritative account
    endpoint so the dashboard doesn't stall waiting for the webhook.
    """
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account()
    client, client_cm = _refresh_client(
        has_new_data=True,
        job_status="processing",
    )

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="s", public_key="p"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
    ):
        result = svc.sync_account_via_refresh(account)

    assert result.success is True
    assert result.transactions_synced == 0
    assert result.ingestion_state == "pending"
    client.trigger_data_refresh.assert_called_once_with("mono-account-1")
    client.get_all_transactions.assert_not_called()
    assert account.last_statement_balance == Decimal("5000.00")
    assert account.mono_last_synced_at is not None
    assert account.mono_last_sync_error is None


def test_via_refresh_finished_with_no_new_data_reports_up_to_date() -> None:
    """``finished`` + ``has_new_data=false`` means the bank genuinely has
    nothing new. No webhook is coming, so the sync must still advance
    balance + freshness here and surface a clear "up to date" message.
    """
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account()
    client, client_cm = _refresh_client(
        has_new_data=False,
        job_status="finished",
    )

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="s", public_key="p"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
    ):
        result = svc.sync_account_via_refresh(account)

    assert result.success is True
    assert result.ingestion_state == "current"
    assert "up to date" in result.message.lower()
    client.get_all_transactions.assert_not_called()
    assert account.last_statement_balance == Decimal("5000.00")


def test_via_refresh_failed_job_records_reauth_error() -> None:
    """Mono's ``failed`` job_status usually means the linked bank
    credentials need reauthorisation. Record the error but still
    refresh the balance so users can see the last-known value while
    they fix the link.
    """
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account()
    client, client_cm = _refresh_client(
        has_new_data=False,
        job_status="failed",
    )

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="s", public_key="p"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
    ):
        result = svc.sync_account_via_refresh(account)

    assert result.success is False
    assert result.ingestion_state == "failed"
    assert "reauthorisation" in (account.mono_last_sync_error or "").lower()
    client.get_all_transactions.assert_not_called()
    # Balance update happens *before* the failure short-circuit so
    # operators still see the latest-known balance.
    assert account.last_statement_balance == Decimal("5000.00")


def test_via_refresh_falls_back_to_direct_pull_on_rate_limit() -> None:
    """Mono caps refresh at 1/5min/account — a 429 must NOT drop the
    cycle. Fall through to sync_account_incremental so any lines
    already sitting in Mono's cache still get ingested.
    """
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account()
    _client, client_cm = _refresh_client(
        has_new_data=False,
        job_status=None,
        refresh_error=MonoError("rate limited", status_code=429),
    )

    with (
        patch.object(
            svc,
            "_get_mono_config",
            return_value=MonoConfig(secret_key="s", public_key="p"),
        ),
        patch(
            "app.services.finance.banking.mono_sync.MonoClient",
            return_value=client_cm,
        ),
        patch.object(
            svc,
            "sync_account_incremental",
            return_value=MonoSyncResult(
                success=True,
                bank_account_id=account.bank_account_id,
                transactions_synced=3,
                message="fallback ran",
            ),
        ) as fallback,
    ):
        result = svc.sync_account_via_refresh(account)

    fallback.assert_called_once_with(account, user_id=None)
    assert result.transactions_synced == 3
    assert result.message == "fallback ran"


def test_sync_all_persists_uncaught_account_failure() -> None:
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account()

    with (
        patch.object(svc, "get_linked_accounts", return_value=[account]),
        patch.object(svc, "sync_account_via_refresh", side_effect=KeyError("boom")),
        patch.object(svc, "_record_account_sync_error") as record_error,
    ):
        result = svc.sync_all_linked_accounts(commit_per_account=True)

    assert result["success"] is False
    assert result["accounts_failed"] == 1
    record_error.assert_called_once_with(
        account.bank_account_id,
        "'boom'",
        commit=True,
    )
    db.rollback.assert_called_once()


def test_record_account_sync_error_commits_best_effort_update() -> None:
    db = MagicMock()
    svc = MonoSyncService(db)
    account_id = uuid4()

    svc._record_account_sync_error(account_id, "x" * 1200, commit=True)

    db.execute.assert_called_once()
    db.flush.assert_called_once()
    db.commit.assert_called_once()


def test_get_or_create_statement_recovers_from_concurrent_insert() -> None:
    db = MagicMock()
    svc = MonoSyncService(db)
    account = _account()
    existing_statement = SimpleNamespace(statement_id=uuid4())

    db.scalar.side_effect = [
        None,
        existing_statement,
    ]
    db.flush.side_effect = IntegrityError("insert", {}, Exception("duplicate"))

    result = svc._get_or_create_statement(
        account,
        date(2026, 4, 1),
        date(2026, 4, 15),
        user_id=None,
    )

    assert result is existing_statement
    added_statement = db.add.call_args.args[0]
    assert isinstance(added_statement, BankStatement)


def test_add_statement_line_once_treats_existing_transaction_as_duplicate() -> None:
    db = MagicMock()
    db.flush.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    db.scalar.return_value = uuid4()
    svc = MonoSyncService(db)
    line = BankStatementLine(transaction_id="mono_txn-raced")

    assert svc._add_statement_line_once(line) is False


def test_add_statement_line_once_retries_line_number_collision() -> None:
    db = MagicMock()
    db.flush.side_effect = [
        IntegrityError("insert", {}, Exception("duplicate line number")),
        None,
    ]
    db.scalar.return_value = None
    svc = MonoSyncService(db)
    statement_id = uuid4()
    line = BankStatementLine(
        statement_id=statement_id,
        line_number=11,
        transaction_id="mono_txn-line-race",
    )

    with patch.object(svc, "_get_max_line_number", return_value=41):
        assert svc._add_statement_line_once(line) is True

    assert line.line_number == 42
    assert db.flush.call_count == 2


def test_webhook_missing_secret_reports_service_unavailable() -> None:
    with (
        patch(
            "app.services.finance.banking.mono_sync.resolve_value",
            return_value=None,
        ),
        pytest.raises(RuntimeError) as exc,
    ):
        MonoSyncService(MagicMock()).process_webhook(
            "webhook-secret",
            json.dumps({"event": "mono.events.account_updated"}).encode(),
        )

    assert "not configured" in str(exc.value)


def test_get_all_transactions_caps_pagination() -> None:
    client = MonoClient(MonoConfig(secret_key="secret", public_key="public"))
    response = {
        "data": [
            {
                "id": "txn-1",
                "narration": "Looping page",
                "amount": 10000,
                "type": "credit",
                "balance": 10000,
                "date": "2026-04-15T09:00:00Z",
            }
        ],
        "meta": {"next": "/v2/accounts/mono-account-1/transactions?page=2"},
    }

    with (
        patch.object(client, "_request", return_value=response) as request,
        pytest.raises(MonoError, match="pagination overflow"),
    ):
        client.get_all_transactions("mono-account-1", max_pages=2)

    assert request.call_count == 2


def test_request_wraps_non_json_error_response() -> None:
    client = MonoClient(MonoConfig(secret_key="secret", public_key="public"))
    response = httpx.Response(
        status_code=400,
        content=b"<html>bad request</html>",
        request=httpx.Request("GET", "https://api.withmono.com/test"),
    )
    http = MagicMock()
    http.request.return_value = response

    with (
        patch.object(client, "_get_client", return_value=http),
        pytest.raises(MonoError, match="Mono API error"),
    ):
        client._request("GET", "/test", operation="test")


def test_get_account_info_rejects_missing_or_invalid_balance() -> None:
    client = MonoClient(MonoConfig(secret_key="secret", public_key="public"))

    with (
        patch.object(
            client,
            "_request",
            return_value={"data": {"account": {"id": "mono-account-1"}}},
        ),
        pytest.raises(MonoError, match="missing balance"),
    ):
        client.get_account_info("mono-account-1")

    with (
        patch.object(
            client,
            "_request",
            return_value={
                "data": {"account": {"id": "mono-account-1", "balance": "not-a-number"}}
            },
        ),
        pytest.raises(MonoError, match="invalid balance"),
    ):
        client.get_account_info("mono-account-1")

    with patch.object(
        client,
        "_request",
        return_value={"data": {"account": {"id": "mono-account-1", "balance": 0}}},
    ):
        account_info = client.get_account_info("mono-account-1")

    assert account_info.balance == 0


def test_verify_webhook_rejects_empty_secrets() -> None:
    # compare_digest("", "") returns True; the guard inside verify_webhook
    # must fail closed before that match can leak through to a caller that
    # forgot to check ``is_configured()``.
    configured = MonoClient(MonoConfig(webhook_secret="real-secret"))
    assert configured.verify_webhook("") is False

    unconfigured = MonoClient(MonoConfig(webhook_secret=""))
    assert unconfigured.verify_webhook("") is False
    assert unconfigured.verify_webhook("anything") is False

    assert configured.verify_webhook("real-secret") is True
