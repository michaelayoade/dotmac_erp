#!/usr/bin/env python
"""
Void phantom duplicate Splynx customer payments (double-sync cleanup).

A re-import that ran without the ExternalSync mapping caused the Splynx
payment sync to INSERT a second CLEARED CustomerPayment for payments that
already existed — same ``correlation_id`` (``splynx-pmt-<id>``), same amount,
posted to GL, but with NO allocation. ~798 such phantoms (~NGN 39.8M) inflate
GL cash and AR collections. The sync bug itself is fixed in
``app/services/splynx/sync/_payments.py`` (fallback idempotency guard); this
script remediates the rows created before that fix.

SAFETY — only the clean phantom signature is voided:
  * grouped by correlation_id with >1 CLEARED payment, AND
  * the voided copy has ZERO allocations AND is NOT bank-reconciled, AND
  * at least one *keeper* (allocated or reconciled) remains in the group.
Any group that does not present exactly (n-1) clean phantoms + >=1 keeper is
SKIPPED and reported for manual Finance review. Voiding goes through
``CustomerPaymentService.void_payment`` which creates an idempotent GL reversal
journal, so re-running is safe.

THIS CHANGES FINANCIAL RECORDS. Requires Finance sign-off and a DB backup
before running with --execute.

Usage:
  # Dry run (default) — reports the plan, no DB changes
  docker exec dotmac_erp_app python scripts/void_splynx_duplicate_payments.py

  # Execute — voids phantoms and commits (after sign-off + backup)
  docker exec dotmac_erp_app python scripts/void_splynx_duplicate_payments.py --execute

  # Limit batch size
  docker exec dotmac_erp_app python scripts/void_splynx_duplicate_payments.py --execute --limit 100
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import SessionLocal
from app.services.finance.ar.customer_payment import CustomerPaymentService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
VOID_REASON = (
    "Phantom Splynx double-sync duplicate (see void_splynx_duplicate_payments.py)"
)

# Each row: one payment in a duplicated correlation_id group, with the flags
# needed to classify keeper vs phantom.
_GROUP_SQL = text(
    """
    WITH dup AS (
        SELECT correlation_id
        FROM ar.customer_payment
        WHERE organization_id = :org
          AND status = 'CLEARED'
          AND correlation_id LIKE 'splynx-pmt-%'
        GROUP BY correlation_id
        HAVING COUNT(*) > 1
    )
    SELECT
        cp.correlation_id,
        cp.payment_id,
        cp.payment_number,
        cp.amount,
        (cp.journal_entry_id IS NOT NULL) AS posted_gl,
        EXISTS (
            SELECT 1 FROM ar.payment_allocation pa
            WHERE pa.payment_id = cp.payment_id
        ) AS has_alloc,
        EXISTS (
            SELECT 1 FROM banking.bank_statement_line_matches m
            WHERE m.source_type = 'CUSTOMER_PAYMENT'
              AND m.source_id = cp.payment_id
              AND m.match_state = 'confirmed'
        ) AS reconciled
    FROM ar.customer_payment cp
    JOIN dup ON dup.correlation_id = cp.correlation_id
    WHERE cp.organization_id = :org
      AND cp.status = 'CLEARED'
    ORDER BY cp.correlation_id, cp.payment_number
    """
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually void phantoms and commit (default is dry run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of phantom payments to void",
    )
    args = parser.parse_args()

    mode = "EXECUTE" if args.execute else "DRY RUN"
    logger.info("=== Void Splynx duplicate payments (%s) ===", mode)

    with SessionLocal() as db:
        db.execute(text(f"SET app.current_organization_id = '{ORG_ID}'"))

        # Group the rows by correlation_id.
        groups: dict[str, list[dict]] = {}
        for row in db.execute(_GROUP_SQL, {"org": str(ORG_ID)}).mappings():
            groups.setdefault(row["correlation_id"], []).append(dict(row))

        logger.info("Found %d duplicated correlation_id groups", len(groups))

        voided = 0
        reversed_amount = 0.0
        skipped_groups = 0

        for corr_id, rows in groups.items():
            keepers = [r for r in rows if r["has_alloc"] or r["reconciled"]]
            phantoms = [r for r in rows if not r["has_alloc"] and not r["reconciled"]]

            # Conservative gate: exactly (n-1) clean phantoms + >=1 keeper.
            if not keepers or len(phantoms) != len(rows) - 1:
                skipped_groups += 1
                logger.warning(
                    "  SKIP %s: %d rows, %d keepers, %d clean phantoms "
                    "(ambiguous — needs manual review)",
                    corr_id,
                    len(rows),
                    len(keepers),
                    len(phantoms),
                )
                continue

            for ph in phantoms:
                if args.limit is not None and voided >= args.limit:
                    logger.info("Batch limit (%d) reached", args.limit)
                    break

                logger.info(
                    "  VOID phantom %s (%s, NGN %s, posted_gl=%s) — keep %s",
                    ph["payment_number"],
                    corr_id,
                    f"{ph['amount']:,.2f}",
                    ph["posted_gl"],
                    keepers[0]["payment_number"],
                )

                if args.execute:
                    try:
                        CustomerPaymentService.void_payment(
                            db,
                            organization_id=ORG_ID,
                            payment_id=ph["payment_id"],
                            voided_by_user_id=SYSTEM_USER_ID,
                            reason=VOID_REASON,
                        )
                    except Exception as e:  # noqa: BLE001 - report & continue
                        logger.error(
                            "    FAILED to void %s: %s", ph["payment_number"], e
                        )
                        continue

                voided += 1
                reversed_amount += float(ph["amount"])

            if args.limit is not None and voided >= args.limit:
                break

        if args.execute:
            db.commit()
            logger.info(
                "DONE. Voided %d phantom payments (NGN %s reversed); "
                "%d groups skipped for manual review.",
                voided,
                f"{reversed_amount:,.2f}",
                skipped_groups,
            )
        else:
            logger.info(
                "DRY RUN. Would void %d phantom payments (NGN %s); "
                "%d groups skipped for manual review. Re-run with --execute.",
                voided,
                f"{reversed_amount:,.2f}",
                skipped_groups,
            )


if __name__ == "__main__":
    main()
