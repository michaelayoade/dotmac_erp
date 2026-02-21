"""
Fix Trade Receivables 1400 — Reclassify misposted revenue.

Root cause: ERPNext sync fallback in sales_invoice.py line 188-189 used
ar_control_account_id (1400 Trade Receivables) as the revenue_account_id
when income_account resolution failed. This caused invoice revenue credits
to post to 1400 instead of 4000 (Internet Revenue).

Impact: 28,589 invoice lines with revenue_account_id = 1400.
Total misposted line_amount: ~NGN 2.44B.

Fix:
  Step 1: Update ar.invoice_line.revenue_account_id from 1400 → 4000
  Step 2: Post reclassification journal DR 1400 / CR 4000

Usage:
    python scripts/fix_trade_receivables_revenue.py --dry-run
    python scripts/fix_trade_receivables_revenue.py --execute
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
from app.models.finance.gl.journal_entry import JournalType  # noqa: E402
from app.services.finance.gl.gl_posting_adapter import GLPostingAdapter  # noqa: E402
from app.services.finance.gl.journal import JournalInput, JournalLineInput  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fix_tr_revenue")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
CREATOR_USER_ID = UUID("ef36328f-2343-4649-afa0-ab1bfd4ec6f0")
APPROVER_USER_ID = UUID("2364e957-d6b0-4702-9b35-2e314cd1d22c")


def get_account(db: object, code: str) -> dict[str, str] | None:
    """Look up account_id and name by code."""
    row = db.execute(
        text("""
            SELECT account_id, account_name FROM gl.account
            WHERE account_code = :code
              AND organization_id = :org_id
              AND is_active = true
        """),
        {"code": code, "org_id": str(ORG_ID)},
    ).one_or_none()
    if row:
        return {"id": str(row[0]), "name": row[1]}
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix Trade Receivables 1400 misposted revenue."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    execute = args.execute

    with SessionLocal() as db:
        logger.info("=" * 60)
        logger.info("FIX TRADE RECEIVABLES 1400 — REVENUE RECLASSIFICATION")
        logger.info("=" * 60)

        # ── Resolve accounts ──
        acct_1400 = get_account(db, "1400")
        acct_4000 = get_account(db, "4000")

        if not acct_1400:
            logger.error("Account 1400 not found!")
            return
        if not acct_4000:
            logger.error("Account 4000 not found!")
            return

        logger.info("  1400: %s (%s)", acct_1400["name"], acct_1400["id"])
        logger.info("  4000: %s (%s)", acct_4000["name"], acct_4000["id"])

        # ── Step 1: Count affected invoice lines ──
        logger.info("")
        logger.info("Step 1: Identify misposted invoice lines")

        stats = db.execute(
            text("""
                SELECT COUNT(*) as line_count,
                       COALESCE(SUM(il.line_amount), 0) as total_line_amount,
                       COALESCE(SUM(il.tax_amount), 0) as total_tax_amount
                FROM ar.invoice_line il
                WHERE il.revenue_account_id = :acct_1400_id
            """),
            {"acct_1400_id": acct_1400["id"]},
        ).one()

        line_count = int(stats[0])
        total_line_amount = Decimal(str(stats[1]))
        total_tax_amount = Decimal(str(stats[2]))

        logger.info("  Affected lines:     %d", line_count)
        logger.info("  Total line_amount:  NGN %s", f"{total_line_amount:,.2f}")
        logger.info("  Total tax_amount:   NGN %s", f"{total_tax_amount:,.2f}")

        if line_count == 0:
            logger.info("  No misposted lines found — nothing to fix.")
            return

        # ── Verify these are invoice lines (not from other sources) ──
        inv_breakdown = db.execute(
            text("""
                SELECT inv.status, COUNT(il.*) as cnt, SUM(il.line_amount) as amt
                FROM ar.invoice_line il
                JOIN ar.invoice inv ON inv.invoice_id = il.invoice_id
                WHERE il.revenue_account_id = :acct_1400_id
                GROUP BY inv.status
                ORDER BY cnt DESC
            """),
            {"acct_1400_id": acct_1400["id"]},
        ).all()

        logger.info("")
        logger.info("  Breakdown by invoice status:")
        for row in inv_breakdown:
            logger.info(
                "    %-20s  %6d lines  NGN %s",
                row[0],
                row[1],
                f"{Decimal(str(row[2])):,.2f}",
            )

        # ── Only reclassify posted invoices (they have GL entries) ──
        posted_stats = db.execute(
            text("""
                SELECT COUNT(*) as line_count,
                       COALESCE(SUM(il.line_amount), 0) as total_amount
                FROM ar.invoice_line il
                JOIN ar.invoice inv ON inv.invoice_id = il.invoice_id
                WHERE il.revenue_account_id = :acct_1400_id
                  AND inv.status NOT IN ('DRAFT', 'VOID')
            """),
            {"acct_1400_id": acct_1400["id"]},
        ).one()

        posted_line_count = int(posted_stats[0])
        posted_amount = Decimal(str(posted_stats[1]))

        logger.info("")
        logger.info("  Posted invoice lines: %d", posted_line_count)
        logger.info(
            "  Posted amount (for GL reclassification): NGN %s", f"{posted_amount:,.2f}"
        )

        # ── Step 2: Update invoice lines ──
        logger.info("")
        logger.info("Step 2: Update invoice line revenue_account_id (1400 → 4000)")

        if execute:
            updated = db.execute(
                text("""
                    UPDATE ar.invoice_line
                    SET revenue_account_id = :new_id
                    WHERE revenue_account_id = :old_id
                """),
                {"new_id": acct_4000["id"], "old_id": acct_1400["id"]},
            )
            logger.info("  Updated %d invoice lines", updated.rowcount)
        else:
            logger.info("  Would update %d invoice lines", line_count)

        # ── Step 3: GL reclassification journal ──
        logger.info("")
        logger.info("Step 3: GL reclassification journal")

        # Use the posted amount for the reclassification
        reclass_amount = posted_amount

        logger.info("  Reclassification amount: NGN %s", f"{reclass_amount:,.2f}")
        logger.info("  DR 1400 (remove misposted revenue credits)")
        logger.info("  CR 4000 (recognize correct revenue)")

        # Current balance check
        cur_balance = db.execute(
            text("""
                SELECT COALESCE(SUM(jel.debit_amount), 0) - COALESCE(SUM(jel.credit_amount), 0)
                FROM gl.journal_entry_line jel
                JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
                    AND je.status = 'POSTED'
                JOIN gl.account a ON a.account_id = jel.account_id
                WHERE a.account_code = '1400'
                  AND a.organization_id = :org_id
            """),
            {"org_id": str(ORG_ID)},
        ).scalar()
        cur_balance = Decimal(str(cur_balance))

        new_balance = cur_balance + reclass_amount
        logger.info("  Current 1400 balance:   NGN %s", f"{cur_balance:,.2f}")
        logger.info("  Post-fix 1400 balance:  NGN %s", f"{new_balance:,.2f}")

        if not execute:
            logger.info("")
            logger.info("DRY RUN — no changes made.")
            return

        journal_input = JournalInput(
            journal_type=JournalType.ADJUSTMENT,
            entry_date=date.today(),
            posting_date=date.today(),
            description=(
                "Reclassify revenue misposted to Trade Receivables 1400. "
                "ERPNext sync fallback used ar_control_account_id as revenue_account_id "
                f"for {posted_line_count} invoice lines. Moving to 4000 Internet Revenue."
            ),
            reference="FIX-TR-REVENUE-RECLASS",
            currency_code="NGN",
            lines=[
                JournalLineInput(
                    account_id=UUID(acct_1400["id"]),
                    debit_amount=reclass_amount,
                    credit_amount=Decimal("0"),
                    description="Remove misposted revenue credits from Trade Receivables",
                ),
                JournalLineInput(
                    account_id=UUID(acct_4000["id"]),
                    debit_amount=Decimal("0"),
                    credit_amount=reclass_amount,
                    description="Recognize revenue correctly in Internet Revenue",
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
            logger.info("  Posted %s", result.entry_number)
        else:
            logger.error("  FAILED: %s", result.message)
            return

        # ── Verify ──
        logger.info("")
        logger.info("=" * 60)
        logger.info("VERIFICATION")
        logger.info("=" * 60)

        # Check remaining misposted lines
        remaining = db.scalar(
            text("""
                SELECT COUNT(*) FROM ar.invoice_line
                WHERE revenue_account_id = :acct_1400_id
            """),
            {"acct_1400_id": acct_1400["id"]},
        )
        logger.info("  Remaining lines with 1400 as revenue: %d", remaining)

        # New balance
        final_balance = db.execute(
            text("""
                SELECT COALESCE(SUM(jel.debit_amount), 0) - COALESCE(SUM(jel.credit_amount), 0)
                FROM gl.journal_entry_line jel
                JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
                    AND je.status = 'POSTED'
                JOIN gl.account a ON a.account_id = jel.account_id
                WHERE a.account_code = '1400'
                  AND a.organization_id = :org_id
            """),
            {"org_id": str(ORG_ID)},
        ).scalar()
        logger.info(
            "  Final 1400 balance: NGN %s", f"{Decimal(str(final_balance)):,.2f}"
        )

        db.commit()
        logger.info("")
        logger.info("Changes committed.")


if __name__ == "__main__":
    main()
