#!/usr/bin/env python
"""
Sync active customers to Paystack.

**This is now a thin CLI adapter.** The decision lives in
`app.services.finance.payments.paystack_customer_sync`, and the scheduled
path is `app.tasks.payments_sync.sync_customers_to_paystack`.

Idempotent: each customer is looked up in Paystack by email first, then
updated or created, so re-running converges rather than duplicating.

`--org-id` is required. This carried `ORG_ID = UUID("0000...0001")` at module
level, so a multi-tenant system had a payment-provider sync for one tenant.
The session now comes from `session_for_org`, and the run is recorded as a
`BatchOperation`.

Worth knowing before scheduling it: two network round-trips per customer, a
lookup then a write.

Usage:
  docker exec dotmac_erp_app python scripts/sync_customers_to_paystack.py \
      --org-id <uuid>              # dry run
  docker exec dotmac_erp_app python scripts/sync_customers_to_paystack.py \
      --org-id <uuid> --execute
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
from app.services.finance.payments.paystack_customer_sync import sync_customers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync active customers to Paystack")
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
    parser.add_argument(
        "--execute", action="store_true", help="Actually sync (default is a dry run)"
    )
    args = parser.parse_args()

    mode = "EXECUTE" if args.execute else "DRY RUN"
    logger.info("=== Paystack customer sync (%s) — org %s ===", mode, args.org_id)

    with (
        session_for_org(args.org_id) as db,
        batch_operation(
            db,
            organization_id=args.org_id,
            operation_type=BatchOperationType.SYNC,
            operation_name="sync_customers_to_paystack",
            started_by_id=args.actor_id,
            description=f"Manual CLI run ({mode})",
            source_file=__file__,
        ) as tally,
    ):
        result = sync_customers(
            db, organization_id=args.org_id, dry_run=not args.execute
        )
        tally.created = result.created
        tally.updated = result.updated
        tally.skipped = result.skipped_no_email
        tally.failed = len(result.errors)

    logger.info(
        "total=%d created=%d updated=%d skipped_no_email=%d errors=%d",
        result.total,
        result.created,
        result.updated,
        result.skipped_no_email,
        len(result.errors),
    )
    for err in result.errors[:20]:
        logger.error("  %s", err)
    if not args.execute and result.total:
        logger.info("DRY RUN — re-run with --execute to sync.")


if __name__ == "__main__":
    main()
