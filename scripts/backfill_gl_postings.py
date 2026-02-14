"""
Backfill GL postings for existing records.

Finds invoices, payments, and expense claims that have a posted status
but are missing GL journal entries, and creates the entries using the
standard posting adapters.

Usage:
    # Dry run (report only)
    python scripts/backfill_gl_postings.py --dry-run

    # Process specific entity types
    python scripts/backfill_gl_postings.py --entity-type ar-invoices --execute
    python scripts/backfill_gl_postings.py --entity-type ar-payments --execute
    python scripts/backfill_gl_postings.py --entity-type expenses --execute
    python scripts/backfill_gl_postings.py --entity-type ap-invoices --execute
    python scripts/backfill_gl_postings.py --entity-type ap-payments --execute

    # Process all entity types
    python scripts/backfill_gl_postings.py --execute

    # Limit batch size (default 1000)
    python scripts/backfill_gl_postings.py --execute --batch-size 500

    # Process specific organization only
    python scripts/backfill_gl_postings.py --execute --org-id <uuid>
"""

from __future__ import annotations

import argparse
import calendar
import logging
import sys
import time
import uuid as uuid_lib
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Bootstrap the app to get DB access
sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402
from app.models.expense.expense_claim import (  # noqa: E402
    ExpenseClaim,
    ExpenseClaimStatus,
)
from app.models.finance.ap.supplier_invoice import (  # noqa: E402
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ap.supplier_payment import (  # noqa: E402
    APPaymentStatus,
    SupplierPayment,
)
from app.models.finance.ar.customer_payment import (  # noqa: E402
    CustomerPayment,
    PaymentStatus,
)
from app.models.finance.ar.invoice import Invoice, InvoiceStatus  # noqa: E402
from app.models.finance.gl.fiscal_period import (  # noqa: E402
    FiscalPeriod,
    PeriodStatus,
)
from app.models.finance.gl.fiscal_year import FiscalYear  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_gl")


# ---------------------------------------------------------------------------
# Fiscal Period Preparation
# ---------------------------------------------------------------------------


# Records with dates before this are treated as corrupt data and skipped.
MIN_VALID_DATE = date(2017, 1, 1)


def _get_date_range(
    db: Session, org_id: UUID | None
) -> tuple[date | None, date | None]:
    """Get the min/max dates across all entity types needing GL posting."""
    queries = []

    # AR Invoices
    q = select(func.min(Invoice.invoice_date), func.max(Invoice.invoice_date)).where(
        Invoice.status.in_(
            [
                InvoiceStatus.APPROVED,
                InvoiceStatus.POSTED,
                InvoiceStatus.PAID,
                InvoiceStatus.PARTIALLY_PAID,
                InvoiceStatus.OVERDUE,
            ]
        ),
        Invoice.journal_entry_id.is_(None),
    )
    if org_id:
        q = q.where(Invoice.organization_id == org_id)
    queries.append(q)

    # AR Payments
    q = select(
        func.min(CustomerPayment.payment_date), func.max(CustomerPayment.payment_date)
    ).where(
        CustomerPayment.status == PaymentStatus.CLEARED,
        CustomerPayment.journal_entry_id.is_(None),
    )
    if org_id:
        q = q.where(CustomerPayment.organization_id == org_id)
    queries.append(q)

    # Expense Claims
    q = select(
        func.min(ExpenseClaim.claim_date), func.max(ExpenseClaim.claim_date)
    ).where(
        ExpenseClaim.status.in_([ExpenseClaimStatus.APPROVED, ExpenseClaimStatus.PAID]),
        ExpenseClaim.journal_entry_id.is_(None),
    )
    if org_id:
        q = q.where(ExpenseClaim.organization_id == org_id)
    queries.append(q)

    overall_min: date | None = None
    overall_max: date | None = None

    for q in queries:
        row = db.execute(q).one()
        if row[0]:
            d_min = max(row[0], MIN_VALID_DATE)
            d_max = row[1]
            if overall_min is None or d_min < overall_min:
                overall_min = d_min
            if overall_max is None or d_max > overall_max:
                overall_max = d_max

    return overall_min, overall_max


def prepare_fiscal_periods(db: Session, org_id: UUID | None) -> list[UUID]:
    """
    Ensure fiscal periods exist for all dates needing GL posting.

    Creates missing fiscal years and monthly periods (OPEN status).
    Temporarily sets HARD_CLOSED periods to OPEN for backfill.

    Returns:
        List of fiscal_period_ids that were changed from HARD_CLOSED to OPEN
        (so they can be restored after backfill).
    """
    min_date, max_date = _get_date_range(db, org_id)
    if not min_date or not max_date:
        return []

    logger.info("Preparing fiscal periods for date range %s to %s", min_date, max_date)

    # Get all organizations that need periods
    if org_id:
        org_ids = [org_id]
    else:
        from app.models.finance.core_org.organization import Organization

        org_ids = list(db.scalars(select(Organization.organization_id)).all())

    reopened_period_ids: list[UUID] = []

    for oid in org_ids:
        # Iterate month by month from min_date to max_date
        y, m = min_date.year, min_date.month
        end_y, end_m = max_date.year, max_date.month

        while (y, m) <= (end_y, end_m):
            month_start = date(y, m, 1)
            _, last_day = calendar.monthrange(y, m)
            month_end = date(y, m, last_day)

            # Check if period exists
            existing = db.scalar(
                select(FiscalPeriod).where(
                    FiscalPeriod.organization_id == oid,
                    FiscalPeriod.start_date <= month_start,
                    FiscalPeriod.end_date >= month_start,
                )
            )

            if existing:
                # If HARD_CLOSED, temporarily open it
                if (
                    existing.status == PeriodStatus.HARD_CLOSED
                    or existing.status == PeriodStatus.SOFT_CLOSED
                ):
                    existing.status = PeriodStatus.OPEN
                    reopened_period_ids.append(existing.fiscal_period_id)
            else:
                # Create fiscal year if missing
                year_code = str(y)
                fiscal_year = db.scalar(
                    select(FiscalYear).where(
                        FiscalYear.organization_id == oid,
                        FiscalYear.year_code == year_code,
                    )
                )
                if not fiscal_year:
                    fiscal_year = FiscalYear(
                        fiscal_year_id=uuid_lib.uuid4(),
                        organization_id=oid,
                        year_code=year_code,
                        year_name=f"Fiscal Year {y}",
                        start_date=date(y, 1, 1),
                        end_date=date(y, 12, 31),
                    )
                    db.add(fiscal_year)
                    db.flush()
                    logger.info("  Created fiscal year %s", year_code)

                # Create the monthly period
                month_name = calendar.month_name[m]
                period = FiscalPeriod(
                    fiscal_period_id=uuid_lib.uuid4(),
                    organization_id=oid,
                    fiscal_year_id=fiscal_year.fiscal_year_id,
                    period_number=m,
                    period_name=f"{month_name} {y}",
                    start_date=month_start,
                    end_date=month_end,
                    status=PeriodStatus.OPEN,
                )
                db.add(period)
                logger.info("  Created fiscal period %s %s", month_name, y)

            # Next month
            m += 1
            if m > 12:
                m = 1
                y += 1

    db.flush()
    db.commit()

    if reopened_period_ids:
        logger.info(
            "  Temporarily opened %d HARD/SOFT_CLOSED periods for backfill",
            len(reopened_period_ids),
        )

    return reopened_period_ids


def restore_fiscal_periods(db: Session, period_ids: list[UUID]) -> None:
    """Restore temporarily opened periods back to HARD_CLOSED."""
    if not period_ids:
        return

    for pid in period_ids:
        period = db.get(FiscalPeriod, pid)
        if period and period.status == PeriodStatus.OPEN:
            period.status = PeriodStatus.HARD_CLOSED

    db.commit()
    logger.info("Restored %d periods back to HARD_CLOSED", len(period_ids))


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


def count_missing_gl(
    db: Session, org_id: UUID | None = None
) -> dict[str, dict[str, int]]:
    """Count records with posted status but no GL entries."""
    results: dict[str, dict[str, int]] = {}

    # AR Invoices (exclude zero-amount)
    ar_inv_q = (
        select(func.count())
        .select_from(Invoice)
        .where(
            Invoice.status.in_(
                [
                    InvoiceStatus.POSTED,
                    InvoiceStatus.PAID,
                    InvoiceStatus.PARTIALLY_PAID,
                    InvoiceStatus.OVERDUE,
                ]
            ),
            Invoice.journal_entry_id.is_(None),
            Invoice.total_amount != 0,
        )
    )
    ar_inv_total = (
        select(func.count())
        .select_from(Invoice)
        .where(
            Invoice.status.in_(
                [
                    InvoiceStatus.POSTED,
                    InvoiceStatus.PAID,
                    InvoiceStatus.PARTIALLY_PAID,
                    InvoiceStatus.OVERDUE,
                ]
            ),
        )
    )
    if org_id:
        ar_inv_q = ar_inv_q.where(Invoice.organization_id == org_id)
        ar_inv_total = ar_inv_total.where(Invoice.organization_id == org_id)
    results["ar-invoices"] = {
        "missing_gl": db.scalar(ar_inv_q) or 0,
        "total_posted": db.scalar(ar_inv_total) or 0,
    }

    # AR Payments (exclude zero-amount)
    ar_pmt_q = (
        select(func.count())
        .select_from(CustomerPayment)
        .where(
            CustomerPayment.status == PaymentStatus.CLEARED,
            CustomerPayment.journal_entry_id.is_(None),
            CustomerPayment.amount != 0,
        )
    )
    ar_pmt_total = (
        select(func.count())
        .select_from(CustomerPayment)
        .where(
            CustomerPayment.status == PaymentStatus.CLEARED,
        )
    )
    if org_id:
        ar_pmt_q = ar_pmt_q.where(CustomerPayment.organization_id == org_id)
        ar_pmt_total = ar_pmt_total.where(CustomerPayment.organization_id == org_id)
    results["ar-payments"] = {
        "missing_gl": db.scalar(ar_pmt_q) or 0,
        "total_posted": db.scalar(ar_pmt_total) or 0,
    }

    # Expense Claims (exclude zero-amount)
    exp_q = (
        select(func.count())
        .select_from(ExpenseClaim)
        .where(
            ExpenseClaim.status.in_(
                [
                    ExpenseClaimStatus.APPROVED,
                    ExpenseClaimStatus.PAID,
                ]
            ),
            ExpenseClaim.journal_entry_id.is_(None),
            func.coalesce(
                ExpenseClaim.total_approved_amount,
                ExpenseClaim.total_claimed_amount,
            )
            != 0,
        )
    )
    exp_total = (
        select(func.count())
        .select_from(ExpenseClaim)
        .where(
            ExpenseClaim.status.in_(
                [
                    ExpenseClaimStatus.APPROVED,
                    ExpenseClaimStatus.PAID,
                ]
            ),
        )
    )
    if org_id:
        exp_q = exp_q.where(ExpenseClaim.organization_id == org_id)
        exp_total = exp_total.where(ExpenseClaim.organization_id == org_id)
    results["expenses"] = {
        "missing_gl": db.scalar(exp_q) or 0,
        "total_posted": db.scalar(exp_total) or 0,
    }

    # AP Invoices (exclude zero-amount)
    ap_inv_q = (
        select(func.count())
        .select_from(SupplierInvoice)
        .where(
            SupplierInvoice.status.in_(
                [
                    SupplierInvoiceStatus.APPROVED,
                    SupplierInvoiceStatus.POSTED,
                    SupplierInvoiceStatus.PAID,
                    SupplierInvoiceStatus.PARTIALLY_PAID,
                ]
            ),
            SupplierInvoice.journal_entry_id.is_(None),
            SupplierInvoice.total_amount != 0,
        )
    )
    ap_inv_total = (
        select(func.count())
        .select_from(SupplierInvoice)
        .where(
            SupplierInvoice.status.in_(
                [
                    SupplierInvoiceStatus.APPROVED,
                    SupplierInvoiceStatus.POSTED,
                    SupplierInvoiceStatus.PAID,
                    SupplierInvoiceStatus.PARTIALLY_PAID,
                ]
            ),
        )
    )
    if org_id:
        ap_inv_q = ap_inv_q.where(SupplierInvoice.organization_id == org_id)
        ap_inv_total = ap_inv_total.where(SupplierInvoice.organization_id == org_id)
    results["ap-invoices"] = {
        "missing_gl": db.scalar(ap_inv_q) or 0,
        "total_posted": db.scalar(ap_inv_total) or 0,
    }

    # AP Payments (exclude zero-amount)
    ap_pmt_q = (
        select(func.count())
        .select_from(SupplierPayment)
        .where(
            SupplierPayment.status.in_([APPaymentStatus.SENT, APPaymentStatus.CLEARED]),
            SupplierPayment.journal_entry_id.is_(None),
            SupplierPayment.amount != 0,
        )
    )
    ap_pmt_total = (
        select(func.count())
        .select_from(SupplierPayment)
        .where(
            SupplierPayment.status.in_([APPaymentStatus.SENT, APPaymentStatus.CLEARED]),
        )
    )
    if org_id:
        ap_pmt_q = ap_pmt_q.where(SupplierPayment.organization_id == org_id)
        ap_pmt_total = ap_pmt_total.where(SupplierPayment.organization_id == org_id)
    results["ap-payments"] = {
        "missing_gl": db.scalar(ap_pmt_q) or 0,
        "total_posted": db.scalar(ap_pmt_total) or 0,
    }

    return results


# ---------------------------------------------------------------------------
# Processors
# ---------------------------------------------------------------------------


def _process_batch(
    db: Session,
    entity_name: str,
    items: list,
    id_attr: str,
    post_fn,
) -> dict[str, int]:
    """
    Process a batch of records through their ensure_gl_posted function.

    Commits every 50 records and rolls back + skips on any per-record error,
    so one failure never cascades to the rest of the batch.
    """
    posted = 0
    failed = 0

    for i, item in enumerate(items):
        try:
            if post_fn(db, item):
                posted += 1
        except Exception as exc:
            failed += 1
            db.rollback()
            logger.error(
                "  FAILED %s %s: %s",
                entity_name,
                getattr(item, id_attr, "?"),
                exc,
            )

        # Commit in batches of 50
        if (i + 1) % 50 == 0:
            try:
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.error("  Commit failed at %d: %s", i + 1, exc)
            logger.info(
                "  %s: %d/%d processed (%d posted, %d failed)",
                entity_name,
                i + 1,
                len(items),
                posted,
                failed,
            )

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("  Final commit failed: %s", exc)

    return {"total": len(items), "posted": posted, "failed": failed}


def backfill_ar_invoices(
    db: Session, batch_size: int, org_id: UUID | None = None
) -> dict[str, int]:
    """Backfill GL entries for AR invoices."""
    from app.services.finance.ar.invoice import ARInvoiceService

    stmt = (
        select(Invoice)
        .where(
            Invoice.status.in_(
                [
                    InvoiceStatus.POSTED,
                    InvoiceStatus.PAID,
                    InvoiceStatus.PARTIALLY_PAID,
                    InvoiceStatus.OVERDUE,
                ]
            ),
            Invoice.journal_entry_id.is_(None),
            Invoice.total_amount != 0,
        )
        .order_by(Invoice.invoice_date)
        .limit(batch_size)
    )
    if org_id:
        stmt = stmt.where(Invoice.organization_id == org_id)

    invoices = list(db.scalars(stmt).all())
    return _process_batch(
        db, "AR invoices", invoices, "invoice_id", ARInvoiceService.ensure_gl_posted
    )


def backfill_ar_payments(
    db: Session, batch_size: int, org_id: UUID | None = None
) -> dict[str, int]:
    """Backfill GL entries for AR payments."""
    from app.services.finance.ar.customer_payment import CustomerPaymentService

    stmt = (
        select(CustomerPayment)
        .where(
            CustomerPayment.status == PaymentStatus.CLEARED,
            CustomerPayment.journal_entry_id.is_(None),
            CustomerPayment.amount != 0,
        )
        .order_by(CustomerPayment.payment_date)
        .limit(batch_size)
    )
    if org_id:
        stmt = stmt.where(CustomerPayment.organization_id == org_id)

    payments = list(db.scalars(stmt).all())
    return _process_batch(
        db,
        "AR payments",
        payments,
        "payment_id",
        CustomerPaymentService.ensure_gl_posted,
    )


def backfill_expenses(
    db: Session, batch_size: int, org_id: UUID | None = None
) -> dict[str, int]:
    """Backfill GL entries for expense claims."""
    from app.services.expense.expense_service import ExpenseService

    stmt = (
        select(ExpenseClaim)
        .where(
            ExpenseClaim.status.in_(
                [
                    ExpenseClaimStatus.APPROVED,
                    ExpenseClaimStatus.PAID,
                ]
            ),
            ExpenseClaim.journal_entry_id.is_(None),
            func.coalesce(
                ExpenseClaim.total_approved_amount,
                ExpenseClaim.total_claimed_amount,
            )
            != 0,
        )
        .order_by(ExpenseClaim.claim_date)
        .limit(batch_size)
    )
    if org_id:
        stmt = stmt.where(ExpenseClaim.organization_id == org_id)

    claims = list(db.scalars(stmt).all())
    return _process_batch(
        db, "Expenses", claims, "claim_id", ExpenseService.ensure_gl_posted
    )


def backfill_ap_invoices(
    db: Session, batch_size: int, org_id: UUID | None = None
) -> dict[str, int]:
    """Backfill GL entries for AP (supplier) invoices."""
    from app.services.finance.ap.supplier_invoice import SupplierInvoiceService

    stmt = (
        select(SupplierInvoice)
        .where(
            SupplierInvoice.status.in_(
                [
                    SupplierInvoiceStatus.APPROVED,
                    SupplierInvoiceStatus.POSTED,
                    SupplierInvoiceStatus.PAID,
                    SupplierInvoiceStatus.PARTIALLY_PAID,
                ]
            ),
            SupplierInvoice.journal_entry_id.is_(None),
            SupplierInvoice.total_amount != 0,
        )
        .order_by(SupplierInvoice.invoice_date)
        .limit(batch_size)
    )
    if org_id:
        stmt = stmt.where(SupplierInvoice.organization_id == org_id)

    invoices = list(db.scalars(stmt).all())
    return _process_batch(
        db,
        "AP invoices",
        invoices,
        "invoice_id",
        SupplierInvoiceService.ensure_gl_posted,
    )


def backfill_ap_payments(
    db: Session, batch_size: int, org_id: UUID | None = None
) -> dict[str, int]:
    """Backfill GL entries for AP (supplier) payments."""
    from app.services.finance.ap.supplier_payment import SupplierPaymentService

    stmt = (
        select(SupplierPayment)
        .where(
            SupplierPayment.status.in_([APPaymentStatus.SENT, APPaymentStatus.CLEARED]),
            SupplierPayment.journal_entry_id.is_(None),
            SupplierPayment.amount != 0,
        )
        .order_by(SupplierPayment.payment_date)
        .limit(batch_size)
    )
    if org_id:
        stmt = stmt.where(SupplierPayment.organization_id == org_id)

    payments = list(db.scalars(stmt).all())
    return _process_batch(
        db,
        "AP payments",
        payments,
        "payment_id",
        SupplierPaymentService.ensure_gl_posted,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


ENTITY_TYPES = {
    "ar-invoices": backfill_ar_invoices,
    "ar-payments": backfill_ar_payments,
    "expenses": backfill_expenses,
    "ap-invoices": backfill_ap_invoices,
    "ap-payments": backfill_ap_payments,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill GL postings for records missing journal entries."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report missing GL entries without making changes",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually create GL entries",
    )
    parser.add_argument(
        "--entity-type",
        choices=list(ENTITY_TYPES.keys()),
        help="Process only this entity type (default: all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Max records per entity type per run (default: 1000)",
    )
    parser.add_argument(
        "--org-id",
        type=str,
        default=None,
        help="Process only this organization (UUID)",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    org_id = UUID(args.org_id) if args.org_id else None

    with SessionLocal() as db:
        # Always show the report first
        logger.info("=" * 60)
        logger.info("GL POSTING BACKFILL REPORT")
        logger.info("=" * 60)

        counts = count_missing_gl(db, org_id)
        total_missing = 0
        for entity_type, info in counts.items():
            missing = info["missing_gl"]
            total = info["total_posted"]
            pct = (missing / total * 100) if total > 0 else 0
            marker = " ***" if missing > 0 else ""
            logger.info(
                "  %-15s  %6d / %6d posted records missing GL entries (%.1f%%)%s",
                entity_type,
                missing,
                total,
                pct,
                marker,
            )
            total_missing += missing

        logger.info("-" * 60)
        logger.info("  TOTAL: %d records need GL posting", total_missing)
        logger.info("=" * 60)

        if args.dry_run:
            logger.info("DRY RUN — no changes made.")
            return

        if total_missing == 0:
            logger.info("Nothing to do — all records have GL entries.")
            return

        # Step 1: Prepare fiscal periods (create missing, open closed)
        logger.info("")
        logger.info("Step 1: Preparing fiscal periods...")
        reopened_ids = prepare_fiscal_periods(db, org_id)

        # Execute backfill
        types_to_process = (
            {args.entity_type: ENTITY_TYPES[args.entity_type]}
            if args.entity_type
            else ENTITY_TYPES
        )

        logger.info("")
        logger.info("Step 2: Starting GL backfill (chunk_size=%d)...", args.batch_size)
        logger.info("")

        grand_total = {"total": 0, "posted": 0, "failed": 0}
        overall_start = time.time()

        try:
            for entity_type, processor in types_to_process.items():
                entity_missing = counts[entity_type]["missing_gl"]
                if entity_missing == 0:
                    logger.info("[%s] No records to process — skipping.", entity_type)
                    continue

                logger.info(
                    "[%s] Starting: %d records to process in chunks of %d...",
                    entity_type,
                    entity_missing,
                    args.batch_size,
                )
                entity_start = time.time()
                entity_total = {"total": 0, "posted": 0, "failed": 0}
                chunk_num = 0

                while True:
                    chunk_num += 1
                    chunk_start = time.time()

                    result = processor(db, args.batch_size, org_id)

                    if result["total"] == 0:
                        break  # No more records

                    entity_total["total"] += result["total"]
                    entity_total["posted"] += result["posted"]
                    entity_total["failed"] += result["failed"]

                    chunk_elapsed = time.time() - chunk_start
                    rate = result["total"] / chunk_elapsed if chunk_elapsed > 0 else 0
                    pct = (
                        entity_total["total"] / entity_missing * 100
                        if entity_missing > 0
                        else 100
                    )
                    remaining_est = (
                        (entity_missing - entity_total["total"]) / rate
                        if rate > 0
                        else 0
                    )

                    logger.info(
                        "[%s] Chunk %d: %d processed (%.1f/s) — "
                        "cumulative: %d/%d (%.1f%%) posted=%d failed=%d "
                        "— ETA: %.0fm",
                        entity_type,
                        chunk_num,
                        result["total"],
                        rate,
                        entity_total["total"],
                        entity_missing,
                        pct,
                        entity_total["posted"],
                        entity_total["failed"],
                        remaining_est / 60,
                    )

                    # If all records in this chunk failed, stop to avoid infinite loop
                    if result["posted"] == 0 and result["failed"] == result["total"]:
                        logger.error(
                            "[%s] All records in chunk failed — stopping to avoid loop.",
                            entity_type,
                        )
                        break

                    if result["total"] < args.batch_size:
                        break  # Last chunk was partial, we're done

                entity_elapsed = time.time() - entity_start
                logger.info(
                    "[%s] DONE in %.1fs — %d processed, %d posted, %d failed",
                    entity_type,
                    entity_elapsed,
                    entity_total["total"],
                    entity_total["posted"],
                    entity_total["failed"],
                )
                grand_total["total"] += entity_total["total"]
                grand_total["posted"] += entity_total["posted"]
                grand_total["failed"] += entity_total["failed"]
        finally:
            # Step 3: Restore period statuses even if backfill fails
            logger.info("")
            logger.info("Step 3: Restoring fiscal period statuses...")
            restore_fiscal_periods(db, reopened_ids)

        overall_elapsed = time.time() - overall_start
        logger.info("")
        logger.info("=" * 60)
        logger.info(
            "BACKFILL COMPLETE in %.1fs: %d processed, %d posted, %d failed",
            overall_elapsed,
            grand_total["total"],
            grand_total["posted"],
            grand_total["failed"],
        )
        logger.info("=" * 60)

        # Show remaining
        remaining = count_missing_gl(db, org_id)
        remaining_total = sum(v["missing_gl"] for v in remaining.values())
        if remaining_total > 0:
            logger.info("Remaining records needing GL posting: %d", remaining_total)


if __name__ == "__main__":
    main()
