#!/usr/bin/env python
"""
Tier-A auto-allocation: payments with exactly one matching invoice.

Finds CLEARED payments with no allocation records where the same customer has
exactly ONE open invoice whose total matches the payment amount. Only
unambiguous matches are processed — a payment matching two invoices is left
alone, because guessing which one it settles is how misallocations happen.
Tier-B (`allocate_splynx_fifo.py`) handles the remainder.

**This is now a thin CLI adapter.** The decision lives in
`app.services.finance.ar.exact_match_allocation`, and the scheduled path is
`app.tasks.ar_allocation.allocate_exact_match_payments`.

Two defects the extraction fixed, both invisible from here before:

* **The query had no organization filter at all** — not a wrong one, none. It
  joined `ar.invoice` to `ar.customer_payment` on `customer_id` alone, and
  this script opened a raw `SessionLocal()`, so neither the ORM listener nor
  the RLS GUC was primed. Nothing at any layer bounded it to a tenant; it
  survived on customer ids not colliding across organizations in practice.
* **Year and limit were spliced into the SQL as strings.** Both came from
  argparse `int`, so it was not exploitable — but SQL built by formatting
  stops being safe the moment an argument becomes a string. Both are bound
  parameters now.

Usage:
  docker exec dotmac_erp_app python scripts/allocate_exact_match_payments.py \
      --org-id <uuid>                       # dry run
  docker exec dotmac_erp_app python scripts/allocate_exact_match_payments.py \
      --org-id <uuid> --year 2025 --commit
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
from app.services.finance.ar.exact_match_allocation import allocate_exact_matches

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-allocate payments with an exact single-invoice match"
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
    parser.add_argument("--year", type=int, default=None, help="Limit to a year")
    parser.add_argument("--limit", type=int, default=None, help="Max allocations")
    parser.add_argument(
        "--commit", action="store_true", help="Apply (default is a dry run)"
    )
    args = parser.parse_args()

    mode = "EXECUTE" if args.commit else "DRY RUN"
    logger.info("=== Exact-match allocation (%s) — org %s ===", mode, args.org_id)

    with (
        session_for_org(args.org_id) as db,
        batch_operation(
            db,
            organization_id=args.org_id,
            operation_type=BatchOperationType.BULK_UPDATE,
            operation_name="allocate_exact_match_payments",
            started_by_id=args.actor_id,
            description=f"Manual CLI run ({mode})",
            source_file=__file__,
        ) as tally,
    ):
        result = allocate_exact_matches(
            db,
            organization_id=args.org_id,
            year=args.year,
            limit=args.limit,
            dry_run=not args.commit,
        )
        tally.created = result.allocated
        tally.skipped = result.skipped + (result.candidates if not args.commit else 0)
        tally.failed = len(result.errors)

    logger.info(
        "candidates=%d allocated=%d skipped=%d errors=%d total=%s",
        result.candidates,
        result.allocated,
        result.skipped,
        len(result.errors),
        result.total_allocated,
    )
    for err in result.errors[:20]:
        logger.error("  %s", err)
    if not args.commit and result.candidates:
        logger.info("DRY RUN — re-run with --commit to allocate.")


if __name__ == "__main__":
    main()
