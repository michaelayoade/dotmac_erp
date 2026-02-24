#!/usr/bin/env python
"""
FIFO Auto-Allocation: Link unallocated Splynx payments to invoices.

Uses First-In-First-Out ordering — oldest payment covers oldest unpaid
invoice, splitting across multiple invoices as needed.  This is Tier-B
allocation, complementing the exact 1:1 matching in Tier-A.

Only creates ar.payment_allocation records.  Does NOT modify
invoice.amount_paid or invoice.status (Splynx sync owns those fields).

Idempotent: re-running produces zero additional changes because
remaining balances already reflect previously committed allocations.

Usage:
  # Dry run (default) — shows what would be allocated, no DB changes
  docker exec dotmac_erp_app python scripts/allocate_splynx_fifo.py

  # Execute — creates allocations and commits
  docker exec dotmac_erp_app python scripts/allocate_splynx_fifo.py --commit
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FIFO auto-allocate Splynx payments to invoices"
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Commit changes (default is dry run)",
    )
    args = parser.parse_args()

    dry_run = not args.commit
    mode = "DRY RUN" if dry_run else "COMMIT"
    logger.info("=== FIFO Splynx Payment Allocation (%s) ===", mode)

    with SessionLocal() as db:
        # Import inside to avoid circular imports at module level
        # Discover the single org (all Splynx data belongs to one org)
        from sqlalchemy import text

        from app.services.finance.ar.fifo_allocation_service import (
            FIFOAllocationService,
        )

        org_row = db.execute(
            text("""
                SELECT DISTINCT organization_id
                FROM ar.customer_payment
                WHERE splynx_id IS NOT NULL
                LIMIT 1
            """)
        ).fetchone()

        if org_row is None:
            logger.info("No Splynx payments found. Nothing to do.")
            return

        org_id = org_row[0]
        logger.info("Organization: %s", org_id)

        service = FIFOAllocationService(db)
        result = service.allocate_for_org(org_id, dry_run=dry_run)

        # Print summary
        logger.info("")
        logger.info("=== Summary ===")
        logger.info("  Customers processed:  %d", result.customers_processed)
        logger.info("  Allocations created:  %d", result.allocations_created)
        logger.info(
            "  Total allocated:      %s NGN",
            f"{result.total_allocated:,.2f}",
        )
        logger.info("  Prepayment customers: %d", len(result.prepayment_customers))
        logger.info("  Errors:               %d", len(result.errors))

        if result.prepayment_customers:
            logger.info("")
            logger.info(
                "--- Customers with remaining payment balance (prepayments) ---"
            )
            for cust_id, name, excess in sorted(
                result.prepayment_customers, key=lambda x: x[2], reverse=True
            ):
                logger.info("  %-40s %s NGN", name, f"{excess:>14,.2f}")
            total_excess = sum(e for _, _, e in result.prepayment_customers)
            logger.info(
                "  %-40s %s NGN",
                "TOTAL PREPAYMENTS",
                f"{total_excess:>14,.2f}",
            )

        if result.errors:
            logger.info("")
            logger.info("--- Errors ---")
            for err in result.errors:
                logger.error("  %s", err)

        if dry_run:
            db.rollback()
            logger.info("")
            logger.info("Dry run complete. Re-run with --commit to apply changes.")
        else:
            db.commit()
            logger.info("")
            logger.info("Committed %d allocations.", result.allocations_created)


if __name__ == "__main__":
    main()
