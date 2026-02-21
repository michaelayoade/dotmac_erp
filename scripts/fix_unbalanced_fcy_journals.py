"""
Fix 46 unbalanced POSTED NGN journal entries from ERPNext FCY migration.

Background:
These journals have one line in USD (Zenith USD bank account) and one in NGN,
but the journal currency_code is 'NGN'. The debit_amount/credit_amount columns
hold mixed currencies (one side USD, one side NGN), causing an apparent
imbalance. The functional amounts (debit_amount_functional/credit_amount_functional)
are correctly balanced in NGN.

Fix:
For each unbalanced journal, copy the functional amounts into the transaction
amounts (debit_amount = debit_amount_functional, credit_amount =
credit_amount_functional). This normalizes all amounts to NGN and resolves the
trial balance imbalance of ~NGN 12.2M.

Usage:
    python scripts/fix_unbalanced_fcy_journals.py --dry-run
    python scripts/fix_unbalanced_fcy_journals.py --execute
"""

from __future__ import annotations

import argparse
import logging
import sys
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
logger = logging.getLogger("fix_fcy_journals")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def get_unbalanced_journals(db: object) -> list[dict]:
    """Get all unbalanced POSTED NGN journals."""
    rows = db.execute(
        text("""
            SELECT je.journal_entry_id, je.journal_number,
                   SUM(jel.debit_amount) AS total_debit,
                   SUM(jel.credit_amount) AS total_credit,
                   SUM(jel.debit_amount) - SUM(jel.credit_amount) AS imbalance,
                   SUM(jel.debit_amount_functional) AS total_debit_func,
                   SUM(jel.credit_amount_functional) AS total_credit_func,
                   SUM(jel.debit_amount_functional) - SUM(jel.credit_amount_functional) AS func_imbalance
            FROM gl.journal_entry je
            JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
            WHERE je.organization_id = :org_id
              AND je.status = 'POSTED'
              AND je.currency_code = 'NGN'
            GROUP BY je.journal_entry_id, je.journal_number
            HAVING ABS(SUM(jel.debit_amount) - SUM(jel.credit_amount)) > 0.01
            ORDER BY ABS(SUM(jel.debit_amount) - SUM(jel.credit_amount)) DESC
        """),
        {"org_id": str(ORG_ID)},
    ).all()
    return [
        {
            "journal_entry_id": str(r[0]),
            "journal_number": r[1],
            "imbalance": Decimal(str(r[4])),
            "func_imbalance": Decimal(str(r[7])),
        }
        for r in rows
    ]


def get_lines_needing_fix(db: object, journal_entry_id: str) -> list[dict]:
    """Get journal lines where transaction amount differs from functional amount."""
    rows = db.execute(
        text("""
            SELECT jel.line_id,
                   jel.debit_amount, jel.credit_amount,
                   jel.debit_amount_functional, jel.credit_amount_functional,
                   jel.exchange_rate,
                   a.account_code, a.account_name
            FROM gl.journal_entry_line jel
            JOIN gl.account a ON a.account_id = jel.account_id
            WHERE jel.journal_entry_id = :je_id
        """),
        {"je_id": journal_entry_id},
    ).all()
    lines = []
    for r in rows:
        debit = Decimal(str(r[1]))
        credit = Decimal(str(r[2]))
        debit_func = Decimal(str(r[3]))
        credit_func = Decimal(str(r[4]))
        # Only include lines where transaction != functional
        if debit != debit_func or credit != credit_func:
            lines.append(
                {
                    "line_id": str(r[0]),
                    "debit": debit,
                    "credit": credit,
                    "debit_func": debit_func,
                    "credit_func": credit_func,
                    "exchange_rate": r[5],
                    "account_code": r[6],
                    "account_name": r[7],
                }
            )
    return lines


def fix_line(
    db: object, line_id: str, debit_func: Decimal, credit_func: Decimal
) -> None:
    """Set transaction amounts to match functional amounts."""
    db.execute(
        text("""
            UPDATE gl.journal_entry_line
            SET debit_amount = :debit_func,
                credit_amount = :credit_func
            WHERE line_id = :line_id
        """),
        {
            "debit_func": str(debit_func),
            "credit_func": str(credit_func),
            "line_id": line_id,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix unbalanced FCY journal entries.")
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Apply fixes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    with SessionLocal() as db:
        journals = get_unbalanced_journals(db)

        logger.info("=" * 60)
        logger.info("UNBALANCED FCY JOURNAL FIX")
        logger.info("=" * 60)
        logger.info("  Found %d unbalanced POSTED NGN journals", len(journals))

        if not journals:
            logger.info("  Nothing to fix.")
            return

        # Verify all are balanced in functional currency
        func_unbalanced = [
            j for j in journals if abs(j["func_imbalance"]) > Decimal("0.01")
        ]
        if func_unbalanced:
            logger.error(
                "  %d journals are also unbalanced in functional currency! Aborting.",
                len(func_unbalanced),
            )
            for j in func_unbalanced:
                logger.error(
                    "    %s: func imbalance = %s",
                    j["journal_number"],
                    j["func_imbalance"],
                )
            sys.exit(1)

        total_imbalance = sum(j["imbalance"] for j in journals)
        logger.info("  Total nominal imbalance: NGN %s", f"{total_imbalance:,.2f}")
        logger.info("  All %d journals balanced in functional currency.", len(journals))
        logger.info("")

        fixed_count = 0
        lines_fixed = 0

        for j in journals:
            lines = get_lines_needing_fix(db, j["journal_entry_id"])
            if not lines:
                logger.warning(
                    "  %s: no lines need fixing (already balanced?)",
                    j["journal_number"],
                )
                continue

            if args.dry_run:
                logger.info(
                    "  %s (imbalance: %s):",
                    j["journal_number"],
                    f"{j['imbalance']:,.2f}",
                )
                for ln in lines:
                    logger.info(
                        "    %s %s: %s/%s → %s/%s",
                        ln["account_code"],
                        ln["account_name"][:30],
                        f"{ln['debit']:,.2f}",
                        f"{ln['credit']:,.2f}",
                        f"{ln['debit_func']:,.2f}",
                        f"{ln['credit_func']:,.2f}",
                    )
            else:
                for ln in lines:
                    fix_line(db, ln["line_id"], ln["debit_func"], ln["credit_func"])
                    lines_fixed += 1

            fixed_count += 1

        logger.info("")

        if args.dry_run:
            logger.info("DRY RUN — no changes made.")
            logger.info(
                "  %d journals, %d+ lines would be fixed.",
                fixed_count,
                sum(
                    len(get_lines_needing_fix(db, j["journal_entry_id"]))
                    for j in journals[:3]
                ),
            )
        else:
            db.commit()
            logger.info(
                "SUCCESS: Fixed %d journals, %d lines.", fixed_count, lines_fixed
            )

            # Verify
            remaining = get_unbalanced_journals(db)
            if remaining:
                logger.warning("  %d journals still unbalanced!", len(remaining))
            else:
                logger.info("  Verification: 0 unbalanced journals remain.")


if __name__ == "__main__":
    main()
