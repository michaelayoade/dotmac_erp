#!/usr/bin/env python3
"""
Dry-run reconciliation for January 2026 bank statements.

Runs AutoReconciliationService.auto_match_statement() on all Jan 2026
statements inside a transaction that is ROLLED BACK.  Reports what
would be matched without making any changes.

Usage:
    python scripts/reconcile_jan_2026_dryrun.py          # dry-run (default)
    python scripts/reconcile_jan_2026_dryrun.py --execute # actually commit
"""

from __future__ import annotations

import argparse
import logging
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
)
from app.services.finance.banking.auto_reconciliation import (
    AutoReconciliationService,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reconcile_jan_2026")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
JAN_START = date(2026, 1, 1)
JAN_END = date(2026, 1, 31)


def find_jan_2026_statements(db: Session) -> list[tuple[BankStatement, str]]:
    """Return all statements that overlap January 2026, with bank label."""
    stmt = (
        select(BankStatement, BankAccount)
        .join(BankAccount, BankAccount.bank_account_id == BankStatement.bank_account_id)
        .where(
            BankStatement.organization_id == ORG_ID,
            BankStatement.period_start <= JAN_END,
            BankStatement.period_end >= JAN_START,
        )
        .order_by(BankAccount.bank_name, BankStatement.period_start)
    )
    rows = db.execute(stmt).all()
    results: list[tuple[BankStatement, str]] = []
    for bs, ba in rows:
        label = f"{ba.bank_name} - {ba.account_name} ({ba.account_number})"
        results.append((bs, label))
    return results


def count_unmatched(db: Session, statement_id: UUID) -> int:
    """Count unmatched lines in a statement."""
    return (
        db.scalar(
            select(func.count(BankStatementLine.line_id)).where(
                BankStatementLine.statement_id == statement_id,
                BankStatementLine.is_matched.is_(False),
            )
        )
        or 0
    )


def count_jan_unmatched(db: Session, statement_id: UUID) -> int:
    """Count unmatched lines in January only (for multi-month statements)."""
    return (
        db.scalar(
            select(func.count(BankStatementLine.line_id)).where(
                BankStatementLine.statement_id == statement_id,
                BankStatementLine.is_matched.is_(False),
                BankStatementLine.transaction_date >= JAN_START,
                BankStatementLine.transaction_date <= JAN_END,
            )
        )
        or 0
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile Jan 2026 statements")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually commit matches (default: dry-run with rollback)",
    )
    args = parser.parse_args()
    dry_run = not args.execute

    mode_label = "DRY-RUN" if dry_run else "EXECUTE"
    logger.info("=== Jan 2026 Reconciliation [%s] ===", mode_label)

    with SessionLocal() as db:
        statements = find_jan_2026_statements(db)
        logger.info("Found %d statements overlapping Jan 2026", len(statements))

        if not statements:
            logger.warning("No statements found — nothing to do")
            return

        service = AutoReconciliationService()
        grand_matched = 0
        grand_skipped = 0
        grand_errors: list[str] = []

        for bs, label in statements:
            before_unmatched = count_unmatched(db, bs.statement_id)
            jan_unmatched = count_jan_unmatched(db, bs.statement_id)

            logger.info(
                "\n── %s ──\n   Statement: %s | Period: %s → %s\n"
                "   Total unmatched: %d | Jan-only unmatched: %d",
                label,
                bs.statement_number,
                bs.period_start,
                bs.period_end,
                before_unmatched,
                jan_unmatched,
            )

            if before_unmatched == 0:
                logger.info("   → Already fully matched, skipping")
                continue

            result = service.auto_match_statement(
                db,
                ORG_ID,
                bs.statement_id,
                include_contra_suggestions=True,
            )

            after_unmatched = count_unmatched(db, bs.statement_id)
            newly_matched = before_unmatched - after_unmatched

            logger.info(
                "   → Matched: %d | Skipped: %d | Errors: %d | Remaining unmatched: %d",
                result.matched,
                result.skipped,
                len(result.errors),
                after_unmatched,
            )

            if result.contra_suggestions:
                logger.info(
                    "   → Contra transfer suggestions: %d",
                    len(result.contra_suggestions),
                )
                for cs in result.contra_suggestions[:5]:
                    logger.info(
                        "     • Score %s: %s → %s (diff: %s, %s days)",
                        cs.get("score"),
                        cs.get("source_description", "")[:50],
                        cs.get("destination_description", "")[:50],
                        cs.get("amount_diff"),
                        cs.get("date_diff_days"),
                    )
                if len(result.contra_suggestions) > 5:
                    logger.info(
                        "     … and %d more",
                        len(result.contra_suggestions) - 5,
                    )

            for err in result.errors:
                logger.warning("   ⚠ %s", err)

            grand_matched += result.matched
            grand_skipped += result.skipped
            grand_errors.extend(result.errors)

            # Flush to materialise counts before next statement
            db.flush()

        logger.info("\n" + "=" * 60)
        logger.info(
            "TOTALS: Matched=%d | Skipped=%d | Errors=%d",
            grand_matched,
            grand_skipped,
            len(grand_errors),
        )

        if dry_run:
            logger.info("DRY-RUN — rolling back all changes")
            db.rollback()
        else:
            logger.info("EXECUTE — committing all changes")
            db.commit()

    logger.info("Done.")


if __name__ == "__main__":
    main()
