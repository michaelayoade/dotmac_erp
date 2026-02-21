"""
Reopen the 'test' fiscal period, post its 44 APPROVED journals, then re-close it.

The 'test' period (2026-01-01 to 2026-12-31) is SOFT_CLOSED, blocking 44
APPROVED journals from being posted. This script reopens it temporarily,
posts all APPROVED journals, then soft-closes it again.

Usage:
    python scripts/reopen_post_reclose_test_period.py --dry-run
    python scripts/reopen_post_reclose_test_period.py --execute
"""

from __future__ import annotations

import argparse
import logging
import sys
from uuid import UUID, uuid4

from sqlalchemy import text

sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reopen_test_period")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")


def get_test_period(db: object) -> dict | None:
    """Find the 'test' fiscal period."""
    row = db.execute(
        text("""
            SELECT fp.fiscal_period_id, fp.period_name, fp.status,
                   fp.start_date, fp.end_date
            FROM gl.fiscal_period fp
            WHERE fp.organization_id = :org_id
              AND fp.period_name = 'test'
            LIMIT 1
        """),
        {"org_id": str(ORG_ID)},
    ).one_or_none()
    if not row:
        return None
    return {
        "fiscal_period_id": str(row[0]),
        "period_name": row[1],
        "status": row[2],
        "start_date": row[3],
        "end_date": row[4],
    }


def get_approved_in_period(db: object, period_id: str) -> list[dict]:
    """Get APPROVED journals in the given period."""
    rows = db.execute(
        text("""
            SELECT je.journal_entry_id, je.journal_number, je.entry_date,
                   je.description, je.source_module
            FROM gl.journal_entry je
            WHERE je.organization_id = :org_id
              AND je.fiscal_period_id = :period_id
              AND je.status = 'APPROVED'
            ORDER BY je.entry_date
        """),
        {"org_id": str(ORG_ID), "period_id": period_id},
    ).all()
    return [
        {
            "journal_entry_id": str(r[0]),
            "journal_number": r[1],
            "entry_date": r[2],
            "description": (r[3] or "")[:50],
            "source_module": r[4],
        }
        for r in rows
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reopen test period, post journals, re-close."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Do it")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    with SessionLocal() as db:
        period = get_test_period(db)
        if not period:
            logger.info("No 'test' fiscal period found.")
            return

        logger.info("=" * 60)
        logger.info("REOPEN → POST → RE-CLOSE 'test' PERIOD")
        logger.info("=" * 60)
        logger.info(
            "  Period: %s (%s → %s), status: %s",
            period["period_name"],
            period["start_date"],
            period["end_date"],
            period["status"],
        )

        journals = get_approved_in_period(db, period["fiscal_period_id"])
        logger.info("  APPROVED journals: %d", len(journals))

        if not journals:
            logger.info("  No journals to post.")
            return

        if args.dry_run:
            logger.info("")
            logger.info("  Would post:")
            for j in journals[:10]:
                logger.info(
                    "    %s  %s  %s  %s",
                    j["journal_number"],
                    j["entry_date"],
                    j["source_module"],
                    j["description"],
                )
            if len(journals) > 10:
                logger.info("    ... and %d more", len(journals) - 10)
            logger.info("")
            logger.info("DRY RUN — no changes made.")
            return

        # Step 1: Reopen the test period
        from app.services.finance.gl.fiscal_period import FiscalPeriodService
        from app.services.finance.gl.journal import JournalService

        period_id = UUID(period["fiscal_period_id"])
        reopen_session_id = uuid4()

        if period["status"] == "SOFT_CLOSED":
            logger.info("")
            logger.info("Step 1: Reopening test period...")
            FiscalPeriodService.reopen_period(
                db=db,
                organization_id=ORG_ID,
                fiscal_period_id=period_id,
                reopened_by_user_id=SYSTEM_USER_ID,
                reopen_session_id=reopen_session_id,
            )
            db.flush()
            logger.info("  Reopened (session: %s)", reopen_session_id)
        else:
            logger.info("  Period already %s, proceeding...", period["status"])
            reopen_session_id = None

        # Step 2: Post all APPROVED journals
        logger.info("")
        logger.info("Step 2: Posting %d journals...", len(journals))
        posted = 0
        errors: list[str] = []

        for j in journals:
            try:
                JournalService.post_journal(
                    db=db,
                    organization_id=ORG_ID,
                    journal_entry_id=UUID(j["journal_entry_id"]),
                    posted_by_user_id=SYSTEM_USER_ID,
                    reopen_session_id=reopen_session_id,
                )
                posted += 1
            except Exception as e:
                err_msg = f"{j['journal_number']}: {e}"
                errors.append(err_msg)
                logger.warning("  FAILED: %s", err_msg)

        logger.info("  Posted: %d / %d", posted, len(journals))

        # Step 3: Re-close the period
        logger.info("")
        logger.info("Step 3: Re-closing test period...")
        FiscalPeriodService.soft_close_period(
            db=db,
            organization_id=ORG_ID,
            fiscal_period_id=period_id,
            closed_by_user_id=SYSTEM_USER_ID,
        )

        db.commit()

        logger.info("")
        logger.info("RESULTS:")
        logger.info("  Posted: %d", posted)
        logger.info("  Errors: %d", len(errors))
        logger.info("  Period re-closed: YES")
        if errors:
            for err in errors[:10]:
                logger.info("    %s", err)


if __name__ == "__main__":
    main()
