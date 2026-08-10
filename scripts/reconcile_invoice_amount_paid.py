#!/usr/bin/env python
"""
Reconcile `invoice.amount_paid` against its payment allocations.

**This is now a thin CLI adapter.** The decision lives in
`app.services.finance.ar.amount_paid_reconciler`, and the scheduled path is
`app.tasks.ar_allocation.reconcile_invoice_amount_paid`.

`ar.payment_allocation` is the authority for what was applied to an invoice;
`invoice.amount_paid` is a running total maintained alongside it. When they
disagree the allocations win, and the status is re-derived through
`ar.payment_status`.

Two defects the extraction fixed, neither visible from here before:

* **The query had no organization filter**, and this script opened a raw
  `SessionLocal()`, so neither the ORM listener nor the RLS GUC bounded it.
  This one WRITES, which makes an unscoped run worse than a read.
* **Money went through `float()`** on the way into the UPDATE —
  `float(new_amount_paid)` against a numeric column. Amounts stay `Decimal`
  end to end now.

Usage:
  docker exec dotmac_erp_app python scripts/reconcile_invoice_amount_paid.py \
      --org-id <uuid>                       # dry run
  docker exec dotmac_erp_app python scripts/reconcile_invoice_amount_paid.py \
      --org-id <uuid> --month 2026-01 --commit
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session_context import session_for_org
from app.models.batch_operation import BatchOperationType
from app.services.batch_operation import batch_operation
from app.services.finance.ar.amount_paid_reconciler import reconcile_amount_paid

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile invoice.amount_paid against payment allocations"
    )
    parser.add_argument(
        "--org-id",
        required=True,
        type=uuid.UUID,
        help="Organization to run against (no default: this is multi-tenant)",
    )
    parser.add_argument(
        "--actor-id",
        type=uuid.UUID,
        default=SYSTEM_ACTOR_ID,
        help="Recorded as who ran this, on the BatchOperation record",
    )
    parser.add_argument("--month", help="Limit to invoices in YYYY-MM")
    parser.add_argument(
        "--commit", action="store_true", help="Apply (default is a dry run)"
    )
    args = parser.parse_args()

    mode = "EXECUTE" if args.commit else "DRY RUN"
    logger.info("=== amount_paid reconciliation (%s) — org %s ===", mode, args.org_id)
    if args.month:
        logger.info("Limited to %s", args.month)

    with (
        session_for_org(args.org_id) as db,
        batch_operation(
            db,
            organization_id=args.org_id,
            operation_type=BatchOperationType.BULK_UPDATE,
            operation_name="reconcile_invoice_amount_paid",
            started_by_id=args.actor_id,
            description=f"Manual CLI run ({mode})",
            source_file=__file__,
        ) as tally,
    ):
        result = reconcile_amount_paid(
            db,
            organization_id=args.org_id,
            month=args.month,
            dry_run=not args.commit,
        )
        tally.updated = result.updated
        tally.skipped = result.examined - result.updated

    logger.info(
        "examined=%d updated=%d total_correction=%s",
        result.examined,
        result.updated,
        result.total_correction,
    )
    for change, count in sorted(result.status_changes.items()):
        logger.info("  %-35s %d invoices", change, count)
    if not args.commit and result.updated:
        logger.info("DRY RUN — re-run with --commit to apply.")


if __name__ == "__main__":
    main()
