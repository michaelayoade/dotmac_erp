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
import logging
import sys
import time
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_gl")


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


def count_missing_gl(
    db: Session, org_id: UUID | None = None
) -> dict[str, dict[str, int]]:
    """Count records with posted status but no GL entries."""
    results: dict[str, dict[str, int]] = {}

    # AR Invoices
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

    # AR Payments
    ar_pmt_q = (
        select(func.count())
        .select_from(CustomerPayment)
        .where(
            CustomerPayment.status == PaymentStatus.CLEARED,
            CustomerPayment.journal_entry_id.is_(None),
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

    # Expense Claims
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

    # AP Invoices
    ap_inv_q = (
        select(func.count())
        .select_from(SupplierInvoice)
        .where(
            SupplierInvoice.status.in_(
                [
                    SupplierInvoiceStatus.POSTED,
                    SupplierInvoiceStatus.PAID,
                    SupplierInvoiceStatus.PARTIALLY_PAID,
                ]
            ),
            SupplierInvoice.journal_entry_id.is_(None),
        )
    )
    ap_inv_total = (
        select(func.count())
        .select_from(SupplierInvoice)
        .where(
            SupplierInvoice.status.in_(
                [
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

    # AP Payments
    ap_pmt_q = (
        select(func.count())
        .select_from(SupplierPayment)
        .where(
            SupplierPayment.status == APPaymentStatus.SENT,
            SupplierPayment.journal_entry_id.is_(None),
        )
    )
    ap_pmt_total = (
        select(func.count())
        .select_from(SupplierPayment)
        .where(
            SupplierPayment.status == APPaymentStatus.SENT,
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
        )
        .order_by(Invoice.invoice_date)
        .limit(batch_size)
    )
    if org_id:
        stmt = stmt.where(Invoice.organization_id == org_id)

    invoices = list(db.scalars(stmt).all())
    posted = 0
    failed = 0

    for i, invoice in enumerate(invoices):
        if ARInvoiceService.ensure_gl_posted(db, invoice):
            posted += 1
        else:
            failed += 1

        # Commit in batches of 100
        if (i + 1) % 100 == 0:
            db.commit()
            logger.info(
                "  AR invoices: %d/%d processed (%d posted, %d failed)",
                i + 1,
                len(invoices),
                posted,
                failed,
            )

    db.commit()
    return {"total": len(invoices), "posted": posted, "failed": failed}


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
        )
        .order_by(CustomerPayment.payment_date)
        .limit(batch_size)
    )
    if org_id:
        stmt = stmt.where(CustomerPayment.organization_id == org_id)

    payments = list(db.scalars(stmt).all())
    posted = 0
    failed = 0

    for i, payment in enumerate(payments):
        if CustomerPaymentService.ensure_gl_posted(db, payment):
            posted += 1
        else:
            failed += 1

        if (i + 1) % 100 == 0:
            db.commit()
            logger.info(
                "  AR payments: %d/%d processed (%d posted, %d failed)",
                i + 1,
                len(payments),
                posted,
                failed,
            )

    db.commit()
    return {"total": len(payments), "posted": posted, "failed": failed}


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
        )
        .order_by(ExpenseClaim.claim_date)
        .limit(batch_size)
    )
    if org_id:
        stmt = stmt.where(ExpenseClaim.organization_id == org_id)

    claims = list(db.scalars(stmt).all())
    posted = 0
    failed = 0

    for i, claim in enumerate(claims):
        if ExpenseService.ensure_gl_posted(db, claim):
            posted += 1
        else:
            failed += 1

        if (i + 1) % 100 == 0:
            db.commit()
            logger.info(
                "  Expenses: %d/%d processed (%d posted, %d failed)",
                i + 1,
                len(claims),
                posted,
                failed,
            )

    db.commit()
    return {"total": len(claims), "posted": posted, "failed": failed}


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
                    SupplierInvoiceStatus.POSTED,
                    SupplierInvoiceStatus.PAID,
                    SupplierInvoiceStatus.PARTIALLY_PAID,
                ]
            ),
            SupplierInvoice.journal_entry_id.is_(None),
        )
        .order_by(SupplierInvoice.invoice_date)
        .limit(batch_size)
    )
    if org_id:
        stmt = stmt.where(SupplierInvoice.organization_id == org_id)

    invoices = list(db.scalars(stmt).all())
    posted = 0
    failed = 0

    for i, invoice in enumerate(invoices):
        if SupplierInvoiceService.ensure_gl_posted(db, invoice):
            posted += 1
        else:
            failed += 1

        if (i + 1) % 100 == 0:
            db.commit()
            logger.info(
                "  AP invoices: %d/%d processed (%d posted, %d failed)",
                i + 1,
                len(invoices),
                posted,
                failed,
            )

    db.commit()
    return {"total": len(invoices), "posted": posted, "failed": failed}


def backfill_ap_payments(
    db: Session, batch_size: int, org_id: UUID | None = None
) -> dict[str, int]:
    """Backfill GL entries for AP (supplier) payments."""
    from app.services.finance.ap.supplier_payment import SupplierPaymentService

    stmt = (
        select(SupplierPayment)
        .where(
            SupplierPayment.status == APPaymentStatus.SENT,
            SupplierPayment.journal_entry_id.is_(None),
        )
        .order_by(SupplierPayment.payment_date)
        .limit(batch_size)
    )
    if org_id:
        stmt = stmt.where(SupplierPayment.organization_id == org_id)

    payments = list(db.scalars(stmt).all())
    posted = 0
    failed = 0

    for i, payment in enumerate(payments):
        if SupplierPaymentService.ensure_gl_posted(db, payment):
            posted += 1
        else:
            failed += 1

        if (i + 1) % 100 == 0:
            db.commit()
            logger.info(
                "  AP payments: %d/%d processed (%d posted, %d failed)",
                i + 1,
                len(payments),
                posted,
                failed,
            )

    db.commit()
    return {"total": len(payments), "posted": posted, "failed": failed}


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

        # Execute backfill
        types_to_process = (
            {args.entity_type: ENTITY_TYPES[args.entity_type]}
            if args.entity_type
            else ENTITY_TYPES
        )

        logger.info("")
        logger.info("Starting GL backfill (batch_size=%d)...", args.batch_size)
        logger.info("")

        grand_total = {"total": 0, "posted": 0, "failed": 0}
        for entity_type, processor in types_to_process.items():
            if counts[entity_type]["missing_gl"] == 0:
                logger.info("[%s] No records to process — skipping.", entity_type)
                continue

            logger.info(
                "[%s] Processing up to %d records...", entity_type, args.batch_size
            )
            start = time.time()

            result = processor(db, args.batch_size, org_id)

            elapsed = time.time() - start
            logger.info(
                "[%s] Done in %.1fs — %d processed, %d posted, %d failed",
                entity_type,
                elapsed,
                result["total"],
                result["posted"],
                result["failed"],
            )
            grand_total["total"] += result["total"]
            grand_total["posted"] += result["posted"]
            grand_total["failed"] += result["failed"]

        logger.info("")
        logger.info("=" * 60)
        logger.info(
            "BACKFILL COMPLETE: %d processed, %d posted, %d failed",
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
            logger.info("Run again to process the next batch.")


if __name__ == "__main__":
    main()
