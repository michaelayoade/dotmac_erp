"""
dotmac_sub Sync Tasks — Celery background tasks for the dotmac_sub integration.

Replaces the legacy Splynx tasks. 3-tier sync strategy:
  Tier 1 (every 30 min): Incremental — all entities, hash-skip unchanged.
  Tier 2 (nightly 1 AM):  Reconciliation — open invoices + recent payments.
  Tier 3 (Sunday 2 AM):   Full reconciliation — every entity, no filters.

Per-org credentials resolve via DotmacSubConfig.for_org (UI-managed
IntegrationConfig → env fallback).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

try:
    from datetime import UTC  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    UTC = timezone.utc  # type: ignore[assignment]

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session_context import cross_org_session, session_for_org
from app.models.finance.ar.customer import Customer
from app.models.finance.core_org.organization import Organization
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.sync import SyncHistory, SyncJobStatus, SyncType
from app.services.dotmac_sub import (
    SYSTEM_USER_ID,
    DotmacSubConfig,
    DotmacSubSyncService,
)

logger = logging.getLogger(__name__)

_SOURCE = "dotmac_sub"


def _resolve_org_id(explicit_org_id: str | None) -> UUID | None:
    org_id_str = explicit_org_id or settings.default_organization_id
    if not org_id_str:
        return None
    try:
        return UUID(org_id_str)
    except ValueError:
        return None


def _resolve_ar_control_account(db: Session, organization_id: UUID) -> UUID | None:
    account_id = db.scalar(
        select(Account.account_id).where(
            Account.organization_id == organization_id,
            Account.is_active.is_(True),
            Account.is_posting_allowed.is_(True),
            Account.subledger_type == "AR",
        )
    )
    if account_id:
        return account_id
    account_id = db.scalar(
        select(Account.account_id).where(
            Account.organization_id == organization_id,
            Account.account_code == "1400",
            Account.is_active.is_(True),
        )
    )
    if account_id:
        return account_id
    return db.scalar(
        select(Customer.ar_control_account_id).where(
            Customer.organization_id == organization_id
        )
    )


def _resolve_default_revenue_account(db: Session, organization_id: UUID) -> UUID | None:
    account_id = db.scalar(
        select(Account.account_id)
        .join(AccountCategory, AccountCategory.category_id == Account.category_id)
        .where(
            Account.organization_id == organization_id,
            Account.is_active.is_(True),
            Account.is_posting_allowed.is_(True),
            AccountCategory.ifrs_category == IFRSCategory.REVENUE,
        )
        .order_by(Account.account_code.asc())
    )
    if account_id:
        return account_id
    account_id = db.scalar(
        select(Account.account_id).where(
            Account.organization_id == organization_id,
            Account.account_code == "4000",
            Account.is_active.is_(True),
        )
    )
    if account_id:
        return account_id
    return db.scalar(
        select(Customer.default_revenue_account_id).where(
            Customer.organization_id == organization_id,
            Customer.default_revenue_account_id.is_not(None),
        )
    )


def _sum_synced(results: list[Any]) -> int:
    return sum((r.created + r.updated) for r in results)


def _sum_skipped(results: list[Any]) -> int:
    return sum(r.skipped for r in results)


def _sum_total(results: list[Any]) -> int:
    return sum((r.created + r.updated + r.skipped) for r in results)


def _collect_errors(results: list[Any]) -> list[str]:
    errors: list[str] = []
    for result in results:
        errors.extend(result.errors)
    return errors


def _build_sync_context(
    db: Session,
    organization_id: str | None,
    sync_type: SyncType,
    entity_types: list[str],
) -> tuple[DotmacSubSyncService, UUID, UUID] | dict[str, Any]:
    """Shared setup: resolve org + accounts, create SyncHistory."""
    org_id = _resolve_org_id(organization_id)
    if not org_id:
        return {"success": False, "error": "No valid organization ID configured"}

    if not DotmacSubConfig.for_org(db, org_id).is_configured():
        return {"success": False, "error": "dotmac_sub integration is not configured"}

    org = db.get(Organization, org_id)
    if not org or not org.is_active:
        return {"success": False, "error": "Organization not found or inactive"}

    ar_control_account_id = _resolve_ar_control_account(db, org_id)
    if not ar_control_account_id:
        return {"success": False, "error": "AR control account could not be resolved"}

    revenue_account_id = _resolve_default_revenue_account(db, org_id)
    if not revenue_account_id:
        return {
            "success": False,
            "error": "Default revenue account could not be resolved",
        }

    history = SyncHistory(
        organization_id=org_id,
        source_system=_SOURCE,
        sync_type=sync_type,
        entity_types=entity_types,
        created_by_user_id=SYSTEM_USER_ID,
    )
    db.add(history)
    db.flush()
    history.start()
    db.flush()
    history_id = history.history_id

    service = DotmacSubSyncService(
        db=db,
        organization_id=org_id,
        ar_control_account_id=ar_control_account_id,
        default_revenue_account_id=revenue_account_id,
    )
    return service, history_id, org_id


def _finalize_sync(
    db: Session,
    history_id: UUID,
    sync_results: list[Any],
    *,
    org_id: UUID | None = None,
) -> dict[str, Any]:
    history_fresh = db.get(SyncHistory, history_id)
    if not history_fresh:
        logger.error("SyncHistory %s disappeared during sync", history_id)
        return {"success": False, "error": "SyncHistory record lost"}

    history_fresh.total_records = _sum_total(sync_results)
    history_fresh.synced_count = _sum_synced(sync_results)
    history_fresh.skipped_count = _sum_skipped(sync_results)
    for error in _collect_errors(sync_results)[:100]:
        history_fresh.add_error(_SOURCE, "sync", error)
    history_fresh.complete()

    summary = {
        "success": history_fresh.error_count == 0,
        "history_id": str(history_id),
        "organization_id": str(org_id) if org_id else None,
        "synced_count": history_fresh.synced_count,
        "skipped_count": history_fresh.skipped_count,
        "error_count": history_fresh.error_count,
    }
    db.commit()
    return summary


def _handle_sync_failure(
    history_id: UUID, org_id: UUID, exc: BaseException, tier_name: str
) -> None:
    logger.exception("%s dotmac_sub sync failed for org %s", tier_name, org_id)
    with session_for_org(org_id) as db2:
        history2 = db2.get(SyncHistory, history_id)
        if history2:
            history2.fail(str(exc))
            db2.commit()


# Webhook events we subscribe to in dotmac_sub.
_WEBHOOK_EVENTS = [
    "subscriber.created",
    "subscriber.updated",
    "subscriber.suspended",
    "subscriber.reactivated",
    "invoice.created",
    "invoice.sent",
    "invoice.paid",
    "invoice.overdue",
    "payment.received",
    "payment.refunded",
]


@shared_task
def bootstrap_dotmac_sub_webhooks(
    organization_id: str | None = None,
    callback_url: str | None = None,
) -> dict[str, Any]:
    """Register our inbound webhook endpoint + event subscriptions in dotmac_sub.

    Idempotent on the dotmac_sub side is not guaranteed, so this is intended as
    a one-shot setup task (run manually or from the admin UI). ``callback_url``
    must be the public URL of this app's ``/dotmac-sub/webhook`` route.
    """
    from app.services.dotmac_sub import DotmacSubClient

    org_id = _resolve_org_id(organization_id)
    if org_id is None:
        return {"success": False, "error": "No valid organization ID configured"}

    url = callback_url or (
        f"{settings.app_url.rstrip('/')}/dotmac-sub/webhook" if settings.app_url else ""
    )
    if not url:
        return {"success": False, "error": "callback_url is required"}

    with session_for_org(org_id) as db:
        config = DotmacSubConfig.for_org(db, org_id)
        if not config.is_configured():
            return {
                "success": False,
                "error": "dotmac_sub integration is not configured",
            }

        with DotmacSubClient(config) as client:
            endpoint = client._request(
                "POST",
                "/webhooks/endpoints",
                json={
                    "name": "DotMac ERP AR sync",
                    "url": url,
                    "secret": settings.dotmac_sub_webhook_secret or "",
                    "is_active": True,
                },
            )
            endpoint_id = (
                str(endpoint.get("id", "")) if isinstance(endpoint, dict) else ""
            )
            created = []
            for event in _WEBHOOK_EVENTS:
                try:
                    client._request(
                        "POST",
                        "/webhooks/subscriptions",
                        json={
                            "endpoint_id": endpoint_id,
                            "event_type": event,
                            "is_active": True,
                        },
                    )
                    created.append(event)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed subscribing to %s: %s", event, e)

    return {
        "success": True,
        "endpoint_id": endpoint_id,
        "subscribed_events": created,
        "callback_url": url,
    }


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------


@shared_task
def cleanup_stale_dotmac_sub_sync_history(
    stale_after_minutes: int = 180,
    limit: int = 500,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Mark stale RUNNING dotmac_sub SyncHistory rows as FAILED."""
    stale_after_minutes = max(stale_after_minutes, 1)
    limit = max(limit, 1)
    now_utc = datetime.now(UTC)
    cutoff = now_utc - timedelta(minutes=stale_after_minutes)

    with cross_org_session() as db:
        stmt = (
            select(SyncHistory)
            .where(
                SyncHistory.source_system == _SOURCE,
                SyncHistory.status == SyncJobStatus.RUNNING,
                SyncHistory.started_at.is_not(None),
                SyncHistory.started_at < cutoff,
            )
            .order_by(SyncHistory.started_at.asc())
            .limit(limit)
        )
        stale_rows = list(db.scalars(stmt).all())
        if not stale_rows:
            return {"success": True, "checked": 0, "marked_failed": 0}

        marked = 0
        for row in stale_rows:
            started_at = row.started_at or now_utc
            age = int((now_utc - started_at).total_seconds() // 60)
            row.fail(
                f"Marked FAILED by maintenance: stale RUNNING row (age={age}m, "
                f"threshold={stale_after_minutes}m)."
            )
            marked += 1

        if dry_run:
            db.rollback()
            return {"success": True, "checked": len(stale_rows), "would_mark": marked}

        db.commit()
        logger.info(
            "Marked %d stale dotmac_sub RUNNING sync_history rows FAILED", marked
        )
        return {"success": True, "checked": len(stale_rows), "marked_failed": marked}


# ---------------------------------------------------------------------------
# Tier 1 — Incremental (every 30 minutes)
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def run_dotmac_sub_incremental_sync(
    self: Any,
    organization_id: str | None = None,
    batch_size: int = 2000,
) -> dict[str, Any]:
    """Incremental sync — all entities, hash-skip unchanged."""
    entity_types = [
        "resellers",
        "subscribers",
        "invoices",
        "payments",
        "credit_notes",
    ]
    org_id = _resolve_org_id(organization_id)
    if org_id is None:
        return {"success": False, "error": "No valid organization ID configured"}

    with session_for_org(org_id) as db:
        ctx = _build_sync_context(db, str(org_id), SyncType.INCREMENTAL, entity_types)
        if isinstance(ctx, dict):
            return ctx
        service, history_id, org_id = ctx
        try:
            resellers = service.sync_resellers(created_by_user_id=SYSTEM_USER_ID)
            subscribers = service.sync_subscribers(
                created_by_user_id=SYSTEM_USER_ID, batch_size=batch_size
            )
            invoices = service.sync_invoices(
                created_by_user_id=SYSTEM_USER_ID, batch_size=batch_size
            )
            payments = service.sync_payments(
                created_by_user_id=SYSTEM_USER_ID, batch_size=batch_size
            )
            credit_notes = service.sync_credit_notes(
                created_by_user_id=SYSTEM_USER_ID, batch_size=batch_size
            )
            invoice_post = service.post_unposted_invoices(
                created_by_user_id=SYSTEM_USER_ID
            )
            if invoice_post["errors"]:
                invoices.errors.extend(invoice_post["errors"][:100])
            post = service.post_unposted_payments(created_by_user_id=SYSTEM_USER_ID)
            if post["errors"]:
                payments.errors.extend(post["errors"][:100])
            return _finalize_sync(
                db,
                history_id,
                [resellers, subscribers, invoices, payments, credit_notes],
                org_id=org_id,
            )
        except Exception as exc:
            db.rollback()
            _handle_sync_failure(history_id, org_id, exc, "Incremental")
            raise self.retry(exc=exc)
        finally:
            service.close()


# ---------------------------------------------------------------------------
# Tier 2 — Daily reconciliation (1 AM)
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=2, default_retry_delay=600)
def run_dotmac_sub_daily_reconciliation(
    self: Any,
    organization_id: str | None = None,
    batch_size: int = 5000,
) -> dict[str, Any]:
    """Reconcile open invoices + recent settled payments."""
    entity_types = ["invoices_reconciliation", "payments_reconciliation"]
    org_id = _resolve_org_id(organization_id)
    if org_id is None:
        return {"success": False, "error": "No valid organization ID configured"}

    with session_for_org(org_id) as db:
        ctx = _build_sync_context(db, str(org_id), SyncType.INCREMENTAL, entity_types)
        if isinstance(ctx, dict):
            return ctx
        service, history_id, org_id = ctx
        try:
            results = []
            for status in ("issued", "partially_paid", "overdue"):
                results.append(
                    service.sync_invoices(
                        status=status,
                        created_by_user_id=SYSTEM_USER_ID,
                        batch_size=batch_size,
                    )
                )
            payments = service.sync_payments(
                status="succeeded",
                created_by_user_id=SYSTEM_USER_ID,
                batch_size=batch_size,
            )
            results.append(payments)
            invoice_post = service.post_unposted_invoices(
                created_by_user_id=SYSTEM_USER_ID
            )
            if invoice_post["errors"]:
                results[0].errors.extend(invoice_post["errors"][:100])
            post = service.post_unposted_payments(created_by_user_id=SYSTEM_USER_ID)
            if post["errors"]:
                payments.errors.extend(post["errors"][:100])
            return _finalize_sync(db, history_id, results, org_id=org_id)
        except Exception as exc:
            db.rollback()
            _handle_sync_failure(history_id, org_id, exc, "Daily reconciliation")
            raise self.retry(exc=exc)
        finally:
            service.close()


# ---------------------------------------------------------------------------
# Tier 3 — Full reconciliation (Sunday 2 AM)
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=1, default_retry_delay=900)
def run_dotmac_sub_full_reconciliation(
    self: Any,
    organization_id: str | None = None,
    batch_size: int = 10000,
) -> dict[str, Any]:
    """Full reconciliation — every entity, no filters."""
    entity_types = ["full_reconciliation"]
    org_id = _resolve_org_id(organization_id)
    if org_id is None:
        return {"success": False, "error": "No valid organization ID configured"}

    with session_for_org(org_id) as db:
        ctx = _build_sync_context(db, str(org_id), SyncType.FULL, entity_types)
        if isinstance(ctx, dict):
            return ctx
        service, history_id, org_id = ctx
        try:
            full = service.sync_all(
                created_by_user_id=SYSTEM_USER_ID, batch_size=batch_size
            )
            return _finalize_sync(
                db,
                history_id,
                [
                    full.resellers,
                    full.subscribers,
                    full.invoices,
                    full.payments,
                    full.credit_notes,
                ],
                org_id=org_id,
            )
        except Exception as exc:
            db.rollback()
            _handle_sync_failure(history_id, org_id, exc, "Full reconciliation")
            raise self.retry(exc=exc)
        finally:
            service.close()


@shared_task(
    bind=True,
    max_retries=8,
    autoretry_for=(),
    retry_backoff=True,
    retry_backoff_max=3600,
    retry_jitter=True,
    rate_limit="30/m",
)
def process_dotmac_sub_webhook(
    self,
    organization_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Process one dotmac_sub webhook event asynchronously.

    The webhook endpoint ACKs immediately and enqueues here so the synchronous
    read-back into dotmac_sub (which its rate limiter throttles under bursts)
    happens off the request path, paced by this task's rate_limit and retried
    locally with backoff on DotmacSubRateLimitError. Retry exhaustion is safe:
    the scheduled incremental sync remains the authoritative backstop, and the
    handler is idempotent (upsert + change-hash + journal-gated posting).
    """
    from app.services.dotmac_sub.client import DotmacSubRateLimitError
    from app.services.dotmac_sub.webhook_dispatch import dispatch_webhook

    org_id = UUID(organization_id)
    try:
        with session_for_org(org_id) as db:
            return dispatch_webhook(db, org_id, event_type, payload)
    except DotmacSubRateLimitError as exc:
        raise self.retry(exc=exc)
    except Exception:
        logger.exception(
            "dotmac_sub webhook task failed (event=%s); incremental sync will "
            "reconcile",
            event_type,
        )
        raise
