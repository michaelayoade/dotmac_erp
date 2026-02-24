#!/usr/bin/env python
"""
Tier 1 Auto-Allocation: Link unallocated payments to invoices with exact amount match.

Finds CLEARED payments with no allocation records where the same customer has
exactly ONE open invoice with a matching total_amount. Creates the allocation
record and updates the invoice's amount_paid / status.

Only processes unambiguous matches (1 payment : 1 invoice) to avoid misallocation.

Usage:
  # Dry run (default) — shows what would be allocated, no DB changes
  docker exec dotmac_erp_app python scripts/allocate_exact_match_payments.py

  # Execute — creates allocations and commits
  docker exec dotmac_erp_app python scripts/allocate_exact_match_payments.py --execute

  # Limit to specific year
  docker exec dotmac_erp_app python scripts/allocate_exact_match_payments.py --year 2025

  # Limit batch size
  docker exec dotmac_erp_app python scripts/allocate_exact_match_payments.py --execute --limit 50
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.ar.payment_allocation import PaymentAllocation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Tolerance for amount comparison (sub-cent)
AMOUNT_TOLERANCE = Decimal("0.01")


@dataclass
class MatchCandidate:
    """A payment matched to exactly one invoice."""

    payment_id: UUID
    payment_number: str
    payment_date: date
    payment_amount: Decimal
    customer_id: UUID
    customer_name: str
    invoice_id: UUID
    invoice_number: str
    invoice_total: Decimal
    invoice_outstanding: Decimal


def find_exact_match_candidates(
    db: Session,  # noqa: F821
    *,
    year: int | None = None,
    limit: int | None = None,
) -> list[MatchCandidate]:
    """Find unallocated payments with exactly one matching open invoice.

    Criteria:
    - Payment is CLEARED with amount > 0
    - Payment has NO records in ar.payment_allocation
    - Same customer has exactly ONE open invoice where
      abs(invoice.total_amount - payment.amount) < tolerance
    - Invoice status is POSTED, OVERDUE, or PARTIALLY_PAID
    - Invoice has outstanding balance > 0
    """
    date_filter = ""
    if year:
        date_filter = f"AND cp.payment_date >= '{year}-01-01' AND cp.payment_date < '{year + 1}-01-01'"

    limit_clause = f"LIMIT {limit}" if limit else ""

    sql = text(f"""
        WITH unalloc AS (
            SELECT cp.payment_id, cp.payment_number, cp.payment_date,
                   cp.amount, cp.customer_id
            FROM ar.customer_payment cp
            WHERE cp.status::text = 'CLEARED'
              AND cp.amount > 0
              {date_filter}
              AND NOT EXISTS (
                  SELECT 1 FROM ar.payment_allocation pa
                  WHERE pa.payment_id = cp.payment_id
              )
        ),
        with_matches AS (
            SELECT u.payment_id, u.payment_number, u.payment_date,
                   u.amount AS payment_amount, u.customer_id,
                   c.legal_name AS customer_name,
                   i.invoice_id, i.invoice_number, i.total_amount,
                   (i.total_amount - COALESCE(i.amount_paid, 0)) AS outstanding,
                   count(*) OVER (PARTITION BY u.payment_id) AS match_count
            FROM unalloc u
            JOIN ar.customer c ON c.customer_id = u.customer_id
            JOIN ar.invoice i ON i.customer_id = u.customer_id
                AND abs(i.total_amount - u.amount) < :tolerance
                AND i.status::text IN ('POSTED', 'OVERDUE', 'PARTIALLY_PAID')
                AND (i.total_amount - COALESCE(i.amount_paid, 0)) > 0
        )
        SELECT payment_id, payment_number, payment_date, payment_amount,
               customer_id, customer_name,
               invoice_id, invoice_number, total_amount, outstanding
        FROM with_matches
        WHERE match_count = 1
        ORDER BY payment_date, payment_number
        {limit_clause}
    """)

    rows = db.execute(sql, {"tolerance": float(AMOUNT_TOLERANCE)}).fetchall()

    return [
        MatchCandidate(
            payment_id=r[0],
            payment_number=r[1],
            payment_date=r[2],
            payment_amount=r[3],
            customer_name=r[5],
            customer_id=r[4],
            invoice_id=r[6],
            invoice_number=r[7],
            invoice_total=r[8],
            invoice_outstanding=r[9],
        )
        for r in rows
    ]


def allocate_payment_to_invoice(
    db: Session,  # noqa: F821
    candidate: MatchCandidate,
) -> bool:
    """Create allocation record and update invoice amount_paid / status.

    Re-reads the invoice from DB to get the live outstanding balance,
    protecting against multiple payments targeting the same invoice
    within a single batch.

    Returns True if allocation was created, False if skipped (no balance left).
    """
    # Re-read invoice to get live balance (not stale snapshot from query)
    invoice = db.get(Invoice, candidate.invoice_id)
    if invoice is None:
        raise ValueError(f"Invoice {candidate.invoice_id} not found")

    live_outstanding = invoice.total_amount - invoice.amount_paid
    if live_outstanding <= 0:
        logger.warning(
            "  SKIP: %s -> %s: invoice already fully paid (live outstanding=%s)",
            candidate.payment_number,
            candidate.invoice_number,
            f"{live_outstanding:,.2f}",
        )
        return False

    allocated_amount = min(candidate.payment_amount, live_outstanding)

    # Create allocation record
    allocation = PaymentAllocation(
        payment_id=candidate.payment_id,
        invoice_id=candidate.invoice_id,
        allocated_amount=allocated_amount,
        discount_taken=Decimal("0"),
        write_off_amount=Decimal("0"),
        exchange_difference=Decimal("0"),
        allocation_date=candidate.payment_date,
    )
    db.add(allocation)

    # Update invoice
    invoice.amount_paid = invoice.amount_paid + allocated_amount
    if invoice.amount_paid >= invoice.total_amount:
        invoice.status = InvoiceStatus.PAID
    else:
        invoice.status = InvoiceStatus.PARTIALLY_PAID

    db.flush()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-allocate payments with exact single-invoice amount match"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually commit changes (default is dry run)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Filter payments to a specific year (e.g. 2025)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of allocations to process",
    )
    args = parser.parse_args()

    mode = "EXECUTE" if args.execute else "DRY RUN"
    logger.info("=== Tier 1 Exact-Match Auto-Allocation (%s) ===", mode)
    if args.year:
        logger.info("Filtering to year: %d", args.year)
    if args.limit:
        logger.info("Batch limit: %d", args.limit)

    with SessionLocal() as db:
        candidates = find_exact_match_candidates(db, year=args.year, limit=args.limit)
        logger.info("Found %d exact-match candidates", len(candidates))

        if not candidates:
            logger.info("Nothing to allocate.")
            return

        # Summary stats
        total_amount = sum(c.payment_amount for c in candidates)
        full_match = sum(
            1 for c in candidates if c.invoice_outstanding >= c.payment_amount
        )
        partial_match = len(candidates) - full_match
        logger.info(
            "  Full-amount matches: %d, Partial (invoice has prior payments): %d",
            full_match,
            partial_match,
        )
        logger.info("  Total payment amount: %s", f"{total_amount:,.2f}")

        # Process — track invoices seen to detect multi-payment collisions in dry run
        allocated = 0
        skipped = 0
        errors: list[str] = []
        seen_invoices: dict[UUID, int] = {}  # invoice_id -> allocation count

        for c in candidates:
            # Detect multi-payment collision (same invoice matched by >1 payment)
            seen_invoices[c.invoice_id] = seen_invoices.get(c.invoice_id, 0) + 1
            if seen_invoices[c.invoice_id] > 1:
                logger.warning(
                    "  SKIP: %s -> %s: invoice already targeted by another "
                    "payment in this batch (collision #%d)",
                    c.payment_number,
                    c.invoice_number,
                    seen_invoices[c.invoice_id],
                )
                skipped += 1
                continue

            alloc_amount = min(c.payment_amount, c.invoice_outstanding)
            new_status = (
                "PAID"
                if (c.invoice_total - c.invoice_outstanding + c.payment_amount)
                >= c.invoice_total - AMOUNT_TOLERANCE
                else "PARTIALLY_PAID"
            )

            logger.info(
                "  %s (%s) -> %s: allocate %s (invoice outstanding %s -> %s)",
                c.payment_number,
                c.payment_date,
                c.invoice_number,
                f"{alloc_amount:,.2f}",
                f"{c.invoice_outstanding:,.2f}",
                new_status,
            )

            if args.execute:
                try:
                    created = allocate_payment_to_invoice(db, c)
                    if created:
                        allocated += 1
                    else:
                        skipped += 1
                except Exception as e:
                    logger.exception(
                        "  FAILED: %s -> %s: %s",
                        c.payment_number,
                        c.invoice_number,
                        e,
                    )
                    errors.append(f"{c.payment_number} -> {c.invoice_number}: {e}")
                    db.rollback()
            else:
                allocated += 1

        # Commit or rollback
        if args.execute:
            if errors:
                logger.warning(
                    "Completed with %d errors. Successfully allocated %d/%d.",
                    len(errors),
                    allocated,
                    len(candidates),
                )
            db.commit()
            logger.info("Committed %d allocations.", allocated)
        else:
            db.rollback()
            logger.info(
                "Dry run complete: %d allocations would be created, "
                "%d skipped (invoice collision). "
                "Re-run with --execute to apply.",
                allocated,
                skipped,
            )

        # Final summary
        logger.info("=== Summary ===")
        logger.info("  Candidates found: %d", len(candidates))
        logger.info("  Allocated: %d", allocated)
        logger.info("  Skipped (invoice collision): %d", skipped)
        logger.info("  Errors: %d", len(errors))
        total_allocated = sum(
            min(c.payment_amount, c.invoice_outstanding)
            for c in candidates
            if seen_invoices.get(c.invoice_id, 0) <= 1
        )
        logger.info("  Total allocated amount: %s NGN", f"{total_allocated:,.2f}")
        if skipped:
            logger.info(
                "  NOTE: %d payments skipped due to invoice collision. "
                "These may be candidates for Tier 2 (partial allocation).",
                skipped,
            )
        for err in errors:
            logger.info("  ERROR: %s", err)


if __name__ == "__main__":
    main()
