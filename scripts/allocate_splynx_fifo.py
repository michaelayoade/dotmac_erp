#!/usr/bin/env python
"""
FIFO Auto-Allocation: link unallocated Splynx payments to invoices.

Oldest payment covers oldest unpaid invoice, splitting across invoices as
needed. Tier-B allocation, complementing the exact 1:1 matching in Tier-A
(`allocate_exact_match_payments.py`).

Only creates `ar.payment_allocation` records. Does NOT modify
`invoice.amount_paid` or `invoice.status` — the Splynx sync owns those.

Idempotent: re-running produces zero additional changes, because remaining
balances already reflect previously committed allocations.

**This is now a thin CLI adapter.** The allocation decision has always lived
in `app.services.finance.ar.fifo_allocation_service`; what lived here was the
choice of ORGANIZATION, and it chose badly:

    SELECT DISTINCT organization_id FROM ar.customer_payment
    WHERE splynx_id IS NOT NULL LIMIT 1

That inferred the tenant from data and took whatever an unordered scan
returned first, justified by a comment that all Splynx data belongs to one
org. A second organization with Splynx payments would have been skipped
silently, and which one ran depended on the query plan. `--org-id` is now
required; the scheduled path (`app.tasks.ar_allocation
.allocate_splynx_payments_fifo`) iterates every organization that has any.

Usage:
  docker exec dotmac_erp_app python scripts/allocate_splynx_fifo.py \
      --org-id <uuid>            # dry run
  docker exec dotmac_erp_app python scripts/allocate_splynx_fifo.py \
      --org-id <uuid> --commit
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session_context import cross_org_session, session_for_org
from app.models.batch_operation import BatchOperationType
from app.services.batch_operation import batch_operation
from app.services.finance.ar.allocation_backlog import (
    organizations_with_splynx_payments,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FIFO auto-allocate Splynx payments to invoices"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--org-id", type=uuid.UUID, help="Organization to run against")
    group.add_argument(
        "--list-orgs",
        action="store_true",
        help="List organizations that have Splynx payments, and exit",
    )
    parser.add_argument(
        "--actor-id",
        type=uuid.UUID,
        default=SYSTEM_ACTOR_ID,
        help="Recorded as who ran this, on the BatchOperation record",
    )
    parser.add_argument(
        "--commit", action="store_true", help="Apply (default is a dry run)"
    )
    args = parser.parse_args()

    if args.list_orgs:
        with cross_org_session() as db:
            for org_id in organizations_with_splynx_payments(db):
                logger.info("%s", org_id)
        return

    from app.services.finance.ar.fifo_allocation_service import FIFOAllocationService

    mode = "COMMIT" if args.commit else "DRY RUN"
    logger.info("=== FIFO Splynx allocation (%s) — org %s ===", mode, args.org_id)

    with (
        session_for_org(args.org_id) as db,
        batch_operation(
            db,
            organization_id=args.org_id,
            operation_type=BatchOperationType.BULK_UPDATE,
            operation_name="allocate_splynx_fifo",
            started_by_id=args.actor_id,
            description=f"Manual CLI run ({mode})",
            source_file=__file__,
        ) as tally,
    ):
        result = FIFOAllocationService(db).allocate_for_org(
            args.org_id, dry_run=not args.commit
        )
        tally.created = result.allocations_created
        tally.failed = len(result.errors)

    logger.info(
        "customers=%d allocations=%d total=%s prepayments=%d errors=%d",
        result.customers_processed,
        result.allocations_created,
        result.total_allocated,
        len(result.prepayment_customers),
        len(result.errors),
    )
    for cust_id, name, excess in sorted(
        result.prepayment_customers, key=lambda x: x[2], reverse=True
    )[:20]:
        logger.info("  prepayment  %-40s %s", name, excess)
    for err in result.errors[:20]:
        logger.error("  %s", err)
    if not args.commit:
        logger.info("DRY RUN — re-run with --commit to apply.")


if __name__ == "__main__":
    main()
