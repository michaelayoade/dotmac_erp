"""
Reopen all SOFT_CLOSED fiscal periods to OPEN status.

Usage:
    python scripts/reopen_all_periods.py --dry-run
    python scripts/reopen_all_periods.py --execute
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
logger = logging.getLogger("reopen_periods")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_USER_ID = UUID("ef36328f-2343-4649-afa0-ab1bfd4ec6f0")  # Michael Ayoade


def main() -> None:
    parser = argparse.ArgumentParser(description="Reopen all SOFT_CLOSED periods.")
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Do it")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    with SessionLocal() as db:
        rows = db.execute(
            text("""
                SELECT fp.fiscal_period_id, fp.period_name, fp.start_date, fp.end_date,
                       fy.year_name
                FROM gl.fiscal_period fp
                JOIN gl.fiscal_year fy ON fy.fiscal_year_id = fp.fiscal_year_id
                WHERE fp.organization_id = :org_id
                  AND fp.status = 'SOFT_CLOSED'
                ORDER BY fp.start_date
            """),
            {"org_id": str(ORG_ID)},
        ).all()

        logger.info("=" * 60)
        logger.info("REOPEN ALL SOFT_CLOSED FISCAL PERIODS")
        logger.info("=" * 60)
        logger.info("  Found %d SOFT_CLOSED periods", len(rows))

        if not rows:
            logger.info("  Nothing to reopen.")
            return

        # Show summary by year
        year_counts: dict[str, int] = {}
        for r in rows:
            yr = r[4]
            year_counts[yr] = year_counts.get(yr, 0) + 1

        for yr in sorted(year_counts):
            logger.info("    %s: %d periods", yr, year_counts[yr])

        if args.dry_run:
            logger.info("")
            logger.info("DRY RUN — no changes made.")
            return

        logger.info("")
        logger.info("Reopening periods...")

        from app.services.finance.gl.fiscal_period import FiscalPeriodService

        reopened = 0
        errors = 0
        reopen_session_id = uuid4()

        for r in rows:
            try:
                FiscalPeriodService.reopen_period(
                    db=db,
                    organization_id=ORG_ID,
                    fiscal_period_id=r[0],
                    reopened_by_user_id=SYSTEM_USER_ID,
                    reopen_session_id=reopen_session_id,
                )
                reopened += 1
            except Exception as e:
                logger.warning("  FAILED %s (%s): %s", r[1], r[4], e)
                errors += 1

        db.commit()

        logger.info("")
        logger.info("RESULTS:")
        logger.info("  Reopened: %d", reopened)
        logger.info("  Errors:   %d", errors)
        logger.info("  Session:  %s", reopen_session_id)


if __name__ == "__main__":
    main()
