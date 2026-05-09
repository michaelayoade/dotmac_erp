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

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.domain_settings import SettingDomain
from app.models.finance.banking import (
    BankAccount,
    BankAccountStatus,
    BankStatement,
    BankStatementLine,
    BankStatementStatus,
    StatementLineType,
)
from app.services.finance.banking.mono_client import (
    MonoAccountInfo,
    MonoClient,
    MonoConfig,
    MonoDataRefreshResult,
    MonoError,
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


@dataclass
class MonoSyncResult:
    """Result of a Mono sync operation for one account.

    ``ingestion_state`` distinguishes the four outcomes the UI needs to
    render differently:

    * ``"completed"`` — transactions landed synchronously in this call.
      Reload the page to show them.
    * ``"pending"``  — Mono was asked to re-pull from the bank; the
      ``account_updated`` webhook will drive ingest shortly. The UI
      should *not* reload immediately — there's nothing new yet.
    * ``"current"``  — Mono confirms the bank has no new data. No
      webhook is coming; nothing will change on reload.
    * ``"failed"``   — the refresh job failed (usually reauth needed).
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

    def __init__(self, db: Session) -> None:
        self.db = db

    def _get_mono_config(self) -> MonoConfig:
        """Get Mono configuration from domain settings."""
        secret_key = resolve_value(self.db, SettingDomain.banking, "mono_secret_key")
        public_key = resolve_value(self.db, SettingDomain.banking, "mono_public_key")
        webhook_secret = resolve_value(
            self.db, SettingDomain.banking, "mono_webhook_secret"
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
        enabled = resolve_value(self.db, SettingDomain.banking, "mono_enabled")
        if not enabled:
            return False
        try:
            self._get_mono_config()
            return True
        except ValueError:
            return False

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
        account.mono_last_sync_error = None
        self.db.flush()
        return {
            "status": "success",
            "message": "Bank account linked to Mono successfully",
            "data": {"mono_account_id": result.account_id},
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

        config = self._get_mono_config()
        try:
            with MonoClient(config) as client:
                result = client.trigger_data_refresh(account.mono_account_id)
        except MonoError as exc:
            raise RuntimeError(f"Mono data refresh failed: {exc.message}") from exc

        if result.job_status == "failed":
            return {
                "status": "warning",
                "message": (
                    "Mono reported a failed refresh job. "
                    "This may resolve on retry, or the account may need "
                    "reauthorisation."
                ),
                "data": {
                    "has_new_data": result.has_new_data,
                    "job_id": result.job_id,
                    "job_status": result.job_status,
                },
            }

        if result.job_status == "finished" and not result.has_new_data:
            return {
                "status": "success",
                "message": (
                    "Mono confirms no new data from the bank. "
                    "Transactions are up to date."
                ),
                "data": {
                    "has_new_data": False,
                    "job_id": result.job_id,
                    "job_status": result.job_status,
                },
            }

        # "processing" or "finished" with new data — webhook will follow
        return {
            "status": "success",
            "message": (
                "Data refresh requested. New transactions will appear "
                "shortly when Mono completes the bank pull."
            ),
            "data": {
                "has_new_data": result.has_new_data,
                "job_id": result.job_id,
                "job_status": result.job_status,
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
        ``account_updated`` webhook on success; falls back to a direct
        cache pull when Mono rate-limits the refresh.
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
        if not header_secret:
            raise ValueError("Missing webhook secret")

        configured_secret = resolve_value(
            self.db,
            SettingDomain.banking,
            "mono_webhook_secret",
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
            retrieved_data = meta.get("retrieved_data") or []
            logger.info(
                "Mono account_updated: mono_id=%s data_status=%s sync_status=%s "
                "job_id=%s has_new_data=%s retrieved_data=%s data_request_id=%s",
                mono_account_id,
                data_status,
                meta.get("sync_status"),
                meta.get("job_id"),
                meta.get("has_new_data"),
                retrieved_data,
                meta.get("data_request_id"),
            )
            # sync_status=FAILED means Mono's upstream bank scrape failed
            # even when data_status=AVAILABLE (Mono can still serve cached
            # data from a prior successful scrape). Record the error before
            # the AVAILABLE branch so the banner appears — and *don't* queue
            # a sync, because the direct-pull path clears mono_last_sync_error
            # on success and would race-wipe the banner we just set. Any
            # undrained cache will be picked up by the next successful
            # scrape's account_updated event.
            if sync_status == "FAILED" and mono_account_id:
                self._record_webhook_failure(mono_account_id, meta)
            elif data_status == "AVAILABLE" and mono_account_id:
                from app.tasks.finance import sync_mono_account

                sync_mono_account.delay(mono_account_id)
            elif data_status in {"FAILED", "PROCESSING_FAILED"} and mono_account_id:
                self._record_webhook_failure(mono_account_id, meta)
        elif event == "mono.events.account_connected":
            logger.info(
                "Mono account_connected: mono_id=%s",
                _extract_mono_account_id(event_data),
            )
        elif event == "mono.events.account_reauthorized":
            # Mono fires this when the user completes the Connect widget with
            # a reauth_token. It's informational — the real signal (whether
            # the fresh data pull succeeded) comes in the follow-up
            # account_updated event, so we just log and move on.
            logger.info(
                "Mono account_reauthorized: mono_id=%s — awaiting follow-up "
                "account_updated event for data_status",
                _extract_mono_account_id(event_data),
            )
        elif event == "mono.accounts.jobs.update":
            # Async job state updates from Mono's indexer (queued, running,
            # completed, failed). We don't act on these — the authoritative
            # outcome comes via account_updated — but log explicit fields
            # so operators can see job lifecycle without grepping raw JSON.
            logger.info(
                "Mono job update: mono_id=%s status=%s job_id=%s data_request_id=%s",
                _extract_mono_account_id(event_data),
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
        bank_account = self.db.scalar(
            select(BankAccount).where(BankAccount.mono_account_id == mono_account_id)
        )
        if bank_account is None:
            logger.warning(
                "Mono FAILED webhook for unlinked account mono_id=%s", mono_account_id
            )
            return
        job_id = meta.get("job_id") or "unknown"
        sync_status = meta.get("sync_status") or "FAILED"
        retrieved_data = list(meta.get("retrieved_data") or [])
        data_request_id = meta.get("data_request_id")

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
                    retrieved_data = list(account_info.retrieved_data)
            except (MonoError, ValueError) as exc:
                logger.debug(
                    "Could not enrich Mono failure metadata from account_info: %s",
                    exc,
                )

        data_request_id_display = data_request_id or "unknown"

        # Distinguish "indexer fetched balance but not transactions" from a
        # blanket failure. The first pattern is usually a partner-bank
        # limitation for the account type (e.g. USD domiciliary accounts on
        # some Nigerian banks). Tell the operator exactly that so they know
        # reauth won't help and manual upload is the fallback.
        if retrieved_data and "transactions" not in retrieved_data:
            error_message = (
                f"Mono retrieved {retrieved_data} but not transactions "
                f"for this account. This is usually a partner-bank limitation "
                f"for the account type (e.g. USD domiciliary). Fall back to "
                f"manual statement upload. "
                f"Reference Mono data_request_id={data_request_id_display} if "
                f"contacting support@mono.co."
            )
        else:
            error_message = (
                f"Mono data refresh failed "
                f"(sync_status={sync_status}, job_id={job_id}, "
                f"data_request_id={data_request_id_display})"
            )
        bank_account.mono_last_sync_error = error_message
        self.db.flush()
        logger.warning(
            "Mono data refresh failed for account %s "
            "(mono_id=%s sync_status=%s job_id=%s retrieved_data=%s "
            "data_request_id=%s)",
            bank_account.bank_account_id,
            mono_account_id,
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
        bank_account = self.db.scalar(
            select(BankAccount).where(BankAccount.mono_account_id == mono_account_id)
        )
        if not bank_account:
            logger.warning(
                "Mono webhook for unlinked account %s",
                mono_account_id,
            )
            return MonoSyncResult(
                success=False,
                message=f"No bank account linked to mono_account_id={mono_account_id}",
            )

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
        """Stateful incremental sync using the newest statement line as the cursor.

        The window is ``[max(statement_line.transaction_date), today]`` across
        all lines on this bank account — Mono *and* manually imported. Picking
        the newest known line means both sources advance the same cursor, so
        a Mono outage self-heals on the next successful run and a manual
        import naturally advances the resume point for subsequent Mono syncs.

        Falls back to a 90-day window only when the account has no statement
        lines at all (first-ever sync of a brand-new account).

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

        newest_line_date = self._get_newest_line_date(bank_account.bank_account_id)
        if newest_line_date is not None:
            start_date = newest_line_date
        else:
            start_date = date.today() - timedelta(days=90)
        end_date = date.today()
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
        """Scheduled-path sync: ask Mono to re-pull from the upstream bank.

        Mono's ``/v2/accounts/{id}/transactions`` endpoint serves Mono's
        internal indexed cache — without a ``trigger_data_refresh`` call the
        cache never advances and every scheduled run finds "nothing new"
        even when the bank has new lines. This method fires the refresh
        and lets the resulting ``account_updated`` webhook drive
        transaction ingest via :meth:`sync_by_mono_account_id` (which
        reuses the direct-pull path against the now-fresh cache).

        Always refreshes ``last_statement_balance`` and
        ``mono_last_synced_at`` from the authoritative
        ``/v2/accounts/{id}`` response so the dashboard shows current
        balance and freshness even while the webhook is pending.

        Falls back to :meth:`sync_account_incremental` when Mono
        rate-limits the refresh (one per 5 min per account) so cycles
        don't silently skip ingest of lines already sitting in the cache.
        """
        if not bank_account.mono_account_id:
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message="Bank account not linked to Mono",
            )
        mono_account_id = bank_account.mono_account_id

        config = self._get_mono_config()
        refresh: MonoDataRefreshResult | None = None
        account_info: MonoAccountInfo | None = None
        try:
            with MonoClient(config) as client:
                try:
                    refresh = client.trigger_data_refresh(mono_account_id)
                except MonoError as exc:
                    logger.info(
                        "Mono refresh unavailable for account %s (%s); "
                        "falling back to direct pull",
                        bank_account.bank_account_id,
                        exc.message,
                    )
                    return self.sync_account_incremental(bank_account, user_id=user_id)
                account_info = client.get_account_info(mono_account_id)
        except MonoError as exc:
            logger.error(
                "Mono sync failed for account %s: %s",
                bank_account.bank_account_id,
                exc.message,
            )
            bank_account.mono_last_sync_error = exc.message
            self.db.flush()
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message=f"Mono API error: {exc.message}",
                errors=[exc.message],
            )

        self._apply_account_info_watermarks(bank_account, account_info)

        if refresh.job_status == "failed":
            bank_account.mono_last_sync_error = (
                "Mono reported a failed bank pull; account may need reauthorisation."
            )
            self.db.flush()
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message=("Mono bank pull failed; account may need reauthorisation."),
                errors=["mono refresh job failed"],
                ingestion_state="failed",
            )

        # "processing"    → webhook arrives when scrape completes.
        # "finished" + new → webhook follows with a freshly-indexed cache.
        # "finished" + no  → nothing at the bank; no webhook; done.
        # job_status None  → Mono didn't echo the header; treat as
        #                    processing so the webhook path still wins
        #                    if one arrives.
        deferring_to_webhook = refresh.job_status in (
            None,
            "processing",
        ) or (refresh.job_status == "finished" and refresh.has_new_data)

        self.db.flush()
        if deferring_to_webhook:
            msg = (
                "Bank is syncing. New transactions will appear shortly "
                "(usually within 30 seconds)."
            )
            ingestion_state = "pending"
        else:
            msg = "Up to date. Mono reports no new transactions at the bank."
            ingestion_state = "current"
        return MonoSyncResult(
            success=True,
            bank_account_id=bank_account.bank_account_id,
            transactions_synced=0,
            message=msg,
            ingestion_state=ingestion_state,
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
        """
        bank_account.last_statement_balance = account_info.balance_major
        as_of_date = date.today()
        existing = _as_date(bank_account.last_statement_date)
        if existing is None or as_of_date > existing:
            bank_account.last_statement_date = as_of_date
        bank_account.mono_last_synced_at = datetime.now(UTC)
        bank_account.mono_last_sync_error = None

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
        except MonoError as exc:
            logger.error(
                "Mono sync failed for account %s: %s",
                bank_account.bank_account_id,
                exc.message,
            )
            bank_account.mono_last_sync_error = exc.message
            self.db.flush()
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message=f"Mono API error: {exc.message}",
                errors=[exc.message],
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
        except MonoError as exc:
            logger.error(
                "Mono sync returned invalid data for account %s: %s",
                bank_account.bank_account_id,
                exc.message,
            )
            bank_account.mono_last_sync_error = exc.message
            self.db.flush()
            return MonoSyncResult(
                success=False,
                bank_account_id=bank_account.bank_account_id,
                message=f"Mono data error: {exc.message}",
                errors=[exc.message],
            )

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
                            "Truncated Mono narration from %d chars for "
                            "transaction_id=%s",
                            len(txn.narration),
                            txn.id,
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

        # Freshness: every successful API call advances this, even with
        # zero new transactions. Clears any previously recorded error.
        bank_account.mono_last_synced_at = datetime.now(UTC)
        bank_account.mono_last_sync_error = None

        self.db.flush()

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
        logger.info(
            "Mono sync complete for %s: window=%s..%s, %d new, %d duplicates, "
            "credits=%s, debits=%s, balance=%s",
            bank_account.display_name,
            from_date,
            to_date,
            count,
            duplicates,
            credits_str,
            debits_str,
            balance_str,
        )

        if count == 0:
            if account_info is not None:
                msg = (
                    f"Up to date. Balance: {balance_str}. "
                    f"No new transactions in window {from_date}..{to_date}."
                )
            else:
                msg = f"No new transactions in window {from_date}..{to_date}."
        else:
            msg = (
                f"Synced {count} new transactions "
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
                "accounts_synced": 0,
                "message": "No Mono-linked bank accounts found",
            }

        results: list[MonoSyncResult] = []
        for account in accounts:
            account_id = account.bank_account_id
            try:
                with self.db.begin_nested():
                    result = self.sync_account_via_refresh(account, user_id=user_id)
                results.append(result)
                if commit_per_account:
                    self.db.commit()
            except Exception as exc:
                if commit_per_account:
                    self.db.rollback()
                self._record_account_sync_error(
                    account_id,
                    str(exc) or exc.__class__.__name__,
                    commit=commit_per_account,
                )
                logger.exception("Failed to sync Mono account %s", account_id)
                results.append(
                    MonoSyncResult(
                        success=False,
                        bank_account_id=account_id,
                        message=str(exc),
                        errors=[str(exc)],
                    )
                )

        total_synced = sum(r.transactions_synced for r in results)
        total_errors = sum(len(r.errors) for r in results)
        successful = sum(1 for r in results if r.success)

        return {
            "success": total_errors == 0,
            "accounts_synced": successful,
            "accounts_failed": len(results) - successful,
            "total_transactions": total_synced,
            "total_errors": total_errors,
            "errors": [e for r in results for e in r.errors],
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
                    .values(mono_last_sync_error=message[:1000])
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
                if line.transaction_id and self.db.scalar(
                    select(BankStatementLine.line_id).where(
                        BankStatementLine.transaction_id == line.transaction_id
                    )
                ):
                    logger.info(
                        "Skipped duplicate Mono statement line transaction_id=%s",
                        line.transaction_id,
                    )
                    return False
                if attempt >= max_line_number_retries:
                    raise

                line.line_number = self._get_max_line_number(line.statement_id) + 1

        return True

    @staticmethod
    def _parse_date(date_str: str) -> date:
        """Parse a Mono ISO 8601 date string to a date object."""
        if not date_str:
            raise MonoError("Mono transaction is missing a transaction date")
        # Mono returns ISO 8601: "2023-12-14T00:02:00.500Z"
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.date()
        except (ValueError, AttributeError) as exc:
            raise MonoError(
                f"Mono transaction has invalid transaction date: {date_str!r}"
            ) from exc
