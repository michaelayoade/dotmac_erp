"""
Splynx Sync Tasks - Celery background tasks for Splynx integration.

3-tier sync strategy:
  Tier 1 (every 30 min): Incremental sync — all customers, recent invoices/payments/credit notes
  Tier 2 (nightly 1 AM): Daily reconciliation — unpaid/partial invoices, 30-day payment window
  Tier 3 (Sunday 2 AM):  Full reconciliation — all entities, no date/status filters
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from celery import shared_task
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models.finance.ar.customer import Customer
from app.models.finance.core_org.organization import Organization
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.sync import SyncHistory, SyncJobStatus, SyncType
from app.services.splynx import SYSTEM_USER_ID, SplynxConfig, SplynxSyncService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_INCREMENTAL_FALLBACK_DAYS = 7
_INCREMENTAL_OVERLAP_HOURS = 1
_DAILY_RECON_PAYMENT_DAYS = 30


def _resolve_org_id(explicit_org_id: str | None) -> UUID | None:
    org_id_str = explicit_org_id or settings.default_organization_id
    if not org_id_str:
        return None
    try:
        return UUID(org_id_str)
    except ValueError:
        return None


def _resolve_ar_control_account(db: Session, organization_id: UUID) -> UUID | None:
    # Prefer AR-tagged posting account.
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

    # Common fallback chart code for AR control.
    account_id = db.scalar(
        select(Account.account_id).where(
            Account.organization_id == organization_id,
            Account.account_code == "1400",
            Account.is_active.is_(True),
        )
    )
    if account_id:
        return account_id

    # Last fallback from any existing customer.
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

    # Common fallback chart code for sales revenue.
    account_id = db.scalar(
        select(Account.account_id).where(
            Account.organization_id == organization_id,
            Account.account_code == "4000",
            Account.is_active.is_(True),
        )
    )
    if account_id:
        return account_id

    # Last fallback from any existing customer.
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


def _get_last_sync_at(
    db: Session,
    org_id: UUID,
    tier_marker: str,
) -> datetime | None:
    """Get completed_at of most recent successful sync containing *tier_marker* in entity_types.

    Each tier records a unique marker value in the entity_types JSONB array
    so we can distinguish Tier 1/2/3 history entries.
    """
    from sqlalchemy import literal
    from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB

    # Build a JSONB literal for the @> containment check
    marker_literal = func.cast(literal(f'["{tier_marker}"]'), PG_JSONB)

    stmt = select(func.max(SyncHistory.completed_at)).where(
        SyncHistory.organization_id == org_id,
        SyncHistory.source_system == "splynx",
        SyncHistory.status.in_(
            [
                SyncJobStatus.COMPLETED,
                SyncJobStatus.COMPLETED_WITH_ERRORS,
            ]
        ),
        # JSONB contains check — entity_types @> '["marker"]'
        SyncHistory.entity_types.op("@>")(marker_literal),
    )
    return db.scalar(stmt)


def _build_sync_context(
    db: Session,
    organization_id: str | None,
    sync_type: SyncType,
    entity_types: list[str],
) -> tuple[SplynxSyncService, UUID, UUID] | dict[str, Any]:
    """Shared setup: resolve org, accounts, create SyncHistory.

    Returns ``(service, history_id, org_id)`` on success, or an error dict.
    """
    org_id = _resolve_org_id(organization_id)
    if not org_id:
        return {"success": False, "error": "No valid organization ID configured"}

    if not SplynxConfig.from_settings().is_configured():
        return {"success": False, "error": "Splynx integration is not configured"}

    org = db.get(Organization, org_id)
    if not org or not org.is_active:
        return {"success": False, "error": "Organization not found or inactive"}

    ar_control_account_id = _resolve_ar_control_account(db, org_id)
    if not ar_control_account_id:
        return {
            "success": False,
            "error": "AR control account could not be resolved for Splynx sync",
        }

    revenue_account_id = _resolve_default_revenue_account(db, org_id)
    if not revenue_account_id:
        return {
            "success": False,
            "error": "Default revenue account could not be resolved for Splynx sync",
        }

    history = SyncHistory(
        organization_id=org_id,
        source_system="splynx",
        sync_type=sync_type,
        entity_types=entity_types,
        created_by_user_id=SYSTEM_USER_ID,
    )
    db.add(history)
    db.flush()
    history.start()
    db.flush()
    # Capture PK now — batch commits during sync expire/detach the
    # history object so later attribute access raises DetachedInstanceError.
    history_id = history.history_id

    service = SplynxSyncService(
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
    from_date: date | None = None,
    to_date: date | None = None,
    org_id: UUID | None = None,
) -> dict[str, Any]:
    """Record results on SyncHistory and return a summary dict."""
    history_fresh = db.get(SyncHistory, history_id)
    if not history_fresh:
        logger.error("SyncHistory %s disappeared during sync", history_id)
        return {"success": False, "error": "SyncHistory record lost"}

    history_fresh.total_records = _sum_total(sync_results)
    history_fresh.synced_count = _sum_synced(sync_results)
    history_fresh.skipped_count = _sum_skipped(sync_results)
    for error in _collect_errors(sync_results)[:100]:
        history_fresh.add_error("splynx", "sync", error)
    history_fresh.complete()

    db.commit()

    return {
        "success": history_fresh.error_count == 0,
        "history_id": str(history_id),
        "organization_id": str(org_id) if org_id else None,
        "from_date": str(from_date) if from_date else None,
        "to_date": str(to_date) if to_date else None,
        "synced_count": history_fresh.synced_count,
        "skipped_count": history_fresh.skipped_count,
        "error_count": history_fresh.error_count,
    }


def _handle_sync_failure(
    history_id: UUID,
    org_id: UUID,
    exc: BaseException,
    tier_name: str,
) -> None:
    """Mark SyncHistory as failed in a fresh session (original may be broken)."""
    logger.exception("%s Splynx sync failed for org %s", tier_name, org_id)
    with SessionLocal() as db2:
        history2 = db2.get(SyncHistory, history_id)
        if history2:
            history2.fail(str(exc))
            db2.commit()


# ---------------------------------------------------------------------------
# Tier 1 — Incremental sync (every 30 minutes)
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def run_splynx_incremental_sync(
    self: Any,
    organization_id: str | None = None,
    batch_size: int = 2000,
) -> dict[str, Any]:
    """
    Tier 1: Incremental Splynx sync.

    - **Customers**: Always fetched without date filters (~5K records, hash-skip unchanged).
    - **Invoices/Payments/Credit Notes**: ``date_from = last_sync_at - 1 hour`` overlap.
      Falls back to 7-day lookback if no prior sync exists.

    Args:
        organization_id: Optional explicit org UUID. Falls back to DEFAULT_ORGANIZATION_ID.
        batch_size: Per-entity batch cap for large data safety.
    """
    entity_types = ["customers", "invoices", "payments", "credit_notes"]

    with SessionLocal() as db:
        ctx = _build_sync_context(
            db,
            organization_id,
            SyncType.INCREMENTAL,
            entity_types,
        )
        if isinstance(ctx, dict):
            return ctx
        service, history_id, org_id = ctx

        try:
            # Determine incremental date window from last successful sync
            last_sync = _get_last_sync_at(db, org_id, "customers")
            now = datetime.now(UTC)

            if last_sync:
                from_date = (
                    last_sync - timedelta(hours=_INCREMENTAL_OVERLAP_HOURS)
                ).date()
            else:
                from_date = (now - timedelta(days=_INCREMENTAL_FALLBACK_DAYS)).date()
            to_date = date.today()

            # Customers: always fetch all (no date filter), hash-skip handles efficiency
            customers = service.sync_customers(
                created_by_user_id=SYSTEM_USER_ID,
                batch_size=batch_size,
                skip_unchanged=True,
            )

            # Invoices/Payments/Credit Notes: use incremental window
            invoices = service.sync_invoices(
                date_from=from_date,
                date_to=to_date,
                created_by_user_id=SYSTEM_USER_ID,
                batch_size=batch_size,
                skip_unchanged=True,
            )
            payments = service.sync_payments(
                date_from=from_date,
                date_to=to_date,
                created_by_user_id=SYSTEM_USER_ID,
                batch_size=batch_size,
                skip_unchanged=True,
            )
            auto_alloc = service.auto_allocate_unapplied_payments()
            if auto_alloc["errors"]:
                payments.errors.extend(auto_alloc["errors"][:100])
            credit_notes = service.sync_credit_notes(
                date_from=from_date,
                date_to=to_date,
                created_by_user_id=SYSTEM_USER_ID,
                batch_size=batch_size,
                skip_unchanged=True,
            )

            sync_results = [customers, invoices, payments, credit_notes]
            return _finalize_sync(
                db,
                history_id,
                sync_results,
                from_date=from_date,
                to_date=to_date,
                org_id=org_id,
            )

        except Exception as exc:
            db.rollback()
            _handle_sync_failure(history_id, org_id, exc, "Incremental")
            raise self.retry(exc=exc)
        finally:
            service.close()


# Backward-compatible alias for existing scheduled_task DB entries
@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def run_scheduled_splynx_sync(
    self: Any,
    organization_id: str | None = None,
    lookback_days: int = 2,
    batch_size: int = 2000,
    include_credit_notes: bool = True,
) -> dict[str, Any]:
    """
    Legacy alias — delegates to Tier 1 incremental sync.

    Kept for backward compatibility with existing scheduled_task DB entries.
    The ``lookback_days`` and ``include_credit_notes`` params are ignored;
    the new task uses adaptive date windows from SyncHistory.
    """
    result: dict[str, Any] = run_splynx_incremental_sync(
        organization_id=organization_id,
        batch_size=batch_size,
    )
    return result


# ---------------------------------------------------------------------------
# Tier 2 — Daily reconciliation (nightly at 1 AM)
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=2, default_retry_delay=600)
def run_splynx_daily_reconciliation(
    self: Any,
    organization_id: str | None = None,
    batch_size: int = 5000,
) -> dict[str, Any]:
    """
    Tier 2: Nightly reconciliation of open invoices and recent payments.

    - **Invoices**: Fetches ``status=unpaid`` and ``status=partially_paid``
      with ``batch_size`` cap to prevent OOM on large datasets.
      Splynx returns invoices by ID (oldest first), so consecutive nightly
      runs progressively reconcile the full backlog.
    - **Payments**: Re-syncs last 30 days to catch late corrections.

    Note: Splynx invoice ``date_from``/``date_to`` params are silently
    ignored by the API, so status filtering is the only viable approach.

    Args:
        organization_id: Optional explicit org UUID.
        batch_size: Per-status batch cap (default 5000).
    """
    entity_types = ["invoices_reconciliation", "payments_reconciliation"]

    with SessionLocal() as db:
        ctx = _build_sync_context(
            db,
            organization_id,
            SyncType.INCREMENTAL,
            entity_types,
        )
        if isinstance(ctx, dict):
            return ctx
        service, history_id, org_id = ctx

        try:
            now = datetime.now(UTC)
            payment_from_date = (now - timedelta(days=_DAILY_RECON_PAYMENT_DAYS)).date()
            to_date = date.today()

            # Fetch unpaid invoices (batch-capped to prevent OOM)
            invoices_unpaid = service.sync_invoices(
                status="unpaid",
                created_by_user_id=SYSTEM_USER_ID,
                batch_size=batch_size,
                skip_unchanged=True,
            )

            # Fetch partially paid invoices
            invoices_partial = service.sync_invoices(
                status="partially_paid",
                created_by_user_id=SYSTEM_USER_ID,
                batch_size=batch_size,
                skip_unchanged=True,
            )

            # Re-sync payments (date filter works for payments endpoint)
            payments = service.sync_payments(
                date_from=payment_from_date,
                date_to=to_date,
                created_by_user_id=SYSTEM_USER_ID,
                batch_size=batch_size,
                skip_unchanged=True,
            )
            auto_alloc = service.auto_allocate_unapplied_payments()
            if auto_alloc["errors"]:
                payments.errors.extend(auto_alloc["errors"][:100])

            sync_results = [invoices_unpaid, invoices_partial, payments]
            return _finalize_sync(
                db,
                history_id,
                sync_results,
                from_date=payment_from_date,
                to_date=to_date,
                org_id=org_id,
            )

        except Exception as exc:
            db.rollback()
            _handle_sync_failure(history_id, org_id, exc, "Daily reconciliation")
            raise self.retry(exc=exc)
        finally:
            service.close()


# ---------------------------------------------------------------------------
# Tier 3 — Full reconciliation (weekly, Sunday 2 AM)
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=1, default_retry_delay=900)
def run_splynx_full_reconciliation(
    self: Any,
    organization_id: str | None = None,
    batch_size: int = 10000,
) -> dict[str, Any]:
    """
    Tier 3: Weekly full reconciliation — all entities, no date/status filters.

    ``skip_unchanged=True`` makes this efficient (95%+ records unchanged).
    ``batch_size`` caps memory usage; consecutive weekly runs progressively
    cover the full dataset.

    Note: Splynx invoice ``date_from``/``date_to`` params are silently
    ignored by the API — every call paginates the entire invoice table.
    The batch_size limit prevents OOM on large datasets (100K+ invoices).

    Args:
        organization_id: Optional explicit org UUID.
        batch_size: Per-entity batch cap (default 10000).
    """
    entity_types = ["full_reconciliation"]

    with SessionLocal() as db:
        ctx = _build_sync_context(
            db,
            organization_id,
            SyncType.FULL,
            entity_types,
        )
        if isinstance(ctx, dict):
            return ctx
        service, history_id, org_id = ctx

        try:
            customers = service.sync_customers(
                created_by_user_id=SYSTEM_USER_ID,
                batch_size=batch_size,
                skip_unchanged=True,
            )
            invoices = service.sync_invoices(
                created_by_user_id=SYSTEM_USER_ID,
                batch_size=batch_size,
                skip_unchanged=True,
            )
            payments = service.sync_payments(
                created_by_user_id=SYSTEM_USER_ID,
                batch_size=batch_size,
                skip_unchanged=True,
            )
            auto_alloc = service.auto_allocate_unapplied_payments()
            if auto_alloc["errors"]:
                payments.errors.extend(auto_alloc["errors"][:100])
            credit_notes = service.sync_credit_notes(
                created_by_user_id=SYSTEM_USER_ID,
                batch_size=batch_size,
                skip_unchanged=True,
            )

            sync_results = [customers, invoices, payments, credit_notes]
            return _finalize_sync(
                db,
                history_id,
                sync_results,
                org_id=org_id,
            )

        except Exception as exc:
            db.rollback()
            _handle_sync_failure(history_id, org_id, exc, "Full reconciliation")
            raise self.retry(exc=exc)
        finally:
            service.close()
