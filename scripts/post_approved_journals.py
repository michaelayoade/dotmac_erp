#!/usr/bin/env python
"""
Post APPROVED journal entries that never reached POSTED.

**This is now a thin CLI adapter, not the owner.** The decision lives in
`app.services.finance.gl.posting_backlog`, and the scheduled path is
`app.tasks.gl_posting.post_approved_journal_backlog`. Prefer the task; this
exists for a break-glass run against one organization.

The two rules this script used to own now live in the service and are
testable: journals whose debits and credits differ by less than the imbalance
tolerance are postable, and only OPEN/REOPENED periods (or journals with no
period at all) accept a posting. Unbalanced entries and closed periods are
SKIPPED — an unbalanced journal is a data defect to investigate, and a closed
period is a decision to respect, not one a backlog job may reverse.

What changed:

* `--org-id` is required. This carried `ORG_ID = UUID("0000...0001")` at
  module level, so a multi-tenant system had a maintenance tool that could
  only ever serve one tenant.
* The session comes from `session_for_org`, which primes the ORM listener AND
  the PostgreSQL RLS GUC.
* The run is recorded as a `BatchOperation`, visible at
  /admin/batch-operations.

Usage:
  docker exec dotmac_erp_app python scripts/post_approved_journals.py \
      --org-id <uuid>            # dry run
  docker exec dotmac_erp_app python scripts/post_approved_journals.py \
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
from app.services.finance.gl.posting_backlog import post_approved_journals

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post APPROVED journal entries that never reached POSTED"
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
        "--commit", action="store_true", help="Actually post (default is a dry run)"
    )
    args = parser.parse_args()

    mode = "EXECUTE" if args.commit else "DRY RUN"
    logger.info("=== Post approved journals (%s) — org %s ===", mode, args.org_id)

    with (
        session_for_org(args.org_id) as db,
        batch_operation(
            db,
            organization_id=args.org_id,
            operation_type=BatchOperationType.SCRIPT,
            operation_name="post_approved_journals",
            started_by_id=args.actor_id,
            description=f"Manual CLI run ({mode})",
            source_file=__file__,
        ) as tally,
    ):
        result = post_approved_journals(
            db,
            organization_id=args.org_id,
            posted_by_user_id=args.actor_id,
            dry_run=not args.commit,
        )
        tally.created = result.posted
        tally.skipped = result.unbalanced + result.closed_period
        tally.failed = len(result.errors)

    logger.info(
        "found=%d postable=%d posted=%d unbalanced=%d closed_period=%d errors=%d",
        result.found,
        result.postable,
        result.posted,
        result.unbalanced,
        result.closed_period,
        len(result.errors),
    )
    for err in result.errors[:20]:
        logger.warning("  %s", err)
    if not args.commit and result.postable:
        logger.info("DRY RUN — re-run with --commit to post these journals.")


if __name__ == "__main__":
    main()
