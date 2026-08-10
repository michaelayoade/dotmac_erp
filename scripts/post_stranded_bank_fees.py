#!/usr/bin/env python
"""
Post APPROVED journals a source module left stranded before the ledger.

**This is now a thin CLI adapter.** The decision lives in
`app.services.finance.gl.stranded_fee_posting`, and the scheduled path is
`app.tasks.gl_posting.post_stranded_source_journals`.

A journal is *stranded* when its source module created and approved it but
posting never completed — it sits APPROVED with no ledger batch behind it.

Three things the extraction fixed:

* **A hardcoded fiscal year.** `TARGET_YEAR_CODE = "FY2025"` at module level
  made a one-year repair look like a general tool. Year, source module and
  document type are all parameters now — the mechanism is "post stranded
  journals from a source", and bank fees were one instance of it.
* **No organization filter.** The query filtered year, status, module and
  document type, never tenant, and the script opened a raw `SessionLocal()`
  per journal so nothing at any layer bounded it.
* **Replays were detected by substring** — `if "Already posted" in msg`.
  `PostingResult.idempotent_replay` now carries that as a flag set where the
  condition is known, so rewording the message cannot silently reclassify
  every replay as a fresh posting.

Each journal is posted in its own transaction, so one bad entry does not roll
back the ones before it.

Usage:
  docker exec dotmac_erp_app python scripts/post_stranded_bank_fees.py \
      --org-id <uuid> --year FY2025                    # dry run
  docker exec dotmac_erp_app python scripts/post_stranded_bank_fees.py \
      --org-id <uuid> --year FY2025 --execute --limit 1
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
from app.services.finance.gl.stranded_fee_posting import (
    StrandedPostingResult,
    find_stranded_journals,
    post_one,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("post_stranded_bank_fees")
for noisy in ("sqlalchemy.engine", "app.services.finance.gl.ledger_posting"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Post APPROVED journals stranded before the ledger"
    )
    parser.add_argument(
        "--org-id",
        required=True,
        type=uuid.UUID,
        help="Organization to run against (no default: this is multi-tenant)",
    )
    parser.add_argument("--year", required=True, help="Fiscal year code, e.g. FY2025")
    parser.add_argument("--source-module", default="BANKING")
    parser.add_argument("--source-doc-type", default="BANK_FEE")
    parser.add_argument(
        "--actor-id",
        type=uuid.UUID,
        default=SYSTEM_ACTOR_ID,
        help="Recorded as who ran this, on the BatchOperation record",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--execute", action="store_true", help="Post (default is a dry run)"
    )
    args = parser.parse_args()

    mode = "EXECUTE" if args.execute else "DRY RUN"
    logger.info(
        "=== Post stranded %s/%s journals (%s) — org %s, %s ===",
        args.source_module,
        args.source_doc_type,
        mode,
        args.org_id,
        args.year,
    )

    result = StrandedPostingResult()

    with (
        session_for_org(args.org_id) as db,
        batch_operation(
            db,
            organization_id=args.org_id,
            operation_type=BatchOperationType.MIGRATION,
            operation_name="post_stranded_source_journals",
            started_by_id=args.actor_id,
            description=f"{args.source_module}/{args.source_doc_type} {args.year} ({mode})",
            source_file=__file__,
        ) as tally,
    ):
        journals = find_stranded_journals(
            db,
            organization_id=args.org_id,
            year_code=args.year,
            source_module=args.source_module,
            source_document_type=args.source_doc_type,
            limit=args.limit,
        )
        result.found = len(journals)
        logger.info("Found %d stranded journal(s)", result.found)

        if args.execute:
            for journal in journals:
                ok, replay, msg = post_one(
                    db, journal, source_module=args.source_module
                )
                if ok and replay:
                    result.already_posted += 1
                elif ok:
                    result.posted += 1
                else:
                    result.failures.append((journal.journal_number, msg))
                    logger.error("FAIL %s: %s", journal.journal_number, msg)

        tally.created = result.posted
        tally.skipped = result.already_posted + (
            result.found if not args.execute else 0
        )
        tally.failed = len(result.failures)

    logger.info(
        "found=%d posted=%d already_posted=%d failed=%d",
        result.found,
        result.posted,
        result.already_posted,
        len(result.failures),
    )
    if not args.execute and result.found:
        logger.info("DRY RUN — re-run with --execute to post.")
    return 1 if result.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
