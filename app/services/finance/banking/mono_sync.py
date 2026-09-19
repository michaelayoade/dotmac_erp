"""
Mono Sync Service.

Synchronizes bank transactions from Mono Connect with bank statements
for reconciliation.
"""

from __future__ import annotations

import json
import logging
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

# Mono normalizes every transaction timestamp to Lagos-midnight expressed in
# UTC (i.e. T23:00:00Z on day D-1 means "business day D" in Africa/Lagos).
# Consumers that take `.date()` on the raw UTC datetime end up one day behind
# Mono's own UI for every transaction.
_LAGOS = ZoneInfo("Africa/Lagos")

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.domain_settings import SettingDomain
from app.services.domain_settings import AMBIENT
from app.models.finance.banking import (
    BankAccount,
    BankAccountStatus,
    BankStatement,
    BankStatementLine,
    BankStatementStatus,
    MonoTransactionSyncStatus,
    StatementLineType,
)
from app.services.finance.banking.mono_client import (
    MonoAccountInfo,
    MonoClient,
    MonoConfig,
    MonoError,
    MonoTransientError,
    MonoTransaction,
)
from app.services.formatters import format_currency
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)


def _extract_mono_account_id(event_data: dict[str, Any]) -> str | None:
    """Extract the mono account id from a webhook payload.

    Mono's webhook payloads have varied between nested (``data.account._id``)
    and flat (``data.id``) shapes across events and API versions. Try the
    known paths in order and return the first hit.
    """
    account_obj = event_data.get("account") or {}
    if isinstance(account_obj, dict):
        for key in ("_id", "id"):
            value = account_obj.get(key)
            if value:
                return str(value)
    for key in ("_id", "id"):
        value = event_data.get(key)
        if value:
            return str(value)
    return None


def _as_date(value: date | datetime | None) -> date | None:
    """Coerce a date/datetime to a plain ``date``.

    Defensive against older databases, fixtures, and test doubles that may
    still supply ``datetime`` values for account tracking fields.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


def _month_bounds(value: date) -> tuple[date, date]:
    """Return the calendar month containing ``value``."""
    last_day = monthrange(value.year, value.month)[1]
    return date(value.year, value.month, 1), date(value.year, value.month, last_day)


# Mono's indexer reports the outcome of its last upstream-bank pull in
# ``data.meta`` on ``GET /v2/accounts/{id}``. These are the only fields that
# distinguish "the link is alive and we have nothing new" from "the link is
# dead and we are reading a stale cache" — the transactions endpoint answers
# 200 either way.
_DATA_STATUS_FAILED = {"FAILED", "PROCESSING_FAILED"}
_SUCCESSFUL_BANK_PULL_STATUSES = {"SUCCESS", "SUCCESSFUL"}
_SAFE_RETRIEVED_DATA_FIELDS = frozenset(
    {"balance", "identity", "income", "transactions"}
)
_STICKY_MONO_HEALTH = {
    MonoTransactionSyncStatus.reauthorization_required.value,
    MonoTransactionSyncStatus.provider_limited.value,
    MonoTransactionSyncStatus.failed.value,
}


def _safe_retrieved_data(value: object) -> list[str]:
    """Keep only known field-category names from provider metadata."""
    if not isinstance(value, list):
        return []
    return sorted(
        {
            item
            for item in value
            if isinstance(item, str) and item in _SAFE_RETRIEVED_DATA_FIELDS
        }
    )


@dataclass
class MonoSyncResult:
    """Result of a Mono sync operation for one account.

    ``ingestion_state`` distinguishes refresh outcomes from cache ingestion:

    * ``"completed"`` — transactions landed synchronously in this call.
      Reload the page to show them.
    * ``"pending"``  — Mono was asked to re-pull from the bank; the
      ``account_updated`` webhook will drive ingest shortly. The UI
      should *not* reload immediately — there's nothing new yet.
    * ``"failed"``   — the refresh job failed (usually reauth needed).
    * ``"skipped"``  — no refresh was started (for example, rate limited).
    """

    success: bool
    bank_account_id: UUID | None = None
    statement_id: UUID | None = None
    transactions_synced: int = 0
    duplicates_skipped: int = 0
    total_credits: Decimal = Decimal("0")
    total_debits: Decimal = Decimal("0")
    message: str = ""
    errors: list[str] = field(default_factory=list)
    ingestion_state: str = "completed"


class MonoSyncService:
    """
    Service for syncing Mono transactions with bank statements.

    Fetches transactions from linked Mono accounts and creates
    BankStatementLine entries for reconciliation.
    """

    def __init__(
        self,
        db: Session,
        organization_id: UUID | None | Any = AMBIENT,
    ) -> None:
        self.db = db
        self.organization_id = organization_id

    def _get_mono_config(self) -> MonoConfig:
        """Get Mono configuration from domain settings."""
        secret_key = resolve_value(
            self.db,
            SettingDomain.banking,
            "mono_secret_key",
            organization_id=self.organization_id,
        )
        public_key = resolve_value(
            self.db,
            SettingDomain.banking,
            "mono_public_key",
            organization_id=self.organization_id,
        )
        webhook_secret = resolve_value(
            self.db,
            SettingDomain.banking,
            "mono_webhook_secret",
            organization_id=self.organization_id,
        )

        if not secret_key or not public_key:
            raise ValueError("Mono Connect not configured — missing API keys")

        return MonoConfig(
            secret_key=str(secret_key),
            public_key=str(public_key),
            webhook_secret=str(webhook_secret) if webhook_secret else "",
        )

    def is_configured(self) -> bool:
        """Check if Mono Connect is enabled and configured."""
        enabled = resolve_value(
            self.db,
            SettingDomain.banking,
            "mono_enabled",
            organization_id=self.organization_id,
        )
        if not enabled:
            return False
        try:
            self._get_mono_config()
            return True
        except ValueError:
            return False

    @staticmethod
    def _health_status(bank_account: BankAccount) -> str:
        """Return a normalized health value, including for legacy test rows."""
        value = getattr(
            bank_account,
            "mono_transaction_sync_status",
            MonoTransactionSyncStatus.never.value,
        )
        if isinstance(value, MonoTransactionSyncStatus):
            normalized = value.value
        else:
            normalized = str(value or MonoTransactionSyncStatus.never.value)
        if normalized == MonoTransactionSyncStatus.never.value:
            if getattr(bank_account, "mono_link_failed", False):
                return MonoTransactionSyncStatus.failed.value
            if getattr(bank_account, "mono_last_sync_error", None):
                return MonoTransactionSyncStatus.transient_failure.value
        return normalized

    def _transition_sync_health(
        self,
        bank_account: BankAccount,
        status: MonoTransactionSyncStatus,
        *,
        message: str | None = None,
        confirmed_bank_pull: bool = False,
    ) -> bool:
        """Own every Mono health transition.

        A final structural failure survives provider contact, cache reads,
        cached imports, and new refresh requests.  Only an explicit successful
        ``account_updated`` event can move it to ``healthy``.  A later, more
        specific final failure may refine a generic failure, but
        reauthorization remains sticky until that confirmed bank pull.
        """
        current = self._health_status(bank_account)
        target = status.value

        if target == MonoTransactionSyncStatus.healthy.value:
            if not confirmed_bank_pull:
                return False
        elif current == MonoTransactionSyncStatus.reauthorization_required.value:
            if target != current:
                return False
        elif current in _STICKY_MONO_HEALTH and target not in _STICKY_MONO_HEALTH:
            return False

        now = datetime.now(UTC)
        bank_account.mono_transaction_sync_status = target
        bank_account.mono_link_failed = target in _STICKY_MONO_HEALTH
        bank_account.mono_last_sync_error = message
        if confirmed_bank_pull:
            bank_account.mono_last_transaction_sync_at = now
        logger.info(
            "Mono health transition account_id=%s from=%s to=%s evidence=%s",
            bank_account.bank_account_id,
            current,
            target,
            "confirmed_bank_pull" if confirmed_bank_pull else "local_attempt",
        )
        return True

    def _record_provider_contact(self, bank_account: BankAccount) -> None:
        """Record reachability without claiming that the bank link is healthy."""
        bank_account.mono_last_synced_at = datetime.now(UTC)
        if (
            self._health_status(bank_account)
            == MonoTransactionSyncStatus.transient_failure.value
        ):
            restored = (
                MonoTransactionSyncStatus.healthy.value
                if getattr(bank_account, "mono_last_transaction_sync_at", None)
                else MonoTransactionSyncStatus.never.value
            )
            bank_account.mono_transaction_sync_status = restored
            bank_account.mono_last_sync_error = None
            bank_account.mono_link_failed = False

    def _record_transient_failure(
        self,
        bank_account: BankAccount,
        *,
        operation: str,
        status_code: int | None,
    ) -> None:
        """Record a safe transport/API failure without masking link failures."""
        detail = f" during {operation}"
        if status_code is not None:
            detail += f" (HTTP {status_code})"
        message = f"Mono is temporarily unavailable{detail}."
        self._transition_sync_health(
            bank_account,
            MonoTransactionSyncStatus.transient_failure,
            message=message,
        )
        logger.warning(
            "Mono transient failure account_id=%s operation=%s http_status=%s",
            bank_account.bank_account_id,
            operation,
            status_code,
        )

    def link_account(
        self,
        organization_id: UUID,
        bank_account_id: UUID,
        code: str,
    ) -> dict:
        """Exchange a Mono widget code and link it to a bank account."""
        account = self.db.get(BankAccount, bank_account_id)
        if not account or account.organization_id != organization_id:
            raise LookupError("Bank account not found")

        if not self.is_configured():
            raise ValueError("Mono Connect is not configured")

        if not code:
            raise ValueError("Authorization code is required")

        config = self._get_mono_config()
        try:
            with MonoClient(config) as client:
                result = client.exchange_token(code)
                account_info = client.get_account_info(result.account_id)
        except MonoError as exc:
            raise ValueError(exc.message) from exc

        existing_link = self.db.scalar(
            select(BankAccount).where(
                BankAccount.mono_account_id == result.account_id,
                BankAccount.bank_account_id != account.bank_account_id,
            )
        )
        if existing_link is not None:
            raise ValueError(
                "This Mono account is already linked to another bank account"
            )

        # Guard: refuse to link a Mono account whose authoritative identity
        # diverges from the stored bank row. Without this, a user on the
        # wrong account detail page can click "Connect via Mono", pick any
        # institution in the widget, and the link silently lands on the
        # wrong row — see the Zenith/UBA mislabeling incident on 2026-04-15.
        self._assert_mono_account_matches_row(account, account_info)

        account.mono_account_id = result.account_id
        account.mono_last_synced_at = None
        account.mono_last_ingest_at = None
        account.mono_transaction_sync_status = MonoTransactionSyncStatus.never.value
        account.mono_last_transaction_sync_at = None
        account.mono_link_failed = False
        account.mono_last_sync_error = None
        self.db.flush()
        return {
            "status": "success",
            "message": "Bank account linked to Mono successfully",
            "data": {"bank_account_id": str(account.bank_account_id)},
        }

    def trigger_data_refresh(
        self,
        organization_id: UUID,
        bank_account_id: UUID,
    ) -> dict:
        """Ask Mono to re-pull data from the upstream bank.

        Sends a real-time request (``x-realtime: true``) which triggers
        Mono's indexer to do a fresh scrape. When the scrape completes,
        Mono fires an ``account_updated`` webhook that the existing
        handler picks up — so this is fire-and-forget from our side.

        Rate-limited by Mono to one call per account every 5 minutes.
        """
        account = self.db.get(BankAccount, bank_account_id)
        if not account or account.organization_id != organization_id:
            raise LookupError("Bank account not found")

        if not account.mono_account_id:
            raise ValueError("Bank account is not linked to Mono")

        result = self.sync_account_via_refresh(account)
        return {
            "status": "success" if result.success else "warning",
            "message": result.message,
            "data": {
                "ingestion_state": result.ingestion_state,
            },
        }

    def sync_account_by_id(
        self,
        organization_id: UUID,
        bank_account_id: UUID,
        *,
        user_id: UUID | None = None,
    ) -> dict:
        """Incremental Mono sync for a tenant-scoped bank account.

        Leads with a ``trigger_data_refresh`` so "Sync Now" always means
        "ask the bank for the latest" rather than "read Mono's possibly-
        stale indexed cache." Transaction ingest defers to the
        ``account_updated`` webhook on success.
        """
        account = self.db.get(BankAccount, bank_account_id)
        if not account or account.organization_id != organization_id:
            raise LookupError("Bank account not found")

        if not account.mono_account_id:
            raise ValueError("Bank account is not linked to Mono")

        result = self.sync_account_via_refresh(account, user_id=user_id)
        if not result.success:
            details = "; ".join(result.errors) if result.errors else result.message
            raise RuntimeError(details)

        return {
            "status": "success",
            "message": result.message,
            "data": {
                "ingestion_state": result.ingestion_state,
                "transactions_synced": result.transactions_synced,
                "duplicates_skipped": result.duplicates_skipped,
                "total_credits": str(result.total_credits),
                "total_debits": str(result.total_debits),
                "last_statement_balance": (
                    str(account.last_statement_balance)
                    if account.last_statement_balance is not None
                    else None
                ),
                "last_statement_date": (
                    account.last_statement_date.isoformat()
                    if account.last_statement_date
                    else None
                ),
                "mono_last_synced_at": (
                    account.mono_last_synced_at.isoformat()
                    if account.mono_last_synced_at
                    else None
                ),
            },
        }

    def process_webhook(self, header_secret: str, raw_body: bytes) -> dict:
        """Verify and process a Mono webhook payload."""
        from app.db.session_context import allow_cross_org

        if not header_secret:
            raise ValueError("Missing webhook secret")

        # The webhook is unauthenticated, so the tenant is unknown until we
        # inspect the signed payload. Read the global webhook secret without
        # requiring a primed org-filter context.
        with allow_cross_org(self.db):
            configured_secret = resolve_value(
                self.db,
                SettingDomain.banking,
                "mono_webhook_secret",
                organization_id=None,
            )
        if not configured_secret:
            raise RuntimeError("Mono webhook secret not configured")

        config = MonoConfig(webhook_secret=str(configured_secret))
        client = MonoClient(config)
        if not client.verify_webhook(header_secret):
            raise PermissionError("Invalid webhook secret")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON payload") from exc

        event = payload.get("event", "")
        event_data = payload.get("data", {}) or {}
        logger.info("Mono webhook received: event=%s", event)

        if event == "mono.events.account_updated":
            meta = event_data.get("meta") or {}
            data_status = meta.get("data_status", "")
            sync_status = (meta.get("sync_status") or "").upper()
            mono_account_id = _extract_mono_account_id(event_data)
            # retrieved_data is the actionable diagnostic — Mono lists what it
            # successfully fetched ("balance", "transactions", "identity").
            # A FAILED event with retrieved_data=["balance"] means the partner
            # bank serves balance but not transactions for this account type.
            retrieved_data = _safe_retrieved_data(meta.get("retrieved_data"))
            logger.info(
                "Mono account_updated outcome data_status=%s sync_status=%s "
                "job_id=%s has_new_data=%s retrieved_data=%s data_request_id=%s",
                data_status,
                meta.get("sync_status"),
                meta.get("job_id"),
                meta.get("has_new_data"),
                retrieved_data,
                meta.get("data_request_id"),
            )
            # Older events may omit sync_status and can still carry cached
            # transactions, but only an explicit success proves recovery.
            bank_fetch_confirmed = (
                sync_status in _SUCCESSFUL_BANK_PULL_STATUSES
                and data_status == "AVAILABLE"
                and "transactions" in retrieved_data
            )
            # Mono lists what it actually fetched in retrieved_data. Even
            # when sync_status=FAILED, an indexer that successfully fetched
            # transactions has data we can ingest — we should not skip the
            # ingest just because the bank-side scrape stage reported
            # failure, otherwise the user sees a stale error and no new
            # rows even though the cache holds them.
            retrieved_has_transactions = "transactions" in retrieved_data
            if bank_fetch_confirmed and mono_account_id:
                self._record_webhook_success(mono_account_id, meta)
            elif mono_account_id and (
                (sync_status and sync_status not in _SUCCESSFUL_BANK_PULL_STATUSES)
                or data_status in _DATA_STATUS_FAILED
            ):
                self._record_webhook_failure(mono_account_id, meta)

            # Queue ingest whenever Mono claims data is AVAILABLE *or* when
            # the bank-side scrape reported failure but transactions were
            # still retrieved into the indexer. Ingestion never changes the
            # provider-final outcome recorded above.
            if mono_account_id and (
                data_status == "AVAILABLE" or retrieved_has_transactions
            ):
                from app.tasks.finance import sync_mono_account

                sync_mono_account.delay(mono_account_id)
        elif event == "mono.events.account_connected":
            logger.info("Mono account_connected received")
        elif event == "mono.events.account_reauthorized":
            # Mono fires this when the user completes the Connect widget with
            # a reauth_token. It's informational — the real signal (whether
            # the fresh data pull succeeded) comes in the follow-up
            # account_updated event, so we just log and move on.
            logger.info(
                "Mono account_reauthorized received; awaiting confirmed "
                "account_updated bank-pull outcome"
            )
        elif event == "mono.accounts.jobs.update":
            # Async job state updates from Mono's indexer (queued, running,
            # completed, failed). We don't act on these — the authoritative
            # outcome comes via account_updated — but log explicit fields
            # so operators can see job lifecycle without grepping raw JSON.
            logger.info(
                "Mono job update: status=%s job_id=%s data_request_id=%s",
                event_data.get("status"),
                event_data.get("job_id") or event_data.get("id"),
                event_data.get("data_request_id"),
            )
        else:
            logger.info("Unhandled Mono event: %s", event)

        return {"status": "success", "message": f"Webhook {event} processed"}

    @staticmethod
    def _normalize_account_number(value: str | None) -> str:
        """Strip whitespace/punctuation from an account number for comparison.

        Mono returns raw digits; operator-entered values sometimes include
        dashes or spaces. Normalize both sides before matching.
        """
        if not value:
            return ""
        return "".join(ch for ch in value if ch.isdigit())

    def _assert_mono_account_matches_row(
        self,
        account: BankAccount,
        account_info: MonoAccountInfo,
    ) -> None:
        """Refuse the link if Mono's authoritative identity diverges.

        Compares ``bank_code``, ``account_number``, and ``currency`` against
        the stored row. Free-text ``bank_name`` is skipped in favor of the
        canonical CBN code so bank-name spelling variations ("UBA" vs
        "United Bank for Africa") don't produce false rejections.

        On any mismatch, raises ``ValueError`` with a diff listing every
        differing field so the operator can tell exactly which side is
        wrong (usually the stored row, since Mono is the source of truth).
        """
        mismatches: list[str] = []

        stored_bank_code = (account.bank_code or "").strip()
        mono_bank_code = (account_info.bank_code or "").strip()
        if stored_bank_code and mono_bank_code and stored_bank_code != mono_bank_code:
            mismatches.append(
                f"bank_code: stored={stored_bank_code!r} Mono={mono_bank_code!r}"
            )

        stored_number = self._normalize_account_number(account.account_number)
        mono_number = self._normalize_account_number(account_info.account_number)
        if stored_number and mono_number and stored_number != mono_number:
            mismatches.append(
                f"account_number: stored={stored_number!r} Mono={mono_number!r}"
            )

        stored_currency = (account.currency_code or "").upper().strip()
        mono_currency = (account_info.currency or "").upper().strip()
        if stored_currency and mono_currency and stored_currency != mono_currency:
            mismatches.append(
                f"currency: stored={stored_currency!r} Mono={mono_currency!r}"
            )

        if not mismatches:
            return

        stored_bank_name = account.bank_name or "(unset)"
        mono_bank_name = account_info.institution_name or "(unset)"
        raise ValueError(
            "Mono account identity does not match this bank row. "
            f"Stored: {stored_bank_name}. Mono: {mono_bank_name}. "
            "Differences: " + "; ".join(mismatches) + ". "
            "Fix the bank row (or pick the correct one) and retry."
        )

    def _record_webhook_success(
        self, mono_account_id: str, meta: dict[str, Any]
    ) -> None:
        """Record the only event that proves a reauthorized link recovered."""
        from app.db.session_context import allow_cross_org, prime_tenant_context

        with allow_cross_org(self.db):
            bank_account = self.db.scalar(
                select(BankAccount).where(
                    BankAccount.mono_account_id == mono_account_id
                )
            )
        if bank_account is None:
            logger.warning("Mono success webhook received for an unlinked account")
            return

        prime_tenant_context(self.db, bank_account.organization_id)
        self._transition_sync_health(
            bank_account,
            MonoTransactionSyncStatus.healthy,
            confirmed_bank_pull=True,
        )
        self.db.flush()
        logger.info(
            "Mono bank pull confirmed account_id=%s outcome=healthy "
            "job_id=%s data_request_id=%s",
            bank_account.bank_account_id,
            meta.get("job_id"),
            meta.get("data_request_id"),
        )

    def _record_webhook_failure(
        self, mono_account_id: str, meta: dict[str, Any]
    ) -> None:
        """Persist a Mono data-refresh failure against the linked account.

        Mono sends ``data_status=FAILED`` on its ``account_updated`` webhook
        when its own worker failed to refresh the upstream account — commonly
        expired credentials, re-auth required, or a provider-side error. If
        we silently drop this, the next manual or scheduled sync returns 200
        with zero new transactions (because ``/accounts/{id}/transactions``
        serves from Mono's cache) and the user sees a false-positive success.
        Recording it on ``mono_last_sync_error`` surfaces it in the UI
        health banner alongside API-level failures.
        """
        from app.db.session_context import allow_cross_org, prime_tenant_context

        # Cross-org lookup: mono_account_id alone, no tenant context yet.
        with allow_cross_org(self.db):
            bank_account = self.db.scalar(
                select(BankAccount).where(
                    BankAccount.mono_account_id == mono_account_id
                )
            )
        if bank_account is None:
            logger.warning("Mono failure webhook received for an unlinked account")
            return
        prime_tenant_context(self.db, bank_account.organization_id)
        job_id = meta.get("job_id") or "unknown"
        sync_status = str(meta.get("sync_status") or "FAILED").upper()
        retrieved_data = _safe_retrieved_data(meta.get("retrieved_data"))
        data_request_id = meta.get("data_request_id")

        # A provider-final failure outranks any cache ingest that happened
        # while the asynchronous bank pull was running.  Cache data can be
        # useful without proving that the bank link is healthy.
        # Webhooks inconsistently populate data_request_id (observed missing
        # on fresh relink follow-ups). When absent, pull the authoritative
        # value from GET /v2/accounts/{id} — it's cheap, and without it the
        # stored error banner is useless for Mono support tickets.
        if not data_request_id:
            try:
                config = self._get_mono_config()
                with MonoClient(config) as client:
                    account_info = client.get_account_info(mono_account_id)
                if account_info.data_request_id:
                    data_request_id = account_info.data_request_id
                if not retrieved_data and account_info.retrieved_data:
                    retrieved_data = _safe_retrieved_data(account_info.retrieved_data)
            except (MonoError, ValueError):
                logger.debug(
                    "Could not enrich Mono failure metadata account_id=%s",
                    bank_account.bank_account_id,
                )

        data_request_id_display = data_request_id or "unknown"

        # Reauth-required is the most common failure in practice and the
        # only one the user can fix themselves. Give them the exact action
        # to take rather than a generic "data refresh failed" message that
        # leaves them looking at Mono internals.
        if sync_status == "REAUTHORISATION_REQUIRED":
            health_status = MonoTransactionSyncStatus.reauthorization_required
            error_message = (
                "Bank connection expired. Reauthorise this account via the "
                "Mono Connect widget — the bank is no longer accepting "
                "Mono's stored credentials, and only cached transactions "
                "are being served. "
                f"Reference Mono data_request_id={data_request_id_display}."
            )
        # Distinguish "indexer fetched balance but not transactions" from a
        # blanket failure. The first pattern is usually a partner-bank
        # limitation for the account type (e.g. USD domiciliary accounts on
        # some Nigerian banks). Tell the operator exactly that so they know
        # reauth won't help and manual upload is the fallback.
        elif retrieved_data and "transactions" not in retrieved_data:
            health_status = MonoTransactionSyncStatus.provider_limited
            error_message = (
                f"Mono retrieved {retrieved_data} but not transactions "
                f"for this account. This is usually a partner-bank limitation "
                f"for the account type (e.g. USD domiciliary). Fall back to "
                f"manual statement upload. "
                f"Reference Mono data_request_id={data_request_id_display} if "
                f"contacting support@mono.co."
            )
        else:
            health_status = MonoTransactionSyncStatus.failed
            error_message = (
                f"Mono data refresh failed "
                f"(sync_status={sync_status}, job_id={job_id}, "
                f"data_request_id={data_request_id_display})"
            )
        # Mark the link itself as failed, not just the last attempt. Mono keeps
        # answering 200 from its cache on a dead link, so without this the next
        # scheduled cache read would clear the banner and the account would go
        # green while ingesting nothing.
        self._transition_sync_health(
            bank_account,
            health_status,
            message=error_message,
        )
        self.db.flush()
        logger.warning(
            "Mono data refresh failed account_id=%s "
            "sync_status=%s job_id=%s retrieved_data=%s "
            "data_request_id=%s",
            bank_account.bank_account_id,
            sync_status,
            job_id,
            retrieved_data,
            data_request_id_display,
        )

    def sync_by_mono_account_id(self, mono_account_id: str) -> MonoSyncResult:
        """Incremental sync for a single Mono-linked account.

        Used by the webhook-triggered Celery task — the webhook is
        unauthenticated and carries no organization context, so we look the
        account up globally by its unique mono_account_id. Delegates to
        ``sync_account_incremental`` so webhook-triggered and user-triggered
        syncs share the same stateful window logic.
        """
        from app.db.session_context import allow_cross_org, prime_tenant_context

        # mono_account_id → org_id is the resolution we're doing right here.
        # The planned do_orm_execute listener can't filter by org because we
        # don't know it yet; wrap so it doesn't raise MissingOrgContextError.
        with allow_cross_org(self.db):
            bank_account = self.db.scalar(
                select(BankAccount).where(
                    BankAccount.mono_account_id == mono_account_id
                )
            )
        if not bank_account:
            logger.warning("Mono webhook received for an unlinked account")
            return MonoSyncResult(
                success=False,
                message="No bank account is linked to this Mono connection",
            )

        # Now we know the tenant. Prime the session so subsequent queries in
        # sync_account_incremental (statement-line inserts, balance updates)
        # see the right org context.
        prime_tenant_context(self.db, bank_account.organization_id)
        return self.sync_account_incremental(bank_account, user_id=None)

    def get_linked_accounts(
        self, organization_id: UUID | None = None
    ) -> list[BankAccount]:
        """Get all bank accounts linked to Mono."""
        stmt = select(BankAccount).where(
            BankAccount.mono_account_id.isnot(None),
            BankAccount.status == BankAccountStatus.active,
        )
        if organization_id:
            stmt = stmt.where(BankAccount.organization_id == organization_id)
        return list(self.db.scalars(stmt).all())

    def sync_account_incremental(
        self,
        bank_account: BankAccount,
        *,
        user_id: UUID | None = None,
    ) -> MonoSyncResult:
        """Stateful incremental sync, rewound by ``mono_sync_buffer_days``.

        The cursor is the newest transaction date **already imported from
        Mono** — not ``max()`` over all statement lines. A manual CSV import
        is not a superset of what Mono would have returned, so letting it
        advance the Mono cursor silently strands every un-ingested Mono day
        behind it: upload one line dated today while the Mono link is broken,
        and the intervening weeks are never requested again. Manual lines
        still seed the *first* Mono window (nothing Mono-sourced exists yet
        to resume from), which preserves the "don't re-scan imported history"
        intent without the data-loss edge.

        The window is then rewound by ``mono_sync_buffer_days`` (default 7).
        Banks post back-dated transactions routinely — NGN card settlement,
        cheque clearing, weekend batches land with a value date one or more
        days in the past. Without a rewind, anything Mono backfills *behind*
        the cursor falls into no future window and is lost permanently. The
        rewind is free: dedupe is keyed on Mono's transaction id and backed
        by a unique index, so re-fetching an overlap writes nothing.

        Falls back to ``mono_sync_from_date`` (or 90 days) when the account
        has no statement lines at all.

        Always updates ``mono_last_synced_at`` on a successful call, even
        when zero transactions came back — that's how integration health is
        distinguished from "haven't tried in a while." On failure, records
        the error in ``mono_last_sync_error`` without touching the data.
        """
        if not bank_account.mono_account_id:
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message="Bank account not linked to Mono",
            )

        end_date = date.today()

        # Prefer the Mono-sourced cursor; fall back to any statement line
        # only to seed the very first Mono pull on this account.
        cursor = self._get_mono_transaction_cursor(bank_account.bank_account_id)
        if cursor is None:
            cursor = self._get_newest_line_date(bank_account.bank_account_id)

        if cursor is not None:
            # A future-dated line (mis-parsed CSV, post-dated cheque) must not
            # push the cursor past today and collapse the window to [today,
            # today], which would skip every prior day forever.
            cursor = min(_as_date(cursor) or end_date, end_date)
            buffer_days = max(int(bank_account.mono_sync_buffer_days or 0), 0)
            start_date = cursor - timedelta(days=buffer_days)
        else:
            configured_start = _as_date(bank_account.mono_sync_from_date)
            start_date = configured_start or (end_date - timedelta(days=90))

        if start_date > end_date:
            start_date = end_date

        return self._sync_window(
            bank_account,
            start_date,
            end_date,
            user_id=user_id,
        )

    def sync_account_via_refresh(
        self,
        bank_account: BankAccount,
        *,
        user_id: UUID | None = None,
    ) -> MonoSyncResult:
        """Request an asynchronous bank pull without inventing its outcome.

        The trigger response and the immediate account-info read are provider
        contact only.  The later ``account_updated`` webhook is the sole
        evidence that the upstream bank pull completed successfully.
        """
        del user_id
        if not bank_account.mono_account_id:
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message="Bank account not linked to Mono",
                ingestion_state="failed",
            )
        mono_account_id = bank_account.mono_account_id

        config = self._get_mono_config()
        account_info: MonoAccountInfo | None = None
        try:
            with MonoClient(config) as client:
                refresh = client.trigger_data_refresh(mono_account_id)
                self._record_provider_contact(bank_account)
                try:
                    account_info = client.get_account_info(mono_account_id)
                except MonoError as exc:
                    logger.warning(
                        "Mono account-info unavailable after refresh request "
                        "account_id=%s http_status=%s",
                        bank_account.bank_account_id,
                        exc.status_code,
                    )
        except MonoTransientError as exc:
            if exc.status_code == 429:
                logger.info(
                    "Mono refresh skipped account_id=%s reason=rate_limited",
                    bank_account.bank_account_id,
                )
                return MonoSyncResult(
                    success=True,
                    bank_account_id=bank_account.bank_account_id,
                    message="Mono refresh was rate limited; cached data may still import.",
                    ingestion_state="skipped",
                )
            self._record_transient_failure(
                bank_account,
                operation="refresh request",
                status_code=exc.status_code,
            )
            self.db.flush()
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message="Mono refresh request failed temporarily.",
                errors=["mono refresh request failed"],
                ingestion_state="failed",
            )
        except MonoError as exc:
            self._transition_sync_health(
                bank_account,
                MonoTransactionSyncStatus.failed,
                message="Mono rejected the bank refresh request.",
            )
            self.db.flush()
            logger.warning(
                "Mono refresh rejected account_id=%s http_status=%s",
                bank_account.bank_account_id,
                exc.status_code,
            )
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message="Mono rejected the bank refresh request.",
                errors=["mono refresh request rejected"],
                ingestion_state="failed",
            )

        if account_info is not None:
            self._apply_account_info_watermarks(bank_account, account_info)

        if refresh.job_status == "failed":
            self._transition_sync_health(
                bank_account,
                MonoTransactionSyncStatus.failed,
                message="Mono reported that the latest bank refresh failed.",
            )
            self.db.flush()
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message="Mono reported that the latest bank refresh failed.",
                errors=["mono refresh job failed"],
                ingestion_state="failed",
            )

        self._transition_sync_health(
            bank_account,
            MonoTransactionSyncStatus.pending,
        )
        self.db.flush()
        return MonoSyncResult(
            success=True,
            bank_account_id=bank_account.bank_account_id,
            transactions_synced=0,
            message=(
                "Bank refresh requested; awaiting Mono's completion webhook. "
                "Any existing integration failure remains until recovery is confirmed."
            ),
            ingestion_state="pending",
        )

    def sync_account_for_scheduled_sweep(
        self,
        bank_account: BankAccount,
        *,
        user_id: UUID | None = None,
    ) -> MonoSyncResult:
        """Request a refresh and independently drain Mono's current cache."""
        refresh_result = self.sync_account_via_refresh(
            bank_account,
            user_id=user_id,
        )
        cache_result = self.sync_account_incremental(
            bank_account,
            user_id=user_id,
        )

        errors = [*refresh_result.errors, *cache_result.errors]
        success = refresh_result.success and cache_result.success
        outcome = refresh_result.ingestion_state
        if not success:
            outcome = "failed"

        if cache_result.transactions_synced:
            cache_summary = (
                f" Imported {cache_result.transactions_synced} cached transactions."
            )
        else:
            cache_summary = " No cached transactions were imported."

        return MonoSyncResult(
            success=success,
            bank_account_id=bank_account.bank_account_id,
            statement_id=cache_result.statement_id,
            transactions_synced=cache_result.transactions_synced,
            duplicates_skipped=cache_result.duplicates_skipped,
            total_credits=cache_result.total_credits,
            total_debits=cache_result.total_debits,
            message=refresh_result.message + cache_summary,
            errors=errors,
            ingestion_state=outcome,
        )

    def _settle_sync_health(
        self,
        bank_account: BankAccount,
        account_info: MonoAccountInfo | None,
        *,
        ingested: int,
    ) -> None:
        """Record cache-ingestion facts without deciding bank-pull success."""
        if ingested > 0:
            bank_account.mono_last_ingest_at = datetime.now(UTC)
        if account_info is None:
            return

        self._record_provider_contact(bank_account)
        if (account_info.data_status or "").upper() not in _DATA_STATUS_FAILED:
            return

        retrieved = account_info.retrieved_data or []
        if retrieved and "transactions" not in retrieved:
            self._transition_sync_health(
                bank_account,
                MonoTransactionSyncStatus.provider_limited,
                message=(
                    "Mono's latest bank pull returned balance data without "
                    "transaction history."
                ),
            )
        else:
            self._transition_sync_health(
                bank_account,
                MonoTransactionSyncStatus.failed,
                message="Mono reported that its latest bank pull failed.",
            )

    def _apply_account_info_watermarks(
        self,
        bank_account: BankAccount,
        account_info: MonoAccountInfo,
    ) -> None:
        """Advance balance + freshness from ``/v2/accounts/{id}``.

        Mirrors the forward-only ``as-of-today`` semantics used inside
        :meth:`_sync_window` for zero-transaction responses so the
        refresh-first path and the direct-pull path can never disagree
        about where the watermark sits.

        This path ingests nothing by design — it hands off to the
        ``account_updated`` webhook — so it advances ``mono_last_synced_at``
        (we did reach Mono) but never ``mono_last_ingest_at``. Health is
        settled from evidence; see :meth:`_settle_sync_health`.
        """
        bank_account.last_statement_balance = account_info.balance_major
        as_of_date = date.today()
        existing = _as_date(bank_account.last_statement_date)
        if existing is None or as_of_date > existing:
            bank_account.last_statement_date = as_of_date
        self._record_provider_contact(bank_account)

    def _sync_window(
        self,
        bank_account: BankAccount,
        from_date: date,
        to_date: date,
        *,
        user_id: UUID | None = None,
    ) -> MonoSyncResult:
        """Core range-based pull. Shared by incremental and explicit gap fill.

        Fetches Mono's current account info (authoritative balance), then
        pulls transactions for ``[from_date, to_date]``, dedupes against
        existing ``mono_<id>`` lines, and advances the account state.
        """
        mono_account_id = bank_account.mono_account_id
        if mono_account_id is None:
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message="Bank account not linked to Mono",
            )

        config = self._get_mono_config()

        # Format dates for Mono API (DD-MM-YYYY)
        start_str = from_date.strftime("%d-%m-%Y")
        end_str = to_date.strftime("%d-%m-%Y")

        account_info: MonoAccountInfo | None = None
        try:
            with MonoClient(config) as client:
                account_info = client.get_account_info(mono_account_id)
                all_transactions = client.get_all_transactions(
                    mono_account_id,
                    start=start_str,
                    end=end_str,
                )
        except MonoTransientError as exc:
            self._record_transient_failure(
                bank_account,
                operation="cache read",
                status_code=exc.status_code,
            )
            self.db.flush()
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message="Mono cache read failed temporarily.",
                errors=["mono cache read failed"],
                ingestion_state="failed",
            )
        except MonoError:
            self._transition_sync_health(
                bank_account,
                MonoTransactionSyncStatus.failed,
                message="Mono returned invalid account or transaction data.",
            )
            self.db.flush()
            logger.error(
                "Mono cache returned invalid data account_id=%s",
                bank_account.bank_account_id,
            )
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message="Mono returned invalid account or transaction data.",
                errors=["invalid Mono data"],
                ingestion_state="failed",
            )

        count = 0
        duplicates = 0
        total_credits = Decimal("0")
        total_debits = Decimal("0")
        statement_id: UUID | None = None

        new_transactions: list[tuple[MonoTransaction, str, date]] = []
        parsed_transaction_dates: list[date] = []
        try:
            if all_transactions:
                existing_ids = self._get_existing_transaction_ids(
                    bank_account.bank_account_id
                )

                for txn in all_transactions:
                    # Reject transactions without an id before forming the
                    # dedupe key. Without this guard, every id-less txn
                    # collapses to "mono_None" and all but the first are
                    # silently dropped by the unique-index dedupe path —
                    # data loss disguised as deduplication.
                    if not txn.id:
                        raise MonoError("Mono transaction is missing an id")
                    try:
                        parsed_date = self._parse_date(txn.date)
                    except MonoError as exc:
                        raise MonoError(
                            f"{exc.message} (transaction_id={txn.id})"
                        ) from exc
                    parsed_transaction_dates.append(parsed_date)
                    mono_txn_id = f"mono_{txn.id}"
                    if mono_txn_id in existing_ids:
                        duplicates += 1
                        continue
                    new_transactions.append((txn, mono_txn_id, parsed_date))
        except MonoError:
            logger.error(
                "Mono transaction validation failed account_id=%s",
                bank_account.bank_account_id,
            )
            self._transition_sync_health(
                bank_account,
                MonoTransactionSyncStatus.failed,
                message="Mono returned invalid transaction data.",
            )
            self.db.flush()
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message="Mono returned invalid transaction data.",
                errors=["invalid Mono transaction data"],
                ingestion_state="failed",
            )

        try:
            count, duplicates, total_credits, total_debits, statement_id = (
                self._write_transactions(
                    bank_account,
                    new_transactions,
                    duplicates=duplicates,
                    user_id=user_id,
                )
            )
        except MonoError:
            logger.error(
                "Mono transaction import failed account_id=%s",
                bank_account.bank_account_id,
            )
            self._transition_sync_health(
                bank_account,
                MonoTransactionSyncStatus.failed,
                message="Mono transactions could not be imported safely.",
            )
            self.db.flush()
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message="Mono transactions could not be imported safely.",
                errors=["Mono transaction import failed"],
                ingestion_state="failed",
            )

        return self._finalise_window(
            bank_account,
            account_info,
            from_date=from_date,
            to_date=to_date,
            count=count,
            duplicates=duplicates,
            total_credits=total_credits,
            total_debits=total_debits,
            statement_id=statement_id,
            parsed_transaction_dates=parsed_transaction_dates,
        )

    def _write_transactions(
        self,
        bank_account: BankAccount,
        new_transactions: list[tuple[MonoTransaction, str, date]],
        *,
        duplicates: int,
        user_id: UUID | None,
    ) -> tuple[int, int, Decimal, Decimal, UUID | None]:
        """Write new Mono lines, bucketed into one statement per calendar month."""
        count = 0
        total_credits = Decimal("0")
        total_debits = Decimal("0")
        statement_id: UUID | None = None

        if new_transactions:
            transactions_by_month: dict[
                tuple[int, int], list[tuple[MonoTransaction, str, date]]
            ] = {}
            for txn, mono_txn_id, parsed_date in new_transactions:
                transactions_by_month.setdefault(
                    (parsed_date.year, parsed_date.month), []
                ).append((txn, mono_txn_id, parsed_date))

            for year_month in sorted(transactions_by_month):
                bucket_transactions = transactions_by_month[year_month]
                period_start, period_end = _month_bounds(bucket_transactions[0][2])
                statement = self._get_or_create_statement(
                    account=bank_account,
                    period_start=period_start,
                    period_end=period_end,
                    user_id=user_id,
                )
                statement_id = statement.statement_id
                line_number = self._get_max_line_number(statement.statement_id)
                statement_credits = Decimal("0")
                statement_debits = Decimal("0")
                statement_count = 0

                for txn, mono_txn_id, parsed_date in bucket_transactions:
                    amount = txn.amount_major
                    is_credit = txn.type.lower() == "credit"
                    line_type = (
                        StatementLineType.credit
                        if is_credit
                        else StatementLineType.debit
                    )

                    if len(txn.narration) > 500:
                        logger.debug(
                            "Truncated Mono narration from %d chars account_id=%s",
                            len(txn.narration),
                            bank_account.bank_account_id,
                        )
                    line_number += 1
                    line = BankStatementLine(
                        line_id=uuid4(),
                        statement_id=statement.statement_id,
                        line_number=line_number,
                        transaction_id=mono_txn_id,
                        transaction_date=parsed_date,
                        value_date=parsed_date,
                        transaction_type=line_type,
                        amount=amount,
                        running_balance=txn.balance_major,
                        description=txn.narration[:500],
                        reference=txn.id,
                        payee_payer="",
                        is_matched=False,
                        raw_data={
                            "mono_id": txn.id,
                            "mono_type": txn.type,
                            "mono_amount_kobo": txn.amount,
                            "mono_balance_kobo": txn.balance,
                            "mono_category": txn.category,
                            "mono_narration": txn.narration,
                            "import_source": "mono",
                        },
                        created_at=datetime.now(UTC),
                    )
                    if not self._add_statement_line_once(line):
                        duplicates += 1
                        continue

                    line_number = max(line_number, line.line_number)
                    count += 1
                    statement_count += 1
                    if is_credit:
                        total_credits += amount
                        statement_credits += amount
                    else:
                        total_debits += amount
                        statement_debits += amount

                statement.total_credits += statement_credits
                statement.total_debits += statement_debits
                statement.total_lines += statement_count
                statement.unmatched_lines += statement_count
                # Mono statements are transaction containers. The account-level
                # balance is authoritative; statement-level balances are left
                # unset to avoid presenting stale arithmetic balances.
                statement.closing_balance = None

        return count, duplicates, total_credits, total_debits, statement_id

    def _finalise_window(
        self,
        bank_account: BankAccount,
        account_info: MonoAccountInfo | None,
        *,
        from_date: date,
        to_date: date,
        count: int,
        duplicates: int,
        total_credits: Decimal,
        total_debits: Decimal,
        statement_id: UUID | None,
        parsed_transaction_dates: list[date],
    ) -> MonoSyncResult:
        """Advance watermarks, settle sync health, and build the result."""
        # Forward-only watermark advance. Use the newest *transaction* date
        # from the Mono response, not `to_date`, so a stale or empty
        # response can never move the watermark past where data really
        # exists.
        newest_txn_date = (
            min(max(parsed_transaction_dates), date.today())
            if parsed_transaction_dates
            else None
        )
        existing_mono_watermark = _as_date(bank_account.mono_last_transaction_date)
        if newest_txn_date is not None and (
            existing_mono_watermark is None or newest_txn_date > existing_mono_watermark
        ):
            bank_account.mono_last_transaction_date = newest_txn_date

        # last_statement_date + last_statement_balance are an as-of pair.
        # When Mono returns a fresh account-info, the balance is authoritative
        # *as of today*, so the pair advances to today together. Otherwise
        # fall back to the newest imported txn date. Forward-only — a zero-
        # transaction sync cannot regress an already-recorded as-of date.
        if account_info is not None:
            bank_account.last_statement_balance = account_info.balance_major
            as_of_date: date | None = date.today()
        else:
            as_of_date = newest_txn_date

        existing_statement_watermark = _as_date(bank_account.last_statement_date)
        if as_of_date is not None and (
            existing_statement_watermark is None
            or as_of_date > existing_statement_watermark
        ):
            bank_account.last_statement_date = as_of_date

        # Freshness: every successful API call advances this, even with zero
        # new transactions. It means "Mono answered", nothing more — health
        # is settled separately, from evidence that data actually flowed.
        self._settle_sync_health(bank_account, account_info, ingested=count)

        self.db.flush()

        logger.info(
            "Mono cache ingest account_id=%s window=%s..%s imported=%d "
            "duplicates=%d health=%s",
            bank_account.bank_account_id,
            from_date,
            to_date,
            count,
            duplicates,
            self._health_status(bank_account),
        )

        currency_code = (
            getattr(bank_account, "currency_code", None)
            or settings.default_functional_currency_code
        )
        credits_str = format_currency(total_credits, currency_code)
        debits_str = format_currency(total_debits, currency_code)
        balance_str = (
            format_currency(account_info.balance_major, currency_code)
            if account_info
            else "n/a"
        )
        if count == 0:
            if account_info is not None:
                msg = (
                    f"Up to date with Mono's cache. Balance: {balance_str}. "
                    f"No new transactions in window {from_date}..{to_date}."
                )
            else:
                msg = f"No new cached transactions in window {from_date}..{to_date}."
        else:
            msg = (
                f"Imported {count} new cached transactions "
                f"({credits_str} credits, {debits_str} debits). "
                f"Balance: {balance_str}."
            )

        return MonoSyncResult(
            success=True,
            bank_account_id=bank_account.bank_account_id,
            statement_id=statement_id,
            transactions_synced=count,
            duplicates_skipped=duplicates,
            total_credits=total_credits,
            total_debits=total_debits,
            message=msg,
        )

    def sync_all_linked_accounts(
        self,
        user_id: UUID | None = None,
        *,
        commit_per_account: bool = False,
    ) -> dict[str, object]:
        """Incremental sync for every Mono-linked bank account.

        Called by the Celery beat task on a schedule. Each account computes
        its own window from its own watermark. Each account runs inside a
        savepoint so one tenant/account failure cannot poison sibling syncs.
        """
        accounts = sorted(
            self.get_linked_accounts(),
            key=lambda account: (
                str(account.organization_id),
                str(account.bank_account_id),
            ),
        )
        if not accounts:
            return {
                "success": True,
                "completed": 0,
                "pending": 0,
                "failed": 0,
                "skipped": 0,
                "transactions_imported": 0,
                "duplicates_skipped": 0,
                "message": "No Mono-linked bank accounts found",
            }

        results: list[MonoSyncResult] = []
        for account in accounts:
            account_id = account.bank_account_id
            try:
                with self.db.begin_nested():
                    result = self.sync_account_for_scheduled_sweep(
                        account,
                        user_id=user_id,
                    )
                results.append(result)
                if commit_per_account:
                    self.db.commit()
            except Exception as exc:
                if commit_per_account:
                    self.db.rollback()
                safe_error = exc.__class__.__name__
                self._record_account_sync_error(
                    account_id,
                    safe_error,
                    commit=commit_per_account,
                )
                logger.error(
                    "Mono scheduled account failed account_id=%s exception_type=%s",
                    account_id,
                    safe_error,
                )
                results.append(
                    MonoSyncResult(
                        success=False,
                        bank_account_id=account_id,
                        message="Mono account synchronization failed.",
                        errors=[safe_error],
                        ingestion_state="failed",
                    )
                )

        failed = sum(1 for result in results if not result.success)

        return {
            "success": failed == 0,
            "completed": sum(
                1
                for result in results
                if result.success and result.ingestion_state == "completed"
            ),
            "pending": sum(
                1
                for result in results
                if result.success and result.ingestion_state == "pending"
            ),
            "failed": failed,
            "skipped": sum(
                1
                for result in results
                if result.success and result.ingestion_state == "skipped"
            ),
            "transactions_imported": sum(
                result.transactions_synced for result in results
            ),
            "duplicates_skipped": sum(result.duplicates_skipped for result in results),
            "errors": [error for result in results for error in result.errors],
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_existing_transaction_ids(self, bank_account_id: UUID) -> set[str]:
        """Get all Mono transaction IDs already imported for this account."""
        return set(
            self.db.scalars(
                select(BankStatementLine.transaction_id)
                .join(
                    BankStatement,
                    BankStatementLine.statement_id == BankStatement.statement_id,
                )
                .where(
                    BankStatement.bank_account_id == bank_account_id,
                    BankStatementLine.transaction_id.isnot(None),
                    BankStatementLine.transaction_id.startswith("mono_"),
                )
            ).all()
        )

    def _get_newest_line_date(self, bank_account_id: UUID) -> date | None:
        """Return the newest transaction date across ALL statement lines for
        this bank account, regardless of source (manual or Mono).

        This is the cursor for incremental sync — the next Mono pull starts
        at the newest known line date so we never re-scan history already
        imported from any source, and manual imports naturally advance the
        resume point for subsequent Mono syncs.
        """
        return self.db.scalar(
            select(func.max(BankStatementLine.transaction_date))
            .join(
                BankStatement,
                BankStatementLine.statement_id == BankStatement.statement_id,
            )
            .where(BankStatement.bank_account_id == bank_account_id)
        )

    def _get_mono_transaction_cursor(self, bank_account_id: UUID) -> date | None:
        """Return newest transaction date already imported from Mono."""
        return self.db.scalar(
            select(func.max(BankStatementLine.transaction_date))
            .join(
                BankStatement,
                BankStatementLine.statement_id == BankStatement.statement_id,
            )
            .where(
                BankStatement.bank_account_id == bank_account_id,
                BankStatementLine.transaction_id.isnot(None),
                BankStatementLine.transaction_id.startswith("mono_"),
            )
        )

    def _get_max_line_number(self, statement_id: UUID) -> int:
        """Get the highest line number in a statement."""
        result = self.db.scalar(
            select(func.coalesce(func.max(BankStatementLine.line_number), 0)).where(
                BankStatementLine.statement_id == statement_id
            )
        )
        return int(result) if result else 0

    def _record_account_sync_error(
        self,
        bank_account_id: UUID,
        message: str,
        *,
        commit: bool = False,
    ) -> None:
        """Best-effort persistence for unexpected per-account sync failures."""
        try:
            with self.db.begin_nested():
                self.db.execute(
                    update(BankAccount)
                    .where(BankAccount.bank_account_id == bank_account_id)
                    .values(
                        mono_transaction_sync_status=(
                            MonoTransactionSyncStatus.failed.value
                        ),
                        mono_link_failed=True,
                        mono_last_sync_error=message[:1000],
                    )
                )
                self.db.flush()
            if commit:
                self.db.commit()
        except Exception:
            if commit:
                self.db.rollback()
            logger.exception(
                "Failed to persist Mono sync error for account %s",
                bank_account_id,
            )

    def _get_or_create_statement(
        self,
        account: BankAccount,
        period_start: date,
        period_end: date,
        user_id: UUID | None,
    ) -> BankStatement:
        """Get existing monthly Mono statement or create one for the period."""
        statement_number = f"MONO-{period_start.strftime('%Y%m')}"

        existing = self.db.scalar(
            select(BankStatement)
            .where(
                BankStatement.bank_account_id == account.bank_account_id,
                BankStatement.statement_number == statement_number,
            )
            .with_for_update()
        )

        if existing:
            return existing

        try:
            with self.db.begin_nested():
                statement = BankStatement(
                    statement_id=uuid4(),
                    organization_id=account.organization_id,
                    bank_account_id=account.bank_account_id,
                    statement_number=statement_number,
                    statement_date=period_end,
                    period_start=period_start,
                    period_end=period_end,
                    opening_balance=None,
                    closing_balance=None,
                    total_credits=Decimal("0"),
                    total_debits=Decimal("0"),
                    currency_code=(
                        getattr(account, "currency_code", None)
                        or settings.default_functional_currency_code
                    ),
                    status=BankStatementStatus.imported,
                    import_source="mono",
                    imported_at=datetime.now(UTC),
                    imported_by=user_id,
                    total_lines=0,
                    matched_lines=0,
                    unmatched_lines=0,
                    created_at=datetime.now(UTC),
                )
                self.db.add(statement)
                self.db.flush()
        except IntegrityError:
            existing = self.db.scalar(
                select(BankStatement)
                .where(
                    BankStatement.bank_account_id == account.bank_account_id,
                    BankStatement.statement_number == statement_number,
                )
                .with_for_update()
            )
            if existing:
                return existing
            raise

        return statement

    def _add_statement_line_once(
        self,
        line: BankStatementLine,
        *,
        max_line_number_retries: int = 3,
    ) -> bool:
        """Insert a statement line, treating raced Mono duplicates as no-ops."""
        for attempt in range(max_line_number_retries + 1):
            try:
                with self.db.begin_nested():
                    self.db.add(line)
                    self.db.flush()
                return True
            except IntegrityError:
                if line.transaction_id:
                    owner = self._transaction_id_owner(line.transaction_id)
                    if owner is not None:
                        target = self.db.scalar(
                            select(BankStatement.bank_account_id).where(
                                BankStatement.statement_id == line.statement_id
                            )
                        )
                        if owner == target:
                            logger.info(
                                "Skipped duplicate Mono statement line account_id=%s",
                                target,
                            )
                            return False

                        # The unique index on `transaction_id` is GLOBAL, but the
                        # pre-insert dedupe check is scoped to this bank account.
                        # So a Mono transaction already imported against a
                        # *different* bank row collides here and used to be
                        # swallowed as a "duplicate" — the sync then reported
                        # success having imported nothing.
                        #
                        # That is exactly what happens after unlink/re-link onto
                        # the correct bank row (the recovery from the Zenith/UBA
                        # mislabeling): every historical line collides, all are
                        # counted as duplicates, and the correct account silently
                        # ends up empty. Fail loudly — the old lines must be moved
                        # or removed first.
                        raise MonoError(
                            f"Mono transaction {line.transaction_id} is already "
                            f"imported against bank account {owner} — refusing to "
                            f"import it against {target}. The same Mono account "
                            "was most likely re-linked to a different bank row; "
                            "move or delete the statement lines on the old row "
                            "before syncing."
                        )

                if attempt >= max_line_number_retries:
                    raise

                line.line_number = self._get_max_line_number(line.statement_id) + 1

        return True

    def _transaction_id_owner(self, transaction_id: str) -> UUID | None:
        """Bank account that already holds this provider transaction id, if any."""
        return self.db.scalar(
            select(BankStatement.bank_account_id)
            .join(
                BankStatementLine,
                BankStatementLine.statement_id == BankStatement.statement_id,
            )
            .where(BankStatementLine.transaction_id == transaction_id)
        )

    @staticmethod
    def _parse_date(date_str: str) -> date:
        """Parse a Mono ISO 8601 date string to a date object.

        Mono ships every transaction at ``T23:00:00.000Z`` — Lagos midnight in
        UTC — so the UTC date is one day behind the date the bank (and the
        Mono UI) attribute the transaction to. Convert to Africa/Lagos before
        truncating, otherwise every transaction reads as the prior day.
        """
        if not date_str:
            raise MonoError("Mono transaction is missing a transaction date")
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.astimezone(_LAGOS).date()
        except (ValueError, AttributeError) as exc:
            raise MonoError(
                f"Mono transaction has invalid transaction date: {date_str!r}"
            ) from exc
