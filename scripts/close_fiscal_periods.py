"""
Close historical fiscal periods (2015-2025).

Background:
144 fiscal periods remain OPEN dating back to 2015. Closing them prevents
accidental posting to old periods. This script performs pre-close validation
per period, then soft-closes them oldest-first.

Pre-close checks per period:
1. No DRAFT or SUBMITTED journals (must be posted or voided first)
2. No APPROVED journals (must be posted first — run post_approved_journals.py)
3. Trial balance check (debits == credits for the period)

Usage:
    # Analyze all periods — shows which are ready to close
    python scripts/close_fiscal_periods.py --dry-run

    # Close all periods up to end of 2025
    python scripts/close_fiscal_periods.py --execute --through 2025-12-31

    # Close only periods for a specific year
    python scripts/close_fiscal_periods.py --execute --year 2022

    # Hard-close (permanent) instead of soft-close
    python scripts/close_fiscal_periods.py --execute --through 2025-12-31 --hard
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text

sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("close_fiscal_periods")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")


def get_open_periods(
    db: object, through_date: date | None = None, year: int | None = None
) -> list[dict]:
    """Get OPEN fiscal periods, optionally filtered by date or year."""
    query = """
        SELECT fp.fiscal_period_id, fp.period_name, fp.period_number,
               fp.start_date, fp.end_date, fp.status,
               fy.year_name, fy.fiscal_year_id
        FROM gl.fiscal_period fp
        JOIN gl.fiscal_year fy ON fy.fiscal_year_id = fp.fiscal_year_id
        WHERE fp.organization_id = :org_id
          AND fp.status IN ('OPEN', 'REOPENED')
    """
    params: dict[str, str] = {"org_id": str(ORG_ID)}

    if through_date:
        query += " AND fp.end_date <= :through_date"
        params["through_date"] = through_date.isoformat()
    if year:
        query += " AND EXTRACT(YEAR FROM fp.start_date) = :year"
        params["year"] = str(year)

    query += " ORDER BY fp.start_date"

    rows = db.execute(text(query), params).all()
    return [
        {
            "fiscal_period_id": str(r[0]),
            "period_name": r[1],
            "period_number": r[2],
            "start_date": r[3],
            "end_date": r[4],
            "status": r[5],
            "year_name": r[6],
            "fiscal_year_id": str(r[7]),
        }
        for r in rows
    ]


def check_period_journals(db: object, period_id: str) -> dict:
    """Check for unposted journals in a period."""
    row = db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'DRAFT') AS draft_count,
                COUNT(*) FILTER (WHERE status = 'SUBMITTED') AS submitted_count,
                COUNT(*) FILTER (WHERE status = 'APPROVED') AS approved_count,
                COUNT(*) FILTER (WHERE status = 'POSTED') AS posted_count
            FROM gl.journal_entry
            WHERE organization_id = :org_id
              AND fiscal_period_id = :period_id
        """),
        {"org_id": str(ORG_ID), "period_id": period_id},
    ).one()
    return {
        "draft": int(row[0]),
        "submitted": int(row[1]),
        "approved": int(row[2]),
        "posted": int(row[3]),
    }


def check_period_balance(db: object, period_id: str) -> dict:
    """Check trial balance for a period (POSTED journals only)."""
    row = db.execute(
        text("""
            SELECT
                COALESCE(SUM(jel.debit_amount), 0) AS total_debit,
                COALESCE(SUM(jel.credit_amount), 0) AS total_credit
            FROM gl.journal_entry je
            JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
            WHERE je.organization_id = :org_id
              AND je.fiscal_period_id = :period_id
              AND je.status = 'POSTED'
        """),
        {"org_id": str(ORG_ID), "period_id": period_id},
    ).one()
    total_debit = Decimal(str(row[0]))
    total_credit = Decimal(str(row[1]))
    return {
        "total_debit": total_debit,
        "total_credit": total_credit,
        "imbalance": abs(total_debit - total_credit),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Close historical fiscal periods.")
    parser.add_argument("--dry-run", action="store_true", help="Analyze only")
    parser.add_argument("--execute", action="store_true", help="Close periods")
    parser.add_argument(
        "--through",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Close periods ending on or before this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Close periods for this specific year only",
    )
    parser.add_argument(
        "--hard",
        action="store_true",
        help="Hard-close (permanent) instead of soft-close",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Close even if period has unposted journals (not recommended)",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    with SessionLocal() as db:
        periods = get_open_periods(db, through_date=args.through, year=args.year)

        logger.info("=" * 70)
        logger.info("FISCAL PERIOD CLOSE ANALYSIS")
        logger.info("=" * 70)
        logger.info("  OPEN periods found: %d", len(periods))
        if args.through:
            logger.info("  Filter: through %s", args.through)
        if args.year:
            logger.info("  Filter: year %d", args.year)
        logger.info(
            "  Close type: %s", "HARD (permanent)" if args.hard else "SOFT (reversible)"
        )
        logger.info("")

        if not periods:
            logger.info("  No periods to close.")
            return

        # Analyze each period
        ready: list[dict] = []
        blocked: list[dict] = []

        for p in periods:
            journals = check_period_journals(db, p["fiscal_period_id"])
            balance = check_period_balance(db, p["fiscal_period_id"])

            p["journals"] = journals
            p["balance"] = balance

            unposted = journals["draft"] + journals["submitted"] + journals["approved"]
            has_imbalance = balance["imbalance"] > Decimal("0.01")

            if (unposted > 0 or has_imbalance) and not args.force:
                blocked.append(p)
            else:
                ready.append(p)

        # Group by year for display
        by_year: dict[str, list[dict]] = {}
        for p in periods:
            yr = p["year_name"]
            if yr not in by_year:
                by_year[yr] = []
            by_year[yr].append(p)

        for yr in sorted(by_year.keys()):
            yr_periods = by_year[yr]
            yr_ready = sum(1 for p in yr_periods if p in ready)
            yr_blocked = sum(1 for p in yr_periods if p in blocked)
            logger.info(
                "  %s: %d periods (%d ready, %d blocked)",
                yr,
                len(yr_periods),
                yr_ready,
                yr_blocked,
            )

        logger.info("")
        logger.info("  Total ready to close:  %d", len(ready))
        logger.info("  Total blocked:         %d", len(blocked))

        if blocked:
            logger.info("")
            logger.info("  BLOCKED periods:")
            for p in blocked[:20]:
                j = p["journals"]
                b = p["balance"]
                reasons = []
                if j["draft"] > 0:
                    reasons.append(f"{j['draft']} DRAFT")
                if j["submitted"] > 0:
                    reasons.append(f"{j['submitted']} SUBMITTED")
                if j["approved"] > 0:
                    reasons.append(f"{j['approved']} APPROVED")
                if b["imbalance"] > Decimal("0.01"):
                    reasons.append(f"imbalance {b['imbalance']:,.2f}")
                logger.info(
                    "    %-30s %s → %s  [%s]",
                    p["period_name"],
                    p["start_date"],
                    p["end_date"],
                    ", ".join(reasons),
                )
            if len(blocked) > 20:
                logger.info("    ... and %d more", len(blocked) - 20)

        logger.info("=" * 70)

        if args.dry_run:
            logger.info("DRY RUN — no changes made.")
            if ready:
                logger.info("")
                logger.info("  Periods ready to close:")
                for p in ready:
                    j = p["journals"]
                    logger.info(
                        "    %-30s %s → %s  (%d posted JEs)",
                        p["period_name"],
                        p["start_date"],
                        p["end_date"],
                        j["posted"],
                    )
            return

        if not ready:
            logger.info("No periods ready to close. Fix blocked periods first.")
            return

        # Import service inside execute block
        from app.services.finance.gl.fiscal_period import FiscalPeriodService

        closed = 0
        errors: list[str] = []

        for p in ready:
            try:
                if args.hard:
                    # Must soft-close first if currently OPEN
                    if p["status"] == "OPEN":
                        FiscalPeriodService.soft_close_period(
                            db=db,
                            organization_id=ORG_ID,
                            fiscal_period_id=UUID(p["fiscal_period_id"]),
                            closed_by_user_id=SYSTEM_USER_ID,
                        )
                    FiscalPeriodService.hard_close_period(
                        db=db,
                        organization_id=ORG_ID,
                        fiscal_period_id=UUID(p["fiscal_period_id"]),
                        closed_by_user_id=SYSTEM_USER_ID,
                    )
                else:
                    FiscalPeriodService.soft_close_period(
                        db=db,
                        organization_id=ORG_ID,
                        fiscal_period_id=UUID(p["fiscal_period_id"]),
                        closed_by_user_id=SYSTEM_USER_ID,
                    )
                closed += 1
                if closed % 20 == 0:
                    logger.info("  Closed %d / %d ...", closed, len(ready))
                    db.flush()
            except Exception as e:
                err_msg = f"{p['period_name']}: {e}"
                errors.append(err_msg)
                logger.warning("  FAILED: %s", err_msg)

        db.commit()

        logger.info("")
        logger.info("RESULTS:")
        logger.info("  Closed:   %d", closed)
        logger.info("  Blocked:  %d (fix first)", len(blocked))
        logger.info("  Errors:   %d", len(errors))
        if errors:
            for err in errors[:10]:
                logger.info("    %s", err)


if __name__ == "__main__":
    main()
