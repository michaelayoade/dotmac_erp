"""
Data Health Tasks — Celery tasks for data integrity and maintenance.

Handles:
- Notification TTL cleanup (purge old notifications)
- Event outbox recovery (unstick PENDING events)
- Invoice status reconciliation (fix false-PAID invoices)
- Stale draft cleanup (identify/void old drafts)
- Auto-post approved invoices
- Account balance rebuild from posted_ledger_line
- Payment allocation reconciliation (fix unallocated payments)
- Unbalanced journal correction (flag/fix posted journals)
- Comprehensive data health check
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, cast

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import Decimal
from uuid import UUID

from celery import shared_task
from sqlalchemy.engine import CursorResult

from app.db.session_context import (
    cross_org_session,
    for_each_organization,
    session_for_org,
)
from app.services.common import coerce_uuid
from app.services.finance.ar.payment_status import PAYMENT_DUST, apply_payment_status

logger = logging.getLogger(__name__)


def _resolve_org_id(organization_id: UUID | str | None) -> UUID | None:
    """Coerce an optional organization identifier for tenant-scoped tasks."""
    if organization_id is None:
        return None
    return UUID(str(coerce_uuid(organization_id)))


def _tenant_sessions(organization_id: UUID | str | None):
    """One tenant-scoped session per organization in scope.

    This replaces ``_task_session``, whose unpinned branch opened a single
    ``cross_org_session`` and ran the whole fleet's repair through it. That
    bypasses only ERP's SQLAlchemy listener, never PostgreSQL RLS, so under
    ``app_user`` every unpinned run of these jobs sees zero rows: the repair
    tasks repair nothing, and the health check reports a clean bill of health
    for a fleet it cannot see. Both outcomes are indistinguishable from
    success.

    Two deliberate choices:

    * ``include_inactive=True`` — a deactivated organization's false-PAID
      invoices, unbalanced journals and stale drafts still have to be
      repaired, and the scan this replaces never looked at organization
      status.
    * ``only=`` rather than an unchecked ``session_for_org`` — an
      ``organization_id`` argument that is not in the catalogue now yields an
      empty run instead of an unscoped one.

    Every organization filter these tasks used to apply *conditionally* is now
    applied unconditionally. That is not belt-and-braces: the ORM listener
    filters SELECTs only, so a ``DELETE`` or ``UPDATE`` issued in a tenant
    session is scoped by PostgreSQL RLS alone — and by nothing at all in the
    SQLite unit-test lane.
    """
    return for_each_organization(
        include_inactive=True, only=_resolve_org_id(organization_id)
    )


def _outbox_session(org_id: UUID | None):
    """The outbox is read fleet-wide, and deliberately not through a fan-out.

    ``event_outbox`` has no ``organization_id`` column. The ORM listener
    therefore skips it and the RLS migration never gave it a policy — so
    reading it fleet-wide is not an RLS bypass at all, and works unchanged
    under ``app_user``. Its tenant identity lives in a JSONB header instead.

    That is why these two entry points are NOT converted to
    :func:`_tenant_sessions`. A per-tenant fan-out would filter on
    ``headers['organization_id']`` and silently skip every event whose header
    is absent, malformed, or names an organization missing from the catalogue
    — precisely the events a stuck-event recovery job and a data-health check
    exist to surface. It would turn a visible backlog into an invisible one.

    Recorded for disposition review: the inventory classifies this module as
    ``tenant_catalog_definer``, which is right for the seven tenant-table jobs
    and wrong for these two. This residual use needs a non-tenant contract,
    not a fan-out.
    """
    return session_for_org(org_id) if org_id else cross_org_session()


@shared_task
def cleanup_old_notifications(
    read_days: int = 30,
    unread_days: int = 90,
    organization_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Delete old notifications beyond retention thresholds.

    Args:
        read_days: Delete read notifications older than this many days.
        unread_days: Delete unread notifications older than this many days.

    Returns:
        Dict with counts of deleted notifications.
    """
    logger.info(
        "Starting notification cleanup (read=%dd, unread=%dd)",
        read_days,
        unread_days,
    )

    read_deleted = 0
    unread_deleted = 0
    errors: list[str] = []

    for org_id, db in _tenant_sessions(organization_id):
        from app.models.notification import Notification

        now = datetime.now(UTC)
        read_cutoff = now - timedelta(days=read_days)
        unread_cutoff = now - timedelta(days=unread_days)

        try:
            from sqlalchemy import delete

            # The organization filter is unconditional now. A DELETE is not a
            # SELECT, so the ORM listener does not scope it — only RLS would,
            # and only on PostgreSQL. Dropping this filter inside a tenant
            # session would purge the whole fleet's notifications.
            read_del_stmt = delete(Notification).where(
                Notification.is_read.is_(True),
                Notification.created_at < read_cutoff,
                Notification.organization_id == org_id,
            )
            unread_del_stmt = delete(Notification).where(
                Notification.is_read.is_(False),
                Notification.created_at < unread_cutoff,
                Notification.organization_id == org_id,
            )

            read_deleted += (
                cast(CursorResult[Any], db.execute(read_del_stmt)).rowcount or 0
            )
            unread_deleted += (
                cast(CursorResult[Any], db.execute(unread_del_stmt)).rowcount or 0
            )

            db.commit()
        except Exception as e:
            logger.exception(
                "Failed to cleanup notifications for organization %s", org_id
            )
            db.rollback()
            errors.append(str(e))

    total = read_deleted + unread_deleted
    logger.info(
        "Notification cleanup complete: %d read + %d unread = %d total deleted",
        read_deleted,
        unread_deleted,
        total,
    )
    return {
        "read_deleted": read_deleted,
        "unread_deleted": unread_deleted,
        "errors": errors,
    }


@shared_task
def process_stuck_outbox_events(
    stuck_minutes: int = 30,
    batch_size: int = 500,
    organization_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Recover PENDING outbox events that have been stuck.

    Marks old PENDING events as FAILED so the retry logic can pick them up,
    or publishes them if they have no handler errors.

    ``event_outbox`` carries no ``organization_id`` column, so this sweep is
    NOT a tenant fan-out — see :func:`_outbox_session` for why converting it
    would hide exactly the events it exists to recover.

    Args:
        stuck_minutes: Events PENDING longer than this are considered stuck.
        batch_size: Maximum events to process per run.

    Returns:
        Dict with processing counts.
    """
    logger.info(
        "Processing stuck outbox events (stuck > %d min, batch=%d)",
        stuck_minutes,
        batch_size,
    )

    recovered = 0
    marked_dead = 0
    errors: list[str] = []

    org_id = _resolve_org_id(organization_id)
    with _outbox_session(org_id) as db:
        from sqlalchemy import or_, select

        from app.models.finance.platform.event_outbox import EventOutbox, EventStatus

        now = datetime.now(UTC)
        cutoff = now - timedelta(minutes=stuck_minutes)

        stmt = select(EventOutbox).where(
            EventOutbox.status == EventStatus.PENDING,
            EventOutbox.created_at < cutoff,
            # An unexpired claim lease means a relay worker is actively
            # delivering this event — it is in flight, not stuck.
            or_(
                EventOutbox.lease_expires_at.is_(None),
                EventOutbox.lease_expires_at <= now,
            ),
        )
        if org_id is not None:
            stmt = stmt.where(
                EventOutbox.headers["organization_id"].astext == str(org_id)
            )
        stmt = stmt.order_by(EventOutbox.created_at).limit(batch_size)
        stuck_events = list(db.scalars(stmt).all())

        for event in stuck_events:
            try:
                if event.retry_count >= 5:
                    event.status = EventStatus.DEAD
                    event.last_error = "Exceeded max retries (stuck recovery)"
                    marked_dead += 1
                else:
                    # Move stuck events out of PENDING so monitoring and
                    # backlog counters reflect true queue health.
                    event.status = EventStatus.FAILED
                    event.retry_count = (event.retry_count or 0) + 1
                    event.next_retry_at = datetime.now(UTC)
                    event.last_error = (
                        f"Stuck recovery at {datetime.now(UTC).isoformat()}"
                    )
                    recovered += 1
            except Exception as e:
                logger.exception("Failed to recover event %s", event.event_id)
                errors.append(str(e))

        db.commit()

    logger.info(
        "Outbox recovery complete: %d recovered, %d dead",
        recovered,
        marked_dead,
    )
    return {"recovered": recovered, "marked_dead": marked_dead, "errors": errors}


@shared_task
def reconcile_invoice_statuses(
    organization_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Fix invoices marked PAID but with outstanding balance.

    Scans for invoices where status=PAID but amount_paid < total_amount,
    and corrects their status to PARTIALLY_PAID or POSTED.

    Returns:
        Dict with reconciliation counts.
    """
    logger.info("Starting invoice status reconciliation")

    fixed_to_partially_paid = 0
    fixed_to_posted = 0
    errors: list[str] = []

    for org_id, db in _tenant_sessions(organization_id):
        from sqlalchemy import select

        from app.models.finance.ar.invoice import Invoice, InvoiceStatus

        stmt = select(Invoice).where(
            Invoice.status == InvoiceStatus.PAID,
            (Invoice.balance_due) > PAYMENT_DUST,
            Invoice.organization_id == org_id,
        )
        false_paid = list(db.scalars(stmt).all())

        for inv in false_paid:
            try:
                outstanding = inv.balance_due
                resolved = apply_payment_status(inv)
                if resolved is InvoiceStatus.PARTIALLY_PAID:
                    fixed_to_partially_paid += 1
                else:
                    # An unpaid invoice: POSTED, or OVERDUE past its due date.
                    # The old loop could only produce POSTED, so a repaired
                    # invoice that was already late lost its overdue signal.
                    fixed_to_posted += 1
                logger.info(
                    "Invoice %s: PAID -> %s (outstanding=%s)",
                    inv.invoice_number,
                    resolved.value,
                    outstanding,
                )
            except Exception as e:
                logger.exception("Failed to fix invoice %s", inv.invoice_id)
                errors.append(str(e))

        db.commit()

    total = fixed_to_partially_paid + fixed_to_posted
    logger.info("Invoice reconciliation complete: %d fixed", total)
    return {
        "fixed_to_partially_paid": fixed_to_partially_paid,
        "fixed_to_posted": fixed_to_posted,
        "errors": errors,
    }


@shared_task
def auto_post_approved_invoices(
    max_age_days: int = 7,
    organization_id: UUID | str | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """Auto-post invoices stuck in APPROVED status.

    Invoices that have been APPROVED for longer than max_age_days
    are automatically posted to the GL.

    Args:
        max_age_days: Only post invoices approved longer than this.
        batch_size: Optional cap on invoices posted per run. It stays a
            FLEET-WIDE cap: the remaining budget is carried across
            organizations rather than restarting at each one.

    Returns:
        Dict with posting counts.
    """
    logger.info("Starting auto-post of approved invoices (age > %d days)", max_age_days)

    posted = 0
    skipped = 0
    errors: list[str] = []
    remaining = batch_size

    for org_id, db in _tenant_sessions(organization_id):
        if remaining is not None and remaining <= 0:
            break

        from sqlalchemy import select

        from app.models.finance.ar.invoice import Invoice, InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        cutoff = datetime.now(UTC) - timedelta(days=max_age_days)

        stmt = select(Invoice).where(
            Invoice.status == InvoiceStatus.APPROVED,
            Invoice.updated_at < cutoff,
            Invoice.organization_id == org_id,
        )
        if remaining is not None:
            stmt = stmt.limit(remaining)
        approved = list(db.scalars(stmt).all())
        if remaining is not None:
            remaining -= len(approved)

        for inv in approved:
            try:
                ARInvoiceService.post_invoice(
                    db=db,
                    organization_id=inv.organization_id,
                    invoice_id=inv.invoice_id,
                    posted_by_user_id=inv.created_by_user_id,
                )
                posted += 1
                logger.info("Auto-posted invoice %s", inv.invoice_number)
            except Exception as e:
                db.rollback()
                logger.warning(
                    "Failed to auto-post invoice %s: %s",
                    inv.invoice_number,
                    e,
                )
                skipped += 1
                errors.append(f"{inv.invoice_number}: {e}")

        db.commit()

    logger.info(
        "Auto-post complete: %d posted, %d skipped",
        posted,
        skipped,
    )
    return {"posted": posted, "skipped": skipped, "errors": errors}


@shared_task
def cleanup_stale_drafts(
    draft_age_days: int = 180,
    dry_run: bool = True,
    organization_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Identify and optionally void stale draft documents.

    Scans journal entries, invoices, and supplier invoices for old drafts.

    Args:
        draft_age_days: Drafts older than this are considered stale.
        dry_run: If True, only report counts without voiding.

    Returns:
        Dict with counts by entity type.
    """
    logger.info(
        "Starting stale draft cleanup (age > %d days, dry_run=%s)",
        draft_age_days,
        dry_run,
    )

    journal_drafts = 0
    invoice_drafts = 0
    ap_invoice_drafts = 0
    voided = 0
    errors: list[str] = []

    for org_id, db in _tenant_sessions(organization_id):
        from sqlalchemy import func, select

        from app.models.finance.ap.supplier_invoice import (
            SupplierInvoice,
            SupplierInvoiceStatus,
        )
        from app.models.finance.ar.invoice import Invoice, InvoiceStatus
        from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus

        cutoff = datetime.now(UTC) - timedelta(days=draft_age_days)

        journal_drafts_stmt = select(func.count(JournalEntry.journal_entry_id)).where(
            JournalEntry.status == JournalStatus.DRAFT,
            JournalEntry.created_at < cutoff,
            JournalEntry.organization_id == org_id,
        )
        invoice_drafts_stmt = select(func.count(Invoice.invoice_id)).where(
            Invoice.status == InvoiceStatus.DRAFT,
            Invoice.created_at < cutoff,
            Invoice.organization_id == org_id,
        )
        ap_invoice_drafts_stmt = select(func.count(SupplierInvoice.invoice_id)).where(
            SupplierInvoice.status == SupplierInvoiceStatus.DRAFT,
            SupplierInvoice.created_at < cutoff,
            SupplierInvoice.organization_id == org_id,
        )

        journal_drafts += db.scalar(journal_drafts_stmt) or 0
        invoice_drafts += db.scalar(invoice_drafts_stmt) or 0
        ap_invoice_drafts += db.scalar(ap_invoice_drafts_stmt) or 0

        if not dry_run:
            stale_journals_stmt = select(JournalEntry).where(
                JournalEntry.status == JournalStatus.DRAFT,
                JournalEntry.created_at < cutoff,
                JournalEntry.organization_id == org_id,
            )
            stale_invoices_stmt = select(Invoice).where(
                Invoice.status == InvoiceStatus.DRAFT,
                Invoice.created_at < cutoff,
                Invoice.organization_id == org_id,
            )
            stale_ap_stmt = select(SupplierInvoice).where(
                SupplierInvoice.status == SupplierInvoiceStatus.DRAFT,
                SupplierInvoice.created_at < cutoff,
                SupplierInvoice.organization_id == org_id,
            )

            stale_journals = list(db.scalars(stale_journals_stmt).all())
            for je in stale_journals:
                je.status = JournalStatus.VOID
                voided += 1

            stale_invoices = list(db.scalars(stale_invoices_stmt).all())
            for inv in stale_invoices:
                inv.status = InvoiceStatus.VOID
                voided += 1

            stale_ap = list(db.scalars(stale_ap_stmt).all())
            for si in stale_ap:
                si.status = SupplierInvoiceStatus.VOID
                voided += 1

            db.commit()

    total = journal_drafts + invoice_drafts + ap_invoice_drafts
    logger.info(
        "Stale draft scan complete: %d total (%d journals, %d AR, %d AP), voided=%d",
        total,
        journal_drafts,
        invoice_drafts,
        ap_invoice_drafts,
        voided,
    )
    return {
        "journal_drafts": journal_drafts,
        "invoice_drafts": invoice_drafts,
        "ap_invoice_drafts": ap_invoice_drafts,
        "voided": voided,
        "errors": errors,
    }


@shared_task
def rebuild_account_balances(
    organization_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Rebuild gl.account_balance from posted_ledger_line data.

    Aggregates all posted ledger lines by account, period, and dimensions
    to populate the account_balance table used by dashboards and reports.

    Returns:
        Dict with row counts.
    """
    logger.info("Starting account balance rebuild")

    rows_written = 0
    errors: list[str] = []

    for org_id, db in _tenant_sessions(organization_id):
        from sqlalchemy import func, select

        from app.models.finance.gl.account_balance import AccountBalance, BalanceType
        from app.models.finance.gl.posted_ledger_line import PostedLedgerLine

        try:
            agg_stmt = (
                select(
                    PostedLedgerLine.organization_id,
                    PostedLedgerLine.account_id,
                    PostedLedgerLine.fiscal_period_id,
                    PostedLedgerLine.business_unit_id,
                    PostedLedgerLine.cost_center_id,
                    PostedLedgerLine.project_id,
                    PostedLedgerLine.segment_id,
                    func.sum(PostedLedgerLine.debit_amount).label("total_debit"),
                    func.sum(PostedLedgerLine.credit_amount).label("total_credit"),
                    func.count().label("txn_count"),
                )
                .group_by(
                    PostedLedgerLine.organization_id,
                    PostedLedgerLine.account_id,
                    PostedLedgerLine.fiscal_period_id,
                    PostedLedgerLine.business_unit_id,
                    PostedLedgerLine.cost_center_id,
                    PostedLedgerLine.project_id,
                    PostedLedgerLine.segment_id,
                )
                .where(PostedLedgerLine.organization_id == org_id)
            )

            rows = db.execute(agg_stmt).all()

            for row in rows:
                total_debit = Decimal(str(row.total_debit or 0))
                total_credit = Decimal(str(row.total_credit or 0))
                net = total_debit - total_credit

                existing = db.scalar(
                    select(AccountBalance).where(
                        AccountBalance.organization_id == row.organization_id,
                        AccountBalance.account_id == row.account_id,
                        AccountBalance.fiscal_period_id == row.fiscal_period_id,
                        AccountBalance.balance_type == BalanceType.ACTUAL,
                        AccountBalance.business_unit_id == row.business_unit_id,
                        AccountBalance.cost_center_id == row.cost_center_id,
                        AccountBalance.project_id == row.project_id,
                        AccountBalance.segment_id == row.segment_id,
                    )
                )

                if existing:
                    existing.period_debit = total_debit
                    existing.period_credit = total_credit
                    existing.closing_debit = total_debit
                    existing.closing_credit = total_credit
                    existing.net_balance = net
                    existing.ytd_net_balance = net
                    existing.transaction_count = row.txn_count
                else:
                    bal = AccountBalance(
                        organization_id=row.organization_id,
                        account_id=row.account_id,
                        fiscal_period_id=row.fiscal_period_id,
                        balance_type=BalanceType.ACTUAL,
                        business_unit_id=row.business_unit_id,
                        cost_center_id=row.cost_center_id,
                        project_id=row.project_id,
                        segment_id=row.segment_id,
                        period_debit=total_debit,
                        period_credit=total_credit,
                        closing_debit=total_debit,
                        closing_credit=total_credit,
                        net_balance=net,
                        ytd_net_balance=net,
                        transaction_count=row.txn_count,
                    )
                    db.add(bal)

                rows_written += 1

                if rows_written % 1000 == 0:
                    db.flush()

            db.commit()
        except Exception as e:
            logger.exception(
                "Failed to rebuild account balances for organization %s", org_id
            )
            db.rollback()
            errors.append(str(e))

    logger.info("Account balance rebuild complete: %d rows", rows_written)
    return {"rows_written": rows_written, "errors": errors}


@shared_task
def reconcile_payment_allocations(
    batch_size: int = 500,
    dry_run: bool = True,
    organization_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Allocate unallocated CLEARED/APPROVED payments to outstanding invoices.

    For each payment with no allocations, finds invoices for the same customer
    that have an outstanding balance and allocates using FIFO (oldest invoice
    first by due_date).

    Args:
        batch_size: Maximum payments to process per run. It stays a FLEET-WIDE
            cap: the remaining budget is carried across organizations rather
            than restarting at each one.
        dry_run: If True, only report counts without creating allocations.

    Returns:
        Dict with allocation counts.
    """
    logger.info(
        "Starting payment allocation reconciliation (batch=%d, dry_run=%s)",
        batch_size,
        dry_run,
    )

    fully_allocated = 0
    partially_allocated = 0
    no_match = 0
    allocations_created = 0
    errors: list[str] = []
    # Named `budget`, not `remaining`: the per-payment loop below already owns
    # a `remaining` (the unallocated portion of ONE payment), and reusing the
    # name would let the first payment silently overwrite the fleet-wide cap.
    budget = batch_size

    for org_id, db in _tenant_sessions(organization_id):
        if budget <= 0:
            break

        from sqlalchemy import select

        from app.models.finance.ar.customer_payment import (
            CustomerPayment,
            PaymentStatus,
        )
        from app.models.finance.ar.invoice import Invoice, InvoiceStatus
        from app.models.finance.ar.payment_allocation import PaymentAllocation

        # Find payments with no allocations (effective status = APPROVED or CLEARED)
        has_alloc = (
            select(PaymentAllocation.payment_id)
            .where(PaymentAllocation.payment_id == CustomerPayment.payment_id)
            .correlate(CustomerPayment)
            .exists()
        )

        stmt = (
            select(CustomerPayment)
            .where(
                CustomerPayment.status.in_(
                    [PaymentStatus.APPROVED, PaymentStatus.CLEARED]
                ),
                CustomerPayment.amount > Decimal("0"),
                ~has_alloc,
                CustomerPayment.organization_id == org_id,
            )
            .order_by(CustomerPayment.payment_date)
            .limit(budget)
        )
        unallocated = list(db.scalars(stmt).all())
        budget -= len(unallocated)

        logger.info(
            "Found %d unallocated payments to process in organization %s",
            len(unallocated),
            org_id,
        )

        for payment in unallocated:
            try:
                remaining = Decimal(str(payment.amount))

                # Find outstanding invoices for same customer, FIFO by due_date
                inv_stmt = (
                    select(Invoice)
                    .where(
                        Invoice.customer_id == payment.customer_id,
                        Invoice.organization_id == payment.organization_id,
                        Invoice.status.in_(
                            [
                                InvoiceStatus.POSTED,
                                InvoiceStatus.PARTIALLY_PAID,
                                InvoiceStatus.OVERDUE,
                            ]
                        ),
                        (Invoice.balance_due) > Decimal("0.01"),
                    )
                    .order_by(Invoice.due_date, Invoice.created_at)
                )
                invoices = list(db.scalars(inv_stmt).all())

                if not invoices:
                    no_match += 1
                    continue

                payment_allocs: list[tuple[Invoice, Decimal]] = []
                for inv in invoices:
                    if remaining <= Decimal("0.01"):
                        break

                    # NOT `balance_due`: the generated column holds what the last SELECT
                    # returned, and this loop writes `amount_paid` as it goes. The
                    # subtraction must stay live or the second allocation over-applies.
                    balance_due = inv.total_amount - inv.amount_paid
                    alloc_amount = min(remaining, balance_due)
                    payment_allocs.append((inv, alloc_amount))
                    remaining -= alloc_amount

                if not payment_allocs:
                    no_match += 1
                    continue

                if not dry_run:
                    for inv, alloc_amount in payment_allocs:
                        allocation = PaymentAllocation(
                            payment_id=payment.payment_id,
                            invoice_id=inv.invoice_id,
                            allocated_amount=alloc_amount,
                            allocation_date=payment.payment_date,
                        )
                        db.add(allocation)

                        # Update invoice amount_paid and status
                        inv.amount_paid = inv.amount_paid + alloc_amount
                        apply_payment_status(inv)

                        allocations_created += 1

                if remaining <= Decimal("0.01"):
                    fully_allocated += 1
                else:
                    partially_allocated += 1

            except Exception as e:
                logger.exception("Failed to allocate payment %s", payment.payment_id)
                errors.append(f"{payment.payment_id}: {e}")

        if not dry_run:
            db.commit()

    total = fully_allocated + partially_allocated + no_match
    logger.info(
        "Payment allocation reconciliation complete: "
        "%d fully, %d partially, %d no-match, %d allocations created "
        "(dry_run=%s)",
        fully_allocated,
        partially_allocated,
        no_match,
        allocations_created,
        dry_run,
    )
    return {
        "fully_allocated": fully_allocated,
        "partially_allocated": partially_allocated,
        "no_match": no_match,
        "allocations_created": allocations_created,
        "total_processed": total,
        "dry_run": dry_run,
        "errors": errors,
    }


@shared_task
def fix_unbalanced_posted_journals(
    dry_run: bool = True,
    batch_size: int = 100,
    organization_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Find and fix POSTED journals with unbalanced debit/credit lines.

    For each unbalanced journal, creates a correcting journal entry that
    adds the missing debit or credit to a suspense account, bringing the
    original entry into balance. The original journal is then reversed
    and the corrected version is posted.

    In dry-run mode (default), only reports the unbalanced journals found.

    Args:
        dry_run: If True, only report findings without fixing.
        batch_size: Maximum journals to process per run. It stays a FLEET-WIDE
            cap: the remaining budget is carried across organizations rather
            than restarting at each one.

    Returns:
        Dict with journal counts and details.
    """
    logger.info(
        "Starting unbalanced journal fix (dry_run=%s, batch=%d)",
        dry_run,
        batch_size,
    )

    found = 0
    fixed = 0
    details: list[dict[str, Any]] = []
    errors: list[str] = []
    remaining = batch_size

    for org_id, db in _tenant_sessions(organization_id):
        if remaining <= 0:
            break

        from sqlalchemy import func, select

        from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
        from app.models.finance.gl.journal_entry_line import JournalEntryLine

        debit_total = func.sum(
            func.coalesce(
                JournalEntryLine.debit_amount_functional,
                JournalEntryLine.debit_amount,
                0,
            )
        )
        credit_total = func.sum(
            func.coalesce(
                JournalEntryLine.credit_amount_functional,
                JournalEntryLine.credit_amount,
                0,
            )
        )
        imbalance_expr = debit_total - credit_total
        unbalanced_stmt = (
            select(
                JournalEntry.journal_entry_id,
                JournalEntry.journal_number,
                JournalEntry.organization_id,
                JournalEntry.entry_date,
                JournalEntry.description,
                JournalEntry.created_by_user_id,
                debit_total.label("total_debit"),
                credit_total.label("total_credit"),
                imbalance_expr.label("imbalance"),
            )
            .join(
                JournalEntryLine,
                JournalEntryLine.journal_entry_id == JournalEntry.journal_entry_id,
            )
            .where(
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.organization_id == org_id,
            )
            .group_by(
                JournalEntry.journal_entry_id,
                JournalEntry.journal_number,
                JournalEntry.organization_id,
                JournalEntry.entry_date,
                JournalEntry.description,
                JournalEntry.created_by_user_id,
            )
            .having(func.abs(imbalance_expr) > Decimal("0.01"))
            .order_by(func.abs(imbalance_expr).desc())
            .limit(remaining)
        )
        rows = db.execute(unbalanced_stmt).all()
        found += len(rows)
        remaining -= len(rows)

        for row in rows:
            imbalance = Decimal(str(row.imbalance))
            detail: dict[str, Any] = {
                "journal_number": row.journal_number,
                "journal_entry_id": str(row.journal_entry_id),
                "total_debit": str(row.total_debit),
                "total_credit": str(row.total_credit),
                "imbalance": str(imbalance),
            }
            details.append(detail)

            if dry_run:
                logger.warning(
                    "Unbalanced journal %s: debit=%s credit=%s imbalance=%s",
                    row.journal_number,
                    row.total_debit,
                    row.total_credit,
                    imbalance,
                )
                continue

            # Fix: create a correcting journal using the reversal service
            try:
                from app.services.finance.gl.reversal import ReversalService

                result = ReversalService.create_reversal(
                    db=db,
                    organization_id=row.organization_id,
                    original_journal_id=row.journal_entry_id,
                    reversal_date=row.entry_date,
                    created_by_user_id=row.created_by_user_id,
                    reason=(
                        f"Data health fix: unbalanced journal (imbalance={imbalance})"
                    ),
                    auto_post=True,
                )
                if result.success:
                    detail["action"] = "reversed"
                    detail["reversal_journal_id"] = str(result.reversal_journal_id)
                    fixed += 1
                    logger.info(
                        "Reversed unbalanced journal %s -> %s",
                        row.journal_number,
                        result.reversal_journal_id,
                    )
                else:
                    detail["action"] = "failed"
                    detail["reason"] = result.message
                    errors.append(f"{row.journal_number}: {result.message}")
            except Exception as e:
                logger.exception("Failed to fix journal %s", row.journal_number)
                detail["action"] = "error"
                errors.append(f"{row.journal_number}: {e}")

        if not dry_run:
            db.commit()

    logger.info(
        "Unbalanced journal fix complete: %d found, %d fixed (dry_run=%s)",
        found,
        fixed,
        dry_run,
    )
    return {
        "found": found,
        "fixed": fixed,
        "details": details,
        "dry_run": dry_run,
        "errors": errors,
    }


def _outbox_counters(org_id: UUID | None) -> dict[str, int]:
    """Count stuck and dead outbox events, fleet-wide unless pinned.

    Deliberately outside the tenant fan-out above, for the reason spelled out
    in :func:`_outbox_session`: ``event_outbox`` has no ``organization_id``
    column, so a per-tenant count would drop every event whose JSONB header is
    missing or names an unknown organization — and those are exactly the
    events a health check has to surface. Counting them per tenant and summing
    would report a smaller backlog than actually exists, which is worse than
    reporting none at all.
    """
    from sqlalchemy import func, select

    from app.models.finance.platform.event_outbox import EventOutbox, EventStatus

    outbox_cutoff = datetime.now(UTC) - timedelta(minutes=30)
    stuck_stmt = select(func.count(EventOutbox.event_id)).where(
        EventOutbox.status == EventStatus.PENDING,
        EventOutbox.created_at < outbox_cutoff,
    )
    dead_stmt = select(func.count(EventOutbox.event_id)).where(
        EventOutbox.status == EventStatus.DEAD,
    )
    if org_id is not None:
        header_org = EventOutbox.headers["organization_id"].astext == str(org_id)
        stuck_stmt = stuck_stmt.where(header_org)
        dead_stmt = dead_stmt.where(header_org)

    with _outbox_session(org_id) as db:
        return {
            "stuck_outbox_events": db.scalar(stuck_stmt) or 0,
            "dead_outbox_events": db.scalar(dead_stmt) or 0,
        }


@shared_task
def run_data_health_check(
    organization_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Comprehensive data health check.

    Checks for:
    - Unbalanced posted journals
    - False-PAID invoices
    - Stuck outbox events
    - Stale drafts
    - Empty account_balance
    - Notification table size

    Returns:
        Dict with all check results.
    """
    logger.info("Starting comprehensive data health check")

    # Seeded with zeros rather than built key-by-key: a fleet with no
    # organizations, or an `organization_id` that is not in the catalogue,
    # must still answer every counter. A missing key would read to a caller as
    # "this check was not run", which is not what an empty fleet means.
    results: dict[str, Any] = {
        "unbalanced_journals": 0,
        "false_paid_invoices": 0,
        "stuck_outbox_events": 0,
        "dead_outbox_events": 0,
        "stale_journal_drafts": 0,
        "account_balance_rows": 0,
        "notification_total": 0,
        "notification_unread": 0,
        "approved_invoices_stuck": 0,
        "unallocated_payments": 0,
    }

    for org_id, db in _tenant_sessions(organization_id):
        from sqlalchemy import exists, func, select

        from app.models.finance.ar.customer_payment import (
            CustomerPayment,
            PaymentStatus,
        )
        from app.models.finance.ar.invoice import Invoice, InvoiceStatus
        from app.models.finance.ar.payment_allocation import PaymentAllocation
        from app.models.finance.gl.account_balance import AccountBalance
        from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
        from app.models.finance.gl.journal_entry_line import JournalEntryLine
        from app.models.notification import Notification

        # 1. Unbalanced posted journals
        debit_total = func.sum(
            func.coalesce(
                JournalEntryLine.debit_amount_functional,
                JournalEntryLine.debit_amount,
                0,
            )
        )
        credit_total = func.sum(
            func.coalesce(
                JournalEntryLine.credit_amount_functional,
                JournalEntryLine.credit_amount,
                0,
            )
        )
        imbalance_expr = debit_total - credit_total
        unbalanced_stmt = (
            select(JournalEntryLine.journal_entry_id)
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .where(
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.organization_id == org_id,
            )
            .group_by(JournalEntryLine.journal_entry_id)
            .having(func.abs(imbalance_expr) > Decimal("0.01"))
        )
        results["unbalanced_journals"] += (
            db.scalar(select(func.count()).select_from(unbalanced_stmt.subquery())) or 0
        )

        # 2. False-PAID invoices
        false_paid_stmt = select(func.count(Invoice.invoice_id)).where(
            Invoice.status == InvoiceStatus.PAID,
            (Invoice.balance_due) > Decimal("0.01"),
            Invoice.organization_id == org_id,
        )
        results["false_paid_invoices"] += db.scalar(false_paid_stmt) or 0

        # 3. Stale drafts (> 180 days)
        draft_cutoff = datetime.now(UTC) - timedelta(days=180)
        stale_journal_stmt = select(func.count(JournalEntry.journal_entry_id)).where(
            JournalEntry.status == JournalStatus.DRAFT,
            JournalEntry.created_at < draft_cutoff,
            JournalEntry.organization_id == org_id,
        )
        results["stale_journal_drafts"] += db.scalar(stale_journal_stmt) or 0

        # 4. Account balance rows
        account_balance_stmt = select(func.count(AccountBalance.balance_id)).where(
            AccountBalance.organization_id == org_id
        )
        results["account_balance_rows"] += db.scalar(account_balance_stmt) or 0

        # 5. Notification stats
        notification_total_stmt = select(
            func.count(Notification.notification_id)
        ).where(Notification.organization_id == org_id)
        notification_unread_stmt = select(
            func.count(Notification.notification_id)
        ).where(
            Notification.is_read.is_(False),
            Notification.organization_id == org_id,
        )
        results["notification_total"] += db.scalar(notification_total_stmt) or 0
        results["notification_unread"] += db.scalar(notification_unread_stmt) or 0

        # 6. Approved invoices (stuck)
        approved_invoices_stmt = select(func.count(Invoice.invoice_id)).where(
            Invoice.status == InvoiceStatus.APPROVED,
            Invoice.organization_id == org_id,
        )
        results["approved_invoices_stuck"] += db.scalar(approved_invoices_stmt) or 0

        # 7. Unallocated payments (APPROVED/CLEARED with no allocation records)
        unallocated_stmt = select(func.count(CustomerPayment.payment_id)).where(
            CustomerPayment.status.in_([PaymentStatus.APPROVED, PaymentStatus.CLEARED]),
            CustomerPayment.amount > 0,
            ~exists(
                select(PaymentAllocation.allocation_id).where(
                    PaymentAllocation.payment_id == CustomerPayment.payment_id
                )
            ),
            CustomerPayment.organization_id == org_id,
        )
        results["unallocated_payments"] += db.scalar(unallocated_stmt) or 0

    results.update(_outbox_counters(_resolve_org_id(organization_id)))

    # Log summary
    logger.info("=== Data Health Check Results ===")
    for key, value in results.items():
        level = logging.WARNING if value else logging.INFO
        logger.log(level, "  %s: %s", key, value)
    logger.info("=== End Health Check ===")

    return results
