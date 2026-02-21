"""
Reclassify expense claim payables from 2110 (WHT) to 2030 (Employee Reimbursables).

Background:
The SQL backfill script (bulk_gl_backfill_expenses.sql) hardcoded account 2110
(Withholding Tax) as the employee payable account. This script creates a
reclassification journal entry to transfer the expense-related balance from
2110 to 2030, correcting the misclassification.

Usage:
    # Dry run — show the reclassification amount without making changes
    python scripts/reclassify_expense_payable.py --dry-run

    # Execute — create and post the reclassification journal entry
    python scripts/reclassify_expense_payable.py --execute

    # Override amount (e.g. to include opening balances)
    python scripts/reclassify_expense_payable.py --execute --amount 12050966.16
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
from app.models.finance.gl.journal_entry import (  # noqa: E402
    JournalType,
)
from app.services.finance.gl.gl_posting_adapter import (  # noqa: E402
    GLPostingAdapter,
)
from app.services.finance.gl.journal import (  # noqa: E402
    JournalInput,
    JournalLineInput,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reclassify_expense_payable")

# Constants
ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
# Two different admin users required for SoD (creator != approver)
CREATOR_USER_ID = UUID("ef36328f-2343-4649-afa0-ab1bfd4ec6f0")  # Michael Ayoade
APPROVER_USER_ID = UUID("2364e957-d6b0-4702-9b35-2e314cd1d22c")  # Shade Ayoade
ACCT_2110_WHT = UUID("5c4bfaa4-dd26-4448-a9c2-21c8bcc6e256")
ACCT_2030_EMPLOYEE_REIMBURSABLES = UUID("e97d4b48-1662-4c78-8734-c696319bef1b")


def analyze_2110_balance(db: object) -> dict[str, Decimal]:
    """Analyze the composition of account 2110's balance."""
    # Total balance
    row = db.execute(
        text("""
            SELECT
                COALESCE(SUM(jel.debit_amount), 0) AS total_debit,
                COALESCE(SUM(jel.credit_amount), 0) AS total_credit
            FROM gl.journal_entry_line jel
            JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
            WHERE jel.account_id = :acct_id
              AND je.status = 'POSTED'
              AND je.organization_id = :org_id
        """),
        {"acct_id": str(ACCT_2110_WHT), "org_id": str(ORG_ID)},
    ).one()
    total_debit = Decimal(str(row[0]))
    total_credit = Decimal(str(row[1]))

    # Expense-related entries
    exp_row = db.execute(
        text("""
            SELECT
                COALESCE(SUM(jel.debit_amount), 0) AS exp_debit,
                COALESCE(SUM(jel.credit_amount), 0) AS exp_credit,
                COUNT(DISTINCT je.journal_entry_id) AS journal_count
            FROM gl.journal_entry_line jel
            JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
            WHERE jel.account_id = :acct_id
              AND je.status = 'POSTED'
              AND je.organization_id = :org_id
              AND (je.description ILIKE '%expense%'
                   OR je.description ILIKE '%reimburse%'
                   OR je.description ILIKE '%claim%')
        """),
        {"acct_id": str(ACCT_2110_WHT), "org_id": str(ORG_ID)},
    ).one()
    exp_debit = Decimal(str(exp_row[0]))
    exp_credit = Decimal(str(exp_row[1]))
    exp_journal_count = int(exp_row[2])

    # Opening balance entries
    ob_row = db.execute(
        text("""
            SELECT
                COALESCE(SUM(jel.debit_amount), 0) AS ob_debit,
                COALESCE(SUM(jel.credit_amount), 0) AS ob_credit,
                COUNT(DISTINCT je.journal_entry_id) AS journal_count
            FROM gl.journal_entry_line jel
            JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
            WHERE jel.account_id = :acct_id
              AND je.status = 'POSTED'
              AND je.organization_id = :org_id
              AND NOT (je.description ILIKE '%expense%'
                       OR je.description ILIKE '%reimburse%'
                       OR je.description ILIKE '%claim%')
        """),
        {"acct_id": str(ACCT_2110_WHT), "org_id": str(ORG_ID)},
    ).one()
    ob_debit = Decimal(str(ob_row[0]))
    ob_credit = Decimal(str(ob_row[1]))
    ob_journal_count = int(ob_row[2])

    return {
        "total_balance": total_credit - total_debit,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "expense_net": exp_credit - exp_debit,
        "expense_debit": exp_debit,
        "expense_credit": exp_credit,
        "expense_journals": Decimal(str(exp_journal_count)),
        "opening_net": ob_credit - ob_debit,
        "opening_debit": ob_debit,
        "opening_credit": ob_credit,
        "opening_journals": Decimal(str(ob_journal_count)),
    }


def check_existing_reclassification(db: object) -> bool:
    """Check if a reclassification JE already exists (idempotency)."""
    result = db.execute(
        text("""
            SELECT COUNT(*) FROM gl.journal_entry
            WHERE organization_id = :org_id
              AND reference = 'RECLASS-2110-TO-2030'
              AND status = 'POSTED'
        """),
        {"org_id": str(ORG_ID)},
    ).scalar()
    return (result or 0) > 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reclassify expense payables from 2110 (WHT) to 2030."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only — no changes made",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Create and post the reclassification journal entry",
    )
    parser.add_argument(
        "--amount",
        type=Decimal,
        default=None,
        help="Override reclassification amount (default: expense-related net)",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    with SessionLocal() as db:
        # Step 1: Analyze current state
        logger.info("=" * 60)
        logger.info("ACCOUNT 2110 (WHT) BALANCE ANALYSIS")
        logger.info("=" * 60)

        data = analyze_2110_balance(db)

        logger.info(
            "  Total balance (credit):     NGN %s", f"{data['total_balance']:,.2f}"
        )
        logger.info(
            "    Debits:  NGN %s  |  Credits: NGN %s",
            f"{data['total_debit']:,.2f}",
            f"{data['total_credit']:,.2f}",
        )
        logger.info("")
        logger.info(
            "  Expense-related (net):      NGN %s  (%s journals)",
            f"{data['expense_net']:,.2f}",
            f"{data['expense_journals']:,.0f}",
        )
        logger.info(
            "  Opening balance imports:    NGN %s  (%s journals)",
            f"{data['opening_net']:,.2f}",
            f"{data['opening_journals']:,.0f}",
        )
        logger.info("")

        # Determine reclassification amount
        if args.amount is not None:
            reclass_amount = args.amount
            logger.info(
                "  Reclassification amount (manual override): NGN %s",
                f"{reclass_amount:,.2f}",
            )
        else:
            reclass_amount = data["expense_net"]
            logger.info(
                "  Reclassification amount (expense-related): NGN %s",
                f"{reclass_amount:,.2f}",
            )
            if data["opening_net"] > 0:
                logger.info("")
                logger.info(
                    "  NOTE: Opening balance imports (NGN %s) may also contain",
                    f"{data['opening_net']:,.2f}",
                )
                logger.info(
                    "  misclassified expense payables from ERPNext. Use --amount"
                )
                logger.info(
                    "  to include them: --amount %s",
                    f"{data['total_balance']:.2f}",
                )

        logger.info("=" * 60)

        if reclass_amount <= 0:
            logger.info("Nothing to reclassify (amount <= 0). Exiting.")
            return

        if args.dry_run:
            logger.info("DRY RUN — no changes made.")
            logger.info("")
            logger.info("Proposed reclassification journal entry:")
            logger.info("  Date:        %s", date.today())
            logger.info("  Type:        ADJUSTMENT")
            logger.info("  Reference:   RECLASS-2110-TO-2030")
            logger.info(
                "  DR 2110 WHT Payable:              NGN %s",
                f"{reclass_amount:,.2f}",
            )
            logger.info(
                "  CR 2030 Employee Reimbursables:    NGN %s",
                f"{reclass_amount:,.2f}",
            )
            return

        # Step 2: Check idempotency
        if check_existing_reclassification(db):
            logger.info(
                "Reclassification JE (RECLASS-2110-TO-2030) already exists. Skipping."
            )
            return

        # Step 3: Create and post the reclassification JE
        logger.info("")
        logger.info("Creating reclassification journal entry...")

        journal_input = JournalInput(
            journal_type=JournalType.ADJUSTMENT,
            entry_date=date.today(),
            posting_date=date.today(),
            description=(
                "Reclassify expense claim payables from 2110 (WHT) to "
                "2030 (Employee Reimbursables) — corrects misposting by "
                "bulk_gl_backfill_expenses.sql"
            ),
            reference="RECLASS-2110-TO-2030",
            currency_code="NGN",
            lines=[
                # Debit 2110 to reduce the WHT payable balance
                JournalLineInput(
                    account_id=ACCT_2110_WHT,
                    debit_amount=reclass_amount,
                    credit_amount=Decimal("0"),
                    description="Reclassify expense payables from WHT account",
                ),
                # Credit 2030 to increase Employee Reimbursables
                JournalLineInput(
                    account_id=ACCT_2030_EMPLOYEE_REIMBURSABLES,
                    debit_amount=Decimal("0"),
                    credit_amount=reclass_amount,
                    description="Reclassify expense payables to correct account",
                ),
            ],
        )

        result = GLPostingAdapter.create_and_post_journal(
            db=db,
            organization_id=ORG_ID,
            input=journal_input,
            created_by_user_id=CREATOR_USER_ID,
            approved_by_user_id=APPROVER_USER_ID,
            auto_post=True,
        )

        if result.success:
            db.commit()
            logger.info(
                "SUCCESS: Journal %s posted (ID: %s)",
                result.entry_number,
                result.journal_entry_id,
            )
            logger.info(
                "  DR 2110 WHT:                   NGN %s",
                f"{reclass_amount:,.2f}",
            )
            logger.info(
                "  CR 2030 Employee Reimbursables: NGN %s",
                f"{reclass_amount:,.2f}",
            )
        else:
            logger.error("FAILED to create reclassification JE: %s", result.message)
            db.rollback()
            sys.exit(1)


if __name__ == "__main__":
    main()
