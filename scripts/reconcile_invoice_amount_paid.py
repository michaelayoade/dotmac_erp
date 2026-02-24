#!/usr/bin/env python
"""
Reconcile invoice.amount_paid with actual PaymentAllocation records.

Fixes invoices where:
  1. Allocations exist but amount_paid is stale (lower than alloc sum)
  2. Updates invoice status based on corrected amount_paid

Does NOT modify allocation records — only syncs invoice fields to match
what the allocations already say.

Idempotent: re-running produces zero additional changes.

Usage:
  # Dry run (default) — shows what would change, no DB writes
  docker exec dotmac_erp_app python scripts/reconcile_invoice_amount_paid.py

  # Execute — updates invoices and commits
  docker exec dotmac_erp_app python scripts/reconcile_invoice_amount_paid.py --commit
"""

from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

DUST = Decimal("0.01")


def determine_status(
    total_amount: Decimal, amount_paid: Decimal, current_status: str
) -> str:
    """Determine the correct invoice status based on payment coverage."""
    if current_status in ("VOID", "DRAFT", "APPROVED"):
        return current_status

    balance = total_amount - amount_paid
    if balance <= DUST:
        return "PAID"
    elif amount_paid > DUST:
        return "PARTIALLY_PAID"
    else:
        return current_status


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile invoice.amount_paid from allocation records"
    )
    parser.add_argument(
        "--commit", action="store_true", help="Commit changes (default is dry run)"
    )
    parser.add_argument(
        "--month",
        help="Limit to invoices in YYYY-MM (e.g. 2026-01). Default: all invoices.",
    )
    args = parser.parse_args()

    dry_run = not args.commit
    mode = "DRY RUN" if dry_run else "COMMIT"
    logger.info("=== Invoice amount_paid Reconciliation (%s) ===", mode)

    month_filter = ""
    params: dict[str, str | float] = {"dust": float(DUST)}
    if args.month:
        month_filter = (
            "AND i.invoice_date >= :month_start AND i.invoice_date < :month_end"
        )
        params["month_start"] = f"{args.month}-01"
        # Crude next-month calc (works for YYYY-MM format)
        year, mon = args.month.split("-")
        nxt_mon = int(mon) + 1
        nxt_year = int(year)
        if nxt_mon > 12:
            nxt_mon = 1
            nxt_year += 1
        params["month_end"] = f"{nxt_year}-{nxt_mon:02d}-01"
        logger.info("Limiting to invoices in %s", args.month)

    with SessionLocal() as db:
        # Find invoices where allocation sum > amount_paid
        rows = db.execute(
            text(f"""
                SELECT
                    i.invoice_id,
                    i.invoice_number,
                    i.total_amount,
                    i.amount_paid   AS current_amount_paid,
                    i.status        AS current_status,
                    alloc.alloc_sum AS allocation_total
                FROM ar.invoice i
                JOIN LATERAL (
                    SELECT COALESCE(SUM(pa.allocated_amount), 0) AS alloc_sum
                    FROM ar.payment_allocation pa
                    WHERE pa.invoice_id = i.invoice_id
                ) alloc ON true
                WHERE i.status NOT IN ('VOID', 'DRAFT')
                  AND alloc.alloc_sum > i.amount_paid + :dust
                  {month_filter}
                ORDER BY (alloc.alloc_sum - i.amount_paid) DESC
            """),
            params,
        ).fetchall()

        if not rows:
            logger.info("No invoices need amount_paid reconciliation. All in sync.")
            if dry_run:
                db.rollback()
            return

        logger.info("Found %d invoices with stale amount_paid", len(rows))

        updated = 0
        status_changes: dict[str, int] = {}
        total_correction = Decimal("0")

        for row in rows:
            invoice_id = row[0]
            invoice_number = row[1]
            total_amount = Decimal(str(row[2]))
            current_paid = Decimal(str(row[3]))
            current_status = str(row[4])
            alloc_total = Decimal(str(row[5]))

            new_amount_paid = alloc_total
            correction = new_amount_paid - current_paid
            new_status = determine_status(total_amount, new_amount_paid, current_status)

            if correction > DUST or new_status != current_status:
                total_correction += correction

                if new_status != current_status:
                    change_key = f"{current_status} -> {new_status}"
                    status_changes[change_key] = status_changes.get(change_key, 0) + 1

                if not dry_run:
                    db.execute(
                        text("""
                            UPDATE ar.invoice
                            SET amount_paid = :new_paid,
                                status = :new_status
                            WHERE invoice_id = :inv_id
                        """),
                        {
                            "new_paid": float(new_amount_paid),
                            "new_status": new_status,
                            "inv_id": str(invoice_id),
                        },
                    )
                updated += 1

                if updated <= 20:
                    logger.info(
                        "  %-14s  paid: %12s -> %12s  status: %-16s -> %s",
                        invoice_number,
                        f"{current_paid:,.2f}",
                        f"{new_amount_paid:,.2f}",
                        current_status,
                        new_status,
                    )

        if updated > 20:
            logger.info("  ... and %d more invoices", updated - 20)

        # Summary
        logger.info("")
        logger.info("=== Summary ===")
        logger.info("  Invoices updated:       %d", updated)
        logger.info("  Total correction:       %s NGN", f"{total_correction:,.2f}")
        logger.info("")

        if status_changes:
            logger.info("  Status transitions:")
            for change, count in sorted(status_changes.items()):
                logger.info("    %-35s  %d invoices", change, count)

        if dry_run:
            db.rollback()
            logger.info("")
            logger.info("Dry run complete. Re-run with --commit to apply changes.")
        else:
            db.commit()
            logger.info("")
            logger.info("Committed %d invoice updates.", updated)


if __name__ == "__main__":
    main()
