#!/usr/bin/env python
"""
Post AP supplier invoices that are missing GL journal entries.

**This is now a thin CLI adapter, not the owner.** The decision lives in
`app.services.finance.ap.posting_backlog`, and the scheduled path is
`app.tasks.ap_posting.post_unposted_ap_invoices`. Prefer the task; this exists
for a break-glass run against one organization.

What changed, and why it matters:

* `--org-id` is required. This script used to carry
  `ORG_ID = UUID("0000...0001")` at module level, so a multi-tenant system had
  a maintenance tool that could only ever serve one tenant.
* The session comes from `session_for_org`, which primes the ORM listener AND
  the PostgreSQL RLS GUC. The old version ran
  ``SET app.current_organization_id = '{ORG_ID}'`` through an f-string, which
  set one layer of two — and did it by string interpolation.
* The run is recorded as a `BatchOperation`, so it shows up under
  /admin/batch-operations with who ran it and what it touched. A script run
  used to leave no trace at all.

Idempotent: invoices that already have a journal entry are not selected, and
each posting carries a stable idempotency key.

Usage:
  # Dry run (default)
  docker exec dotmac_erp_app python scripts/post_unposted_ap_invoices.py \
      --org-id 00000000-0000-0000-0000-000000000001

  # Execute
  docker exec dotmac_erp_app python scripts/post_unposted_ap_invoices.py \
      --org-id <uuid> --commit
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
from app.services.finance.ap.posting_backlog import post_unposted_invoices

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post AP supplier invoices missing their GL journal entry"
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
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually post (default is a dry run)",
    )
    args = parser.parse_args()

    mode = "EXECUTE" if args.commit else "DRY RUN"
    logger.info("=== Post unposted AP invoices (%s) — org %s ===", mode, args.org_id)

    with (
        session_for_org(args.org_id) as db,
        batch_operation(
            db,
            organization_id=args.org_id,
            operation_type=BatchOperationType.SCRIPT,
            operation_name="post_unposted_ap_invoices",
            started_by_id=args.actor_id,
            description=f"Manual CLI run ({mode})",
            source_file=__file__,
        ) as tally,
    ):
        result = post_unposted_invoices(
            db,
            organization_id=args.org_id,
            fallback_user_id=args.actor_id,
            dry_run=not args.commit,
        )
        tally.created = result.posted
        tally.skipped = result.skipped + (result.found if not args.commit else 0)
        tally.failed = result.errors

    logger.info(
        "found=%d posted=%d skipped=%d errors=%d total=%s",
        result.found,
        result.posted,
        result.skipped,
        result.errors,
        result.total_amount,
    )
    if not args.commit and result.found:
        logger.info("DRY RUN — re-run with --commit to post these invoices.")


if __name__ == "__main__":
    main()
