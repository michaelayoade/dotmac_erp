#!/usr/bin/env python
"""
Close fiscal periods that pass the pre-close gate.

**This is now a thin CLI adapter.** The decision lives in
`app.services.finance.gl.period_close`. There is deliberately NO scheduled
task: closing a period is a one-way accounting act that an operator decides,
not something a scheduler should start doing unattended.

The gate, previously buried in a 334-line script:

* **No unposted journals** — DRAFT, SUBMITTED and APPROVED all count. Closing
  over an APPROVED journal strands it permanently, since it can never be
  posted into a closed period.
* **The period balances** — debits and credits over POSTED journals differ by
  less than the shared imbalance tolerance.

`--force` still bypasses both, because there are real situations that need
it. What changed is that the bypass is explicit, reported, and recorded on
the BatchOperation, so a forced close leaves a trace of what it overrode.

`--org-id` is required: this carried `ORG_ID = UUID("0000...0001")` at module
level, so a multi-tenant system had a period-close tool for one tenant.

Usage:
  docker exec dotmac_erp_app python scripts/close_fiscal_periods.py \
      --org-id <uuid> --through 2025-12-31              # assess only
  docker exec dotmac_erp_app python scripts/close_fiscal_periods.py \
      --org-id <uuid> --through 2025-12-31 --execute
  docker exec dotmac_erp_app python scripts/close_fiscal_periods.py \
      --org-id <uuid> --year 2025 --execute --hard
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session_context import session_for_org
from app.models.batch_operation import BatchOperationType
from app.services.batch_operation import batch_operation
from app.services.finance.gl.period_close import assess_periods, close_periods

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def main() -> None:
    parser = argparse.ArgumentParser(description="Close fiscal periods")
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
        help="Recorded as who closed these periods",
    )
    parser.add_argument(
        "--through",
        type=dt.date.fromisoformat,
        help="Only periods ending on or before this date",
    )
    parser.add_argument("--year", type=int, help="Only periods starting in this year")
    parser.add_argument(
        "--hard",
        action="store_true",
        help="Hard close (soft-closes first if still OPEN)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Close even over unposted journals or an imbalance",
    )
    parser.add_argument(
        "--execute", action="store_true", help="Actually close (default is assess only)"
    )
    args = parser.parse_args()

    mode = "EXECUTE" if args.execute else "ASSESS"
    logger.info("=== Fiscal period close (%s) — org %s ===", mode, args.org_id)

    with (
        session_for_org(args.org_id) as db,
        batch_operation(
            db,
            organization_id=args.org_id,
            operation_type=BatchOperationType.BULK_UPDATE,
            operation_name="close_fiscal_periods",
            started_by_id=args.actor_id,
            description=(
                f"{'Hard' if args.hard else 'Soft'} close ({mode})"
                + (" — FORCED over blockers" if args.force else "")
            ),
            source_file=__file__,
        ) as tally,
    ):
        for period in assess_periods(
            db, organization_id=args.org_id, through_date=args.through, year=args.year
        ):
            if period.is_ready:
                logger.info("  READY    %-24s %s", period.period_name, period.year_name)
            else:
                logger.info(
                    "  BLOCKED  %-24s %s — %s",
                    period.period_name,
                    period.year_name,
                    "; ".join(period.blockers),
                )

        result = close_periods(
            db,
            organization_id=args.org_id,
            closed_by_user_id=args.actor_id,
            through_date=args.through,
            year=args.year,
            hard=args.hard,
            force=args.force,
            dry_run=not args.execute,
        )
        tally.updated = result.closed
        tally.skipped = result.blocked
        tally.failed = len(result.errors)

    logger.info(
        "assessed=%d ready=%d blocked=%d closed=%d forced=%d errors=%d",
        result.assessed,
        result.ready,
        result.blocked,
        result.closed,
        result.forced,
        len(result.errors),
    )
    if result.forced:
        logger.warning("%d period(s) were closed over their blockers", result.forced)
    for err in result.errors[:20]:
        logger.error("  %s", err)
    if not args.execute and result.ready:
        logger.info("ASSESS only — re-run with --execute to close.")


if __name__ == "__main__":
    main()
