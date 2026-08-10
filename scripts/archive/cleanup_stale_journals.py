"""
Clean up stale/duplicate journal entries and post remaining APPROVED journals.

Actions:
1. Void 3 stale reclassification attempts (DRAFT/SUBMITTED)
2. Delete 7 empty AR INVOICE shells (APPROVED, 0 lines)
3. Post 26 balanced BANKING interbank transfers (reopen/post/reclose periods)
4. Skip 2 unbalanced IMPORT reversals (need manual review)

Usage:
    python scripts/cleanup_stale_journals.py --dry-run
    python scripts/cleanup_stale_journals.py --execute
"""

from __future__ import annotations

import argparse
import logging
import sys
from uuid import UUID

from sqlalchemy import text

sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cleanup_stale_journals")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_USER_ID = UUID("ef36328f-2343-4649-afa0-ab1bfd4ec6f0")  # Michael Ayoade

# Stale reclassification journal IDs (DRAFT/SUBMITTED, never posted)
STALE_RECLASS_IDS = [
    "1cc8fcb7-46f9-4223-9f35-1ffad3a7502b",  # JE202602-51721 SUBMITTED
    "aa8a21b1-34a6-4e48-8e94-e318c51890b3",  # JE202602-51702 DRAFT
    "258d44df-4011-42d8-a621-fc5ea4295e4b",  # JE202602-51686 SUBMITTED
]


def void_stale_reclassifications(db: object, *, execute: bool) -> int:
    """Void stale reclassification attempts."""
    logger.info("Step 1: Void stale reclassification journals")

    for je_id in STALE_RECLASS_IDS:
        row = db.execute(
            text("""
                SELECT journal_number, status FROM gl.journal_entry
                WHERE journal_entry_id = :je_id
                  AND organization_id = :org_id
            """),
            {"je_id": je_id, "org_id": str(ORG_ID)},
        ).one_or_none()

        if not row:
            logger.warning("  %s not found, skipping", je_id)
            continue

        logger.info("  %s (status=%s)", row[0], row[1])

        if execute:
            db.execute(
                text("""
                    UPDATE gl.journal_entry
                    SET status = 'VOID'
                    WHERE journal_entry_id = :je_id
                      AND organization_id = :org_id
                      AND status IN ('DRAFT', 'SUBMITTED')
                """),
                {"je_id": je_id, "org_id": str(ORG_ID)},
            )

    count = len(STALE_RECLASS_IDS)
    logger.info("  %s: %d journals", "Voided" if execute else "Would void", count)
    return count


def delete_empty_ar_shells(db: object, *, execute: bool) -> int:
    """Delete APPROVED AR invoice journals with 0 lines (empty shells)."""
    logger.info("")
    logger.info("Step 2: Delete empty AR invoice journal shells")

    rows = db.execute(
        text("""
            SELECT je.journal_entry_id, je.journal_number, je.description
            FROM gl.journal_entry je
            WHERE je.organization_id = :org_id
              AND je.status = 'APPROVED'
              AND je.source_module = 'AR'
              AND je.source_document_type = 'INVOICE'
              AND NOT EXISTS (
                  SELECT 1 FROM gl.journal_entry_line jel
                  WHERE jel.journal_entry_id = je.journal_entry_id
              )
            ORDER BY je.journal_number
        """),
        {"org_id": str(ORG_ID)},
    ).all()

    for r in rows:
        logger.info("  %s: %s", r[1], r[2][:60] if r[2] else "(no description)")

    if execute and rows:
        ids = [str(r[0]) for r in rows]
        db.execute(
            text("""
                UPDATE gl.journal_entry
                SET status = 'VOID'
                WHERE journal_entry_id = ANY(:ids)
                  AND organization_id = :org_id
            """),
            {"ids": ids, "org_id": str(ORG_ID)},
        )

    logger.info("  %s: %d journals", "Voided" if execute else "Would void", len(rows))
    return len(rows)


def post_banking_transfers(db: object, *, execute: bool) -> tuple[int, int]:
    """Post balanced BANKING interbank transfers."""
    logger.info("")
    logger.info("Step 3: Post balanced BANKING interbank transfers")

    rows = db.execute(
        text("""
            SELECT je.journal_entry_id, je.journal_number, je.entry_date,
                   je.description,
                   fp.fiscal_period_id, fp.period_name, fp.status as period_status,
                   ABS(SUM(jel.debit_amount) - SUM(jel.credit_amount)) as imbalance
            FROM gl.journal_entry je
            JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
            LEFT JOIN gl.fiscal_period fp ON fp.fiscal_period_id = je.fiscal_period_id
            WHERE je.organization_id = :org_id
              AND je.status = 'APPROVED'
              AND je.source_module = 'BANKING'
            GROUP BY je.journal_entry_id, je.journal_number, je.entry_date,
                     je.description, fp.fiscal_period_id, fp.period_name, fp.status
            HAVING ABS(SUM(jel.debit_amount) - SUM(jel.credit_amount)) < 0.01
            ORDER BY je.entry_date
        """),
        {"org_id": str(ORG_ID)},
    ).all()

    logger.info("  Found %d balanced banking journals", len(rows))

    if not execute:
        for r in rows:
            logger.info(
                "    %s  %s  period=%s (%s)  %s",
                r[1],
                r[2],
                r[5],
                r[6],
                (r[3] or "")[:50],
            )
        logger.info("  DRY RUN — would post %d journals", len(rows))
        return len(rows), 0

    from uuid import uuid4

    from app.services.finance.gl.fiscal_period import FiscalPeriodService
    from app.services.finance.gl.journal import JournalService

    # Group by period to reopen/close efficiently
    period_journals: dict[str, list] = {}
    for r in rows:
        pid = str(r[4]) if r[4] else "NO_PERIOD"
        if pid not in period_journals:
            period_journals[pid] = {"status": r[6], "name": r[5], "journals": []}
        period_journals[pid]["journals"].append(r)

    posted = 0
    errors = 0

    for pid, info in period_journals.items():
        reopen_session_id = None

        # Reopen if needed
        if info["status"] == "SOFT_CLOSED":
            reopen_session_id = uuid4()
            logger.info("  Reopening period '%s'...", info["name"])
            FiscalPeriodService.reopen_period(
                db=db,
                organization_id=ORG_ID,
                fiscal_period_id=UUID(pid),
                reopened_by_user_id=SYSTEM_USER_ID,
                reopen_session_id=reopen_session_id,
            )
            db.flush()

        # Post journals
        for r in info["journals"]:
            try:
                JournalService.post_journal(
                    db=db,
                    organization_id=ORG_ID,
                    journal_entry_id=r[0],
                    posted_by_user_id=SYSTEM_USER_ID,
                    reopen_session_id=reopen_session_id,
                )
                posted += 1
            except Exception as e:
                logger.warning("  FAILED %s: %s", r[1], e)
                errors += 1

        # Re-close if we reopened
        if reopen_session_id:
            logger.info("  Re-closing period '%s'...", info["name"])
            FiscalPeriodService.soft_close_period(
                db=db,
                organization_id=ORG_ID,
                fiscal_period_id=UUID(pid),
                closed_by_user_id=SYSTEM_USER_ID,
            )

    logger.info("  Posted: %d, Errors: %d", posted, errors)
    return posted, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up stale journals.")
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Do it")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    execute = args.execute

    with SessionLocal() as db:
        logger.info("=" * 60)
        logger.info("JOURNAL CLEANUP")
        logger.info("=" * 60)

        voided_reclass = void_stale_reclassifications(db, execute=execute)
        voided_ar = delete_empty_ar_shells(db, execute=execute)
        posted_banking, banking_errors = post_banking_transfers(db, execute=execute)

        logger.info("")
        logger.info("=" * 60)
        logger.info("SUMMARY")
        logger.info("=" * 60)
        logger.info("  Stale reclassifications voided: %d", voided_reclass)
        logger.info("  Empty AR shells voided:         %d", voided_ar)
        logger.info("  Banking transfers posted:       %d", posted_banking)
        if banking_errors:
            logger.info("  Banking transfer errors:        %d", banking_errors)
        logger.info("")
        logger.info("  Remaining APPROVED (skip):")
        logger.info("    2 unbalanced IMPORT reversals (need manual review)")

        if execute:
            db.commit()
            logger.info("")
            logger.info("Changes committed.")
        else:
            logger.info("")
            logger.info("DRY RUN — no changes made.")


if __name__ == "__main__":
    main()
