"""
Fix at Source — Revenue Reclass: Repoint GL lines from 1400→4000 on invoice journals.

Root cause: ERPNext sync fallback used ar_control_account_id (1400 Trade Receivables)
as the revenue_account_id when income_account resolution failed. This caused invoice
revenue CREDIT lines to post to 1400 instead of 4000 (Internet Revenue).

This script identifies the misposted CREDIT lines on 1400 from INVOICE source journals,
repoints them to 4000 on both journal_entry_line and posted_ledger_line, then removes
the FIX-TR-REVENUE-RECLASS correction journal.

Tables updated:
  1. gl.journal_entry_line  — account_id (CREDIT lines on 1400 from INVOICE journals)
  2. gl.posted_ledger_line  — account_id + account_code
  3. ar.invoice_line        — revenue_account_id (already done by previous script, verified)
  4. gl.journal_entry       — FIX-TR-REVENUE-RECLASS marked VOID
  5. gl.account_balance     — rebuilt for affected periods

Usage:
    python scripts/fix_source_revenue_reclass.py --dry-run
    python scripts/fix_source_revenue_reclass.py --execute
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
logger = logging.getLogger("fix_source_revenue_reclass")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def get_account_id(db: object, code: str) -> str | None:
    """Look up account_id by code."""
    row = db.execute(
        text("""
            SELECT account_id FROM gl.account
            WHERE account_code = :code
              AND organization_id = :org_id
              AND is_active = true
        """),
        {"code": code, "org_id": str(ORG_ID)},
    ).one_or_none()
    return str(row[0]) if row else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix at source: repoint revenue lines from 1400 to 4000."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    execute = args.execute

    with SessionLocal() as db:
        logger.info("=" * 70)
        logger.info("FIX AT SOURCE — REVENUE RECLASS: Repoint 1400 credits → 4000")
        logger.info("=" * 70)

        # ── Resolve accounts ──
        acct_1400_id = get_account_id(db, "1400")
        acct_4000_id = get_account_id(db, "4000")

        if not acct_1400_id:
            logger.error("Account 1400 not found!")
            return
        if not acct_4000_id:
            logger.error("Account 4000 not found!")
            return

        logger.info("  1400 Trade Receivables: %s", acct_1400_id)
        logger.info("  4000 Internet Revenue:  %s", acct_4000_id)

        # ── Step 1: Find the FIX-TR-REVENUE-RECLASS journal ──
        logger.info("")
        logger.info("Step 1: Locate FIX-TR-REVENUE-RECLASS correction journal")

        fix_journal = db.execute(
            text("""
                SELECT journal_entry_id, journal_number, fiscal_period_id,
                       total_debit, status
                FROM gl.journal_entry
                WHERE reference = 'FIX-TR-REVENUE-RECLASS'
                  AND organization_id = :org_id
            """),
            {"org_id": str(ORG_ID)},
        ).one_or_none()

        if not fix_journal:
            logger.warning(
                "  FIX-TR-REVENUE-RECLASS journal not found — may already be voided."
            )
        else:
            logger.info(
                "  Found: %s (status=%s, amount=%s)",
                fix_journal.journal_number,
                fix_journal.status,
                f"{fix_journal.total_debit:,.2f}",
            )

        # ── Step 2: Identify misposted CREDIT lines ──
        # These are CREDIT lines on account 1400 from INVOICE-sourced journals.
        # Legitimate 1400 credits (from credit notes, reversals) have different source types.
        logger.info("")
        logger.info("Step 2: Identify misposted revenue CREDIT lines on 1400")

        # Count by source_document_type to verify we only target INVOICE lines
        source_breakdown = db.execute(
            text("""
                SELECT je.source_document_type,
                       COUNT(jl.line_id) AS line_count,
                       SUM(jl.credit_amount) AS total_credit
                FROM gl.journal_entry_line jl
                JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                WHERE jl.account_id = :acct_1400_id
                  AND jl.credit_amount > 0
                  AND je.status = 'POSTED'
                  AND je.organization_id = :org_id
                  AND je.reference NOT LIKE 'FIX-%%'
                  AND je.reference NOT LIKE 'MERGE-COA%%'
                GROUP BY je.source_document_type
                ORDER BY total_credit DESC
            """),
            {"acct_1400_id": acct_1400_id, "org_id": str(ORG_ID)},
        ).all()

        logger.info("  Credit lines on 1400 by source type:")
        for row in source_breakdown:
            logger.info(
                "    %-25s  %6d lines  CR %s",
                row.source_document_type or "(null)",
                row.line_count,
                f"{row.total_credit:,.2f}",
            )

        # Only repoint INVOICE-sourced credit lines — these are the misposted revenue
        invoice_credits = db.execute(
            text("""
                SELECT COUNT(jl.line_id) AS line_count,
                       SUM(jl.credit_amount) AS total_credit
                FROM gl.journal_entry_line jl
                JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                WHERE jl.account_id = :acct_1400_id
                  AND jl.credit_amount > 0
                  AND je.source_document_type = 'INVOICE'
                  AND je.status = 'POSTED'
                  AND je.organization_id = :org_id
                  AND je.reference NOT LIKE 'FIX-%%'
            """),
            {"acct_1400_id": acct_1400_id, "org_id": str(ORG_ID)},
        ).one()

        target_lines = int(invoice_credits.line_count)
        target_amount = Decimal(str(invoice_credits.total_credit))

        logger.info("")
        logger.info("  Target: %d INVOICE credit lines on 1400", target_lines)
        logger.info("  Total credit amount: NGN %s", f"{target_amount:,.2f}")

        if target_lines == 0:
            logger.info("  No misposted lines found — nothing to fix.")
            return

        # ── Step 3: Collect affected fiscal periods ──
        logger.info("")
        logger.info("Step 3: Identify affected fiscal periods")

        affected_periods = db.execute(
            text("""
                SELECT DISTINCT je.fiscal_period_id, fp.period_name
                FROM gl.journal_entry_line jl
                JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                LEFT JOIN gl.fiscal_period fp ON fp.fiscal_period_id = je.fiscal_period_id
                WHERE jl.account_id = :acct_1400_id
                  AND jl.credit_amount > 0
                  AND je.source_document_type = 'INVOICE'
                  AND je.status = 'POSTED'
                  AND je.organization_id = :org_id
                  AND je.reference NOT LIKE 'FIX-%%'
                  AND je.fiscal_period_id IS NOT NULL
                ORDER BY fp.period_name
            """),
            {"acct_1400_id": acct_1400_id, "org_id": str(ORG_ID)},
        ).all()

        affected_period_ids = [str(r.fiscal_period_id) for r in affected_periods]
        logger.info("  Affected periods: %d", len(affected_period_ids))
        for r in affected_periods[:5]:
            logger.info("    %s", r.period_name)
        if len(affected_periods) > 5:
            logger.info("    ... and %d more", len(affected_periods) - 5)

        # Add the FIX journal's period to rebuild list
        if fix_journal and fix_journal.fiscal_period_id:
            fix_period_id = str(fix_journal.fiscal_period_id)
            if fix_period_id not in affected_period_ids:
                affected_period_ids.append(fix_period_id)

        # ── Step 4: Repoint journal_entry_line ──
        logger.info("")
        logger.info("Step 4: Repoint journal_entry_line.account_id (1400 → 4000)")

        if execute:
            updated_jel = db.execute(
                text("""
                    UPDATE gl.journal_entry_line jl
                    SET account_id = :acct_4000_id
                    FROM gl.journal_entry je
                    WHERE jl.journal_entry_id = je.journal_entry_id
                      AND jl.account_id = :acct_1400_id
                      AND jl.credit_amount > 0
                      AND je.source_document_type = 'INVOICE'
                      AND je.status = 'POSTED'
                      AND je.organization_id = :org_id
                      AND je.reference NOT LIKE 'FIX-%%'
                """),
                {
                    "acct_4000_id": acct_4000_id,
                    "acct_1400_id": acct_1400_id,
                    "org_id": str(ORG_ID),
                },
            )
            logger.info(
                "    Repointed %d journal_entry_line rows", updated_jel.rowcount
            )
        else:
            logger.info("    Would repoint %d journal_entry_line rows", target_lines)

        # ── Step 5: Repoint posted_ledger_line ──
        logger.info("")
        logger.info("Step 5: Repoint posted_ledger_line (account_id + account_code)")

        if execute:
            # Match PLL rows via journal_line_id join to the same criteria
            updated_pll = db.execute(
                text("""
                    UPDATE gl.posted_ledger_line pll
                    SET account_id = :acct_4000_id,
                        account_code = '4000'
                    FROM gl.journal_entry je
                    WHERE pll.journal_entry_id = je.journal_entry_id
                      AND pll.account_id = :acct_1400_id
                      AND pll.credit_amount > 0
                      AND je.source_document_type = 'INVOICE'
                      AND je.status = 'POSTED'
                      AND je.organization_id = :org_id
                      AND je.reference NOT LIKE 'FIX-%%'
                """),
                {
                    "acct_4000_id": acct_4000_id,
                    "acct_1400_id": acct_1400_id,
                    "org_id": str(ORG_ID),
                },
            )
            logger.info(
                "    Repointed %d posted_ledger_line rows", updated_pll.rowcount
            )
        else:
            logger.info("    (dry run — no changes)")

        # ── Step 6: Verify invoice_line.revenue_account_id ──
        logger.info("")
        logger.info("Step 6: Verify ar.invoice_line.revenue_account_id")

        remaining_inv_lines = db.execute(
            text("""
                SELECT COUNT(*) FROM ar.invoice_line
                WHERE revenue_account_id = :acct_1400_id
            """),
            {"acct_1400_id": acct_1400_id},
        ).scalar()

        if remaining_inv_lines and remaining_inv_lines > 0:
            logger.info(
                "  %d invoice lines still have revenue_account_id = 1400",
                remaining_inv_lines,
            )
            if execute:
                fixed_inv = db.execute(
                    text("""
                        UPDATE ar.invoice_line
                        SET revenue_account_id = :acct_4000_id
                        WHERE revenue_account_id = :acct_1400_id
                    """),
                    {"acct_4000_id": acct_4000_id, "acct_1400_id": acct_1400_id},
                )
                logger.info("    Fixed %d invoice lines", fixed_inv.rowcount)
        else:
            logger.info(
                "  All invoice lines already corrected (revenue_account_id = 4000)."
            )

        # ── Step 7: Remove FIX-TR-REVENUE-RECLASS correction journal ──
        logger.info("")
        logger.info("Step 7: Remove FIX-TR-REVENUE-RECLASS correction journal")

        if fix_journal and execute:
            fix_je_id = str(fix_journal.journal_entry_id)

            deleted_pll = db.execute(
                text("DELETE FROM gl.posted_ledger_line WHERE journal_entry_id = :id"),
                {"id": fix_je_id},
            )
            logger.info("    Deleted %d posted_ledger_line rows", deleted_pll.rowcount)

            deleted_jel = db.execute(
                text("DELETE FROM gl.journal_entry_line WHERE journal_entry_id = :id"),
                {"id": fix_je_id},
            )
            logger.info("    Deleted %d journal_entry_line rows", deleted_jel.rowcount)

            db.execute(
                text("""
                    UPDATE gl.journal_entry
                    SET status = 'VOID',
                        description = 'VOIDED: ' || description || ' [Replaced by source fix]'
                    WHERE journal_entry_id = :id
                """),
                {"id": fix_je_id},
            )
            logger.info("    Voided %s", fix_journal.journal_number)
        elif not fix_journal:
            logger.info("    No FIX journal to remove.")
        else:
            logger.info("    (dry run — no changes)")

        # ── Step 8: Rebuild account_balance for affected periods ──
        logger.info("")
        logger.info(
            "Step 8: Rebuild account_balance for %d affected periods",
            len(affected_period_ids),
        )

        if execute:
            from app.services.finance.gl.account_balance import AccountBalanceService

            rebuilt_count = 0
            for period_id_str in affected_period_ids:
                period_id = UUID(period_id_str)

                period_name = db.execute(
                    text(
                        "SELECT period_name FROM gl.fiscal_period "
                        "WHERE fiscal_period_id = :pid"
                    ),
                    {"pid": period_id_str},
                ).scalar()

                count = AccountBalanceService.rebuild_balances_for_period(
                    db=db,
                    organization_id=ORG_ID,
                    fiscal_period_id=period_id,
                )
                logger.info(
                    "    %-20s  %4d balance records",
                    period_name or period_id_str[:8],
                    count,
                )
                rebuilt_count += count

            logger.info("    Total rebuilt: %d", rebuilt_count)
        else:
            logger.info("    (dry run — no rebuild)")

        # ── Verification ──
        logger.info("")
        logger.info("=" * 70)
        logger.info("VERIFICATION")
        logger.info("=" * 70)

        remaining_credits = db.execute(
            text("""
                SELECT COUNT(jl.line_id), COALESCE(SUM(jl.credit_amount), 0)
                FROM gl.journal_entry_line jl
                JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                WHERE jl.account_id = :acct_1400_id
                  AND jl.credit_amount > 0
                  AND je.source_document_type = 'INVOICE'
                  AND je.status = 'POSTED'
                  AND je.organization_id = :org_id
            """),
            {"acct_1400_id": acct_1400_id, "org_id": str(ORG_ID)},
        ).one()

        logger.info(
            "  Remaining INVOICE credits on 1400: %d lines, NGN %s",
            remaining_credits[0],
            f"{Decimal(str(remaining_credits[1])):,.2f}",
        )

        # ── Summary ──
        logger.info("")
        logger.info("=" * 70)
        logger.info("SUMMARY")
        logger.info("=" * 70)
        logger.info("  GL lines repointed (1400→4000):  %d", target_lines)
        logger.info("  Amount repointed:  NGN %s", f"{target_amount:,.2f}")
        logger.info("  FIX journal voided:  %s", "Yes" if fix_journal else "N/A")
        logger.info("  Periods rebuilt:  %d", len(affected_period_ids))

        if execute:
            db.commit()
            logger.info("")
            logger.info("All changes committed.")
        else:
            logger.info("")
            logger.info("DRY RUN — no changes made. Run with --execute to apply.")


if __name__ == "__main__":
    main()
