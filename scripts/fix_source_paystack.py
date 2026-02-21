"""
Fix at Source — Paystack Account Consolidation: Repoint 1210 → 1211.

Root cause: Customer payment receipts post debits to 1211 (Paystack OPEX),
but bank settlement transfers and fees credit 1210 (Paystack). Both represent
the same Paystack wallet, but the split means neither account balances correctly.

This script repoints all 1210 journal lines to 1211, consolidating to a single
account, then removes the FIX-PAYSTACK-NET correction journal.

Tables updated:
  1. gl.journal_entry_line  — account_id (1210 → 1211)
  2. gl.posted_ledger_line  — account_id + account_code
  3. gl.account             — 1210 marked inactive
  4. gl.journal_entry       — FIX-PAYSTACK-NET marked VOID
  5. gl.account_balance     — rebuilt for affected periods

Usage:
    python scripts/fix_source_paystack.py --dry-run
    python scripts/fix_source_paystack.py --execute
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
logger = logging.getLogger("fix_source_paystack")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix at source: consolidate Paystack 1210 into 1211."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    execute = args.execute

    with SessionLocal() as db:
        logger.info("=" * 70)
        logger.info("FIX AT SOURCE — PAYSTACK: Consolidate 1210 → 1211")
        logger.info("=" * 70)

        # ── Resolve accounts ──
        accounts = db.execute(
            text("""
                SELECT account_id, account_code, account_name
                FROM gl.account
                WHERE account_code IN ('1210', '1211')
                  AND organization_id = :org_id
                  AND is_active = true
                ORDER BY account_code
            """),
            {"org_id": str(ORG_ID)},
        ).all()

        acct_map = {
            r.account_code: {"id": str(r.account_id), "name": r.account_name}
            for r in accounts
        }

        if "1210" not in acct_map:
            logger.error("Account 1210 not found (may already be inactive).")
            return
        if "1211" not in acct_map:
            logger.error("Account 1211 not found!")
            return

        acct_1210_id = acct_map["1210"]["id"]
        acct_1211_id = acct_map["1211"]["id"]

        logger.info("  1210: %s (%s)", acct_map["1210"]["name"], acct_1210_id)
        logger.info("  1211: %s (%s)", acct_map["1211"]["name"], acct_1211_id)

        # ── Step 1: Find the FIX-PAYSTACK-NET correction journal ──
        logger.info("")
        logger.info("Step 1: Locate FIX-PAYSTACK-NET correction journal")

        fix_journal = db.execute(
            text("""
                SELECT journal_entry_id, journal_number, fiscal_period_id,
                       total_debit, status
                FROM gl.journal_entry
                WHERE reference = 'FIX-PAYSTACK-NET'
                  AND organization_id = :org_id
            """),
            {"org_id": str(ORG_ID)},
        ).one_or_none()

        if fix_journal:
            logger.info(
                "  Found: %s (status=%s, amount=%s)",
                fix_journal.journal_number,
                fix_journal.status,
                f"{fix_journal.total_debit:,.2f}",
            )
        else:
            logger.warning("  FIX-PAYSTACK-NET not found — may already be voided.")

        # ── Step 2: Count lines to repoint ──
        logger.info("")
        logger.info("Step 2: Count journal lines on 1210 (excluding FIX journals)")

        line_stats = db.execute(
            text("""
                SELECT
                    COUNT(jl.line_id) AS line_count,
                    COALESCE(SUM(jl.debit_amount), 0) AS total_debit,
                    COALESCE(SUM(jl.credit_amount), 0) AS total_credit
                FROM gl.journal_entry_line jl
                JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                WHERE jl.account_id = :acct_1210_id
                  AND je.reference NOT LIKE 'FIX-%%'
            """),
            {"acct_1210_id": acct_1210_id},
        ).one()

        target_lines = int(line_stats.line_count)
        logger.info("  Lines to repoint: %d", target_lines)
        logger.info("  Total DR: NGN %s", f"{line_stats.total_debit:,.2f}")
        logger.info("  Total CR: NGN %s", f"{line_stats.total_credit:,.2f}")

        if target_lines == 0:
            logger.info("  No lines found — nothing to do.")
            return

        # By source type
        by_source = db.execute(
            text("""
                SELECT je.source_document_type,
                       COUNT(jl.line_id) AS cnt,
                       SUM(jl.debit_amount) AS dr,
                       SUM(jl.credit_amount) AS cr
                FROM gl.journal_entry_line jl
                JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                WHERE jl.account_id = :acct_1210_id
                  AND je.reference NOT LIKE 'FIX-%%'
                GROUP BY je.source_document_type
                ORDER BY cnt DESC
            """),
            {"acct_1210_id": acct_1210_id},
        ).all()

        logger.info("")
        logger.info("  Breakdown by source:")
        for row in by_source:
            logger.info(
                "    %-25s  %5d lines  DR %s  CR %s",
                row.source_document_type or "(null)",
                row.cnt,
                f"{row.dr:,.2f}",
                f"{row.cr:,.2f}",
            )

        # ── Step 3: Collect affected periods ──
        affected_periods = db.execute(
            text("""
                SELECT DISTINCT je.fiscal_period_id
                FROM gl.journal_entry_line jl
                JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                WHERE jl.account_id = :acct_1210_id
                  AND je.reference NOT LIKE 'FIX-%%'
                  AND je.fiscal_period_id IS NOT NULL
            """),
            {"acct_1210_id": acct_1210_id},
        ).all()

        affected_period_ids = [str(r.fiscal_period_id) for r in affected_periods]
        if fix_journal and fix_journal.fiscal_period_id:
            fix_period = str(fix_journal.fiscal_period_id)
            if fix_period not in affected_period_ids:
                affected_period_ids.append(fix_period)

        logger.info("")
        logger.info("  Affected periods: %d", len(affected_period_ids))

        # ── Step 4: Repoint journal_entry_line ──
        logger.info("")
        logger.info("Step 4: Repoint journal_entry_line (1210 → 1211)")

        if execute:
            updated_jel = db.execute(
                text("""
                    UPDATE gl.journal_entry_line jl
                    SET account_id = :acct_1211_id
                    FROM gl.journal_entry je
                    WHERE jl.journal_entry_id = je.journal_entry_id
                      AND jl.account_id = :acct_1210_id
                      AND je.reference NOT LIKE 'FIX-%%'
                """),
                {"acct_1211_id": acct_1211_id, "acct_1210_id": acct_1210_id},
            )
            logger.info(
                "    Repointed %d journal_entry_line rows", updated_jel.rowcount
            )
        else:
            logger.info("    Would repoint %d rows", target_lines)

        # ── Step 5: Repoint posted_ledger_line ──
        logger.info("")
        logger.info("Step 5: Repoint posted_ledger_line (1210 → 1211)")

        if execute:
            updated_pll = db.execute(
                text("""
                    UPDATE gl.posted_ledger_line pll
                    SET account_id = :acct_1211_id,
                        account_code = '1211'
                    FROM gl.journal_entry je
                    WHERE pll.journal_entry_id = je.journal_entry_id
                      AND pll.account_id = :acct_1210_id
                      AND je.reference NOT LIKE 'FIX-%%'
                """),
                {"acct_1211_id": acct_1211_id, "acct_1210_id": acct_1210_id},
            )
            logger.info(
                "    Repointed %d posted_ledger_line rows", updated_pll.rowcount
            )
        else:
            logger.info("    (dry run — no changes)")

        # ── Step 6: Deactivate account 1210 ──
        logger.info("")
        logger.info("Step 6: Deactivate account 1210")

        if execute:
            db.execute(
                text("""
                    UPDATE gl.account
                    SET is_active = false,
                        account_name = account_name || ' [MERGED INTO 1211]'
                    WHERE account_id = :acct_1210_id
                """),
                {"acct_1210_id": acct_1210_id},
            )
            logger.info("    1210 marked inactive.")
        else:
            logger.info("    (dry run — no changes)")

        # ── Step 7: Remove FIX-PAYSTACK-NET correction journal ──
        logger.info("")
        logger.info("Step 7: Remove FIX-PAYSTACK-NET correction journal")

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

        # ── Step 8: Rebuild account_balance ──
        logger.info("")
        logger.info(
            "Step 8: Rebuild account_balance for %d affected periods",
            len(affected_period_ids),
        )

        if execute:
            from app.services.finance.gl.account_balance import AccountBalanceService

            rebuilt_count = 0
            for period_id_str in affected_period_ids:
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
                    fiscal_period_id=UUID(period_id_str),
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

        remaining = db.execute(
            text("""
                SELECT COUNT(jl.line_id)
                FROM gl.journal_entry_line jl
                WHERE jl.account_id = :acct_1210_id
            """),
            {"acct_1210_id": acct_1210_id},
        ).scalar()
        logger.info("  Remaining lines on 1210: %d", remaining or 0)

        # Show consolidated 1211 balance
        balance_1211 = db.execute(
            text("""
                SELECT SUM(jl.debit_amount) AS dr, SUM(jl.credit_amount) AS cr
                FROM gl.journal_entry_line jl
                JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                WHERE jl.account_id = :acct_1211_id
                  AND je.status = 'POSTED'
                  AND je.organization_id = :org_id
            """),
            {"acct_1211_id": acct_1211_id, "org_id": str(ORG_ID)},
        ).one()
        net = (balance_1211.dr or 0) - (balance_1211.cr or 0)
        logger.info(
            "  1211 consolidated: DR=%s  CR=%s  Net=%s",
            f"{balance_1211.dr or 0:,.2f}",
            f"{balance_1211.cr or 0:,.2f}",
            f"{net:,.2f}",
        )

        # ── Summary ──
        logger.info("")
        logger.info("=" * 70)
        logger.info("SUMMARY")
        logger.info("=" * 70)
        logger.info("  Lines repointed (1210→1211):  %d", target_lines)
        logger.info("  1210 deactivated:             Yes")
        logger.info(
            "  FIX journal voided:           %s", "Yes" if fix_journal else "N/A"
        )
        logger.info("  Periods rebuilt:              %d", len(affected_period_ids))

        if execute:
            db.commit()
            logger.info("")
            logger.info("All changes committed.")
        else:
            logger.info("")
            logger.info("DRY RUN — no changes made. Run with --execute to apply.")


if __name__ == "__main__":
    main()
