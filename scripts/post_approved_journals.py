"""
Post 123 journal entries stuck in APPROVED status.

Background:
These journals were created by various import/sync processes (AR invoices,
Paystack bank transfers, expense reimbursements) but were never posted to the
GL. They are all in OPEN fiscal periods, so posting should succeed.

Usage:
    python scripts/post_approved_journals.py --dry-run
    python scripts/post_approved_journals.py --execute
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
logger = logging.getLogger("post_approved_journals")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")


def get_approved_journals(db: object) -> list[dict]:
    """Get all APPROVED journal entries with balance info."""
    rows = db.execute(
        text("""
            SELECT je.journal_entry_id, je.journal_number, je.entry_date,
                   je.description, je.source_module, je.source_document_type,
                   fp.period_name, fp.status AS period_status,
                   SUM(jel.debit_amount) AS total_debit,
                   SUM(jel.credit_amount) AS total_credit,
                   ABS(SUM(jel.debit_amount) - SUM(jel.credit_amount)) AS imbalance
            FROM gl.journal_entry je
            JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
            LEFT JOIN gl.fiscal_period fp ON fp.fiscal_period_id = je.fiscal_period_id
            WHERE je.organization_id = :org_id
              AND je.status = 'APPROVED'
            GROUP BY je.journal_entry_id, je.journal_number, je.entry_date,
                     je.description, je.source_module, je.source_document_type,
                     fp.period_name, fp.status
            ORDER BY je.entry_date
        """),
        {"org_id": str(ORG_ID)},
    ).all()
    return [
        {
            "journal_entry_id": str(r[0]),
            "journal_number": r[1],
            "entry_date": r[2],
            "description": (r[3] or "")[:60],
            "source_module": r[4],
            "source_document_type": r[5],
            "period_name": r[6],
            "period_status": r[7],
            "total_debit": r[8],
            "total_credit": r[9],
            "imbalance": float(r[10]),
        }
        for r in rows
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Post APPROVED journal entries to GL.")
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Post journals")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    with SessionLocal() as db:
        journals = get_approved_journals(db)

        logger.info("=" * 60)
        logger.info("POST APPROVED JOURNAL ENTRIES")
        logger.info("=" * 60)
        logger.info("  Found %d APPROVED journals", len(journals))

        if not journals:
            logger.info("  Nothing to post.")
            return

        # Categorize
        balanced = [j for j in journals if j["imbalance"] < 0.01]
        unbalanced = [j for j in journals if j["imbalance"] >= 0.01]
        closed_period = [
            j for j in balanced if j["period_status"] not in ("OPEN", "REOPENED", None)
        ]

        # Group by source
        by_source: dict[str, int] = {}
        for j in journals:
            key = f"{j['source_module']}/{j['source_document_type'] or '(none)'}"
            by_source[key] = by_source.get(key, 0) + 1

        logger.info("")
        logger.info("  By source:")
        for src, count in sorted(by_source.items(), key=lambda x: -x[1]):
            logger.info("    %-40s %d", src, count)
        logger.info("")
        logger.info("  Balanced (postable):    %d", len(balanced) - len(closed_period))
        logger.info("  In closed period:       %d", len(closed_period))
        logger.info("  Unbalanced (skipped):   %d", len(unbalanced))

        if unbalanced:
            logger.info("")
            logger.info("  Unbalanced journals (will be skipped):")
            for j in unbalanced:
                logger.info(
                    "    %s  date=%s  imbalance=%.2f  %s",
                    j["journal_number"],
                    j["entry_date"],
                    j["imbalance"],
                    j["description"],
                )

        if closed_period:
            logger.info("")
            logger.info("  Journals in closed periods (will be skipped):")
            for j in closed_period:
                logger.info(
                    "    %s  period=%s (%s)",
                    j["journal_number"],
                    j["period_name"],
                    j["period_status"],
                )

        logger.info("=" * 60)

        if args.dry_run:
            logger.info("DRY RUN — no changes made.")
            return

        # Import JournalService inside execute block to avoid import issues
        from app.services.finance.gl.journal import JournalService

        posted = 0
        skipped = 0
        errors: list[str] = []

        postable = [
            j for j in balanced if j["period_status"] in ("OPEN", "REOPENED", None)
        ]

        for j in postable:
            try:
                JournalService.post_journal(
                    db=db,
                    organization_id=ORG_ID,
                    journal_entry_id=UUID(j["journal_entry_id"]),
                    posted_by_user_id=SYSTEM_USER_ID,
                )
                posted += 1
                if posted % 20 == 0:
                    logger.info("  Posted %d / %d ...", posted, len(postable))
                    db.flush()
            except Exception as e:
                err_msg = f"{j['journal_number']}: {e}"
                errors.append(err_msg)
                logger.warning("  FAILED: %s", err_msg)
                skipped += 1

        db.commit()

        logger.info("")
        logger.info("RESULTS:")
        logger.info("  Posted:   %d", posted)
        logger.info(
            "  Skipped:  %d (unbalanced or closed period)",
            len(unbalanced) + len(closed_period),
        )
        logger.info("  Errors:   %d", len(errors))
        if errors:
            for err in errors[:10]:
                logger.info("    %s", err)


if __name__ == "__main__":
    main()
