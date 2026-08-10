"""
Fix at Source — Pension OB Sign: Swap debit/credit on OB pension lines.

Root cause: Opening Balance journals OB-2022/2023/2024 posted pension (account 2130,
a liability) as DEBIT instead of CREDIT. Pension payable should be a credit balance.

This script swaps the debit_amount and credit_amount on the 3 affected OB lines
and their corresponding posted_ledger_line entries, then removes the
FIX-PENSION-OB-SIGN correction journal.

Also adjusts the contra-entry on Retained Earnings (3100) in the same OB journals.

Tables updated:
  1. gl.journal_entry_line  — swap debit/credit on pension lines
  2. gl.posted_ledger_line  — swap debit/credit
  3. gl.journal_entry       — FIX-PENSION-OB-SIGN marked VOID
  4. gl.account_balance     — rebuilt for affected periods

Usage:
    python scripts/fix_source_pension_ob.py --dry-run
    python scripts/fix_source_pension_ob.py --execute
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
logger = logging.getLogger("fix_source_pension_ob")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix at source: swap pension debit/credit on OB journals."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    execute = args.execute

    with SessionLocal() as db:
        logger.info("=" * 70)
        logger.info("FIX AT SOURCE — PENSION OB SIGN: Swap debit/credit on OB lines")
        logger.info("=" * 70)

        # ── Step 1: Find the wrong-sign pension lines in OB journals ──
        logger.info("")
        logger.info("Step 1: Identify wrong-sign pension lines in OB journals")

        pension_lines = db.execute(
            text("""
                SELECT
                    jl.line_id,
                    je.journal_entry_id,
                    je.reference,
                    je.fiscal_period_id,
                    a.account_code,
                    a.account_name,
                    jl.debit_amount,
                    jl.credit_amount,
                    jl.debit_amount_functional,
                    jl.credit_amount_functional
                FROM gl.journal_entry_line jl
                JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                JOIN gl.account a ON a.account_id = jl.account_id
                WHERE je.organization_id = :org_id
                  AND je.reference IN ('OB-2022', 'OB-2023', 'OB-2024')
                  AND a.account_code = '2130'
                ORDER BY je.reference
            """),
            {"org_id": str(ORG_ID)},
        ).all()

        if not pension_lines:
            logger.info("  No pension lines found in OB journals.")
            return

        affected_period_ids: set[str] = set()

        for line in pension_lines:
            logger.info(
                "  %s  %s (%s)  DR=%s  CR=%s  %s",
                line.reference,
                line.account_code,
                line.account_name,
                f"{line.debit_amount:,.2f}",
                f"{line.credit_amount:,.2f}",
                "← WRONG (liability should be CR)" if line.debit_amount > 0 else "OK",
            )
            if line.fiscal_period_id:
                affected_period_ids.add(str(line.fiscal_period_id))

        # Only fix lines where pension is on the wrong side (debit instead of credit)
        wrong_lines = [l for l in pension_lines if l.debit_amount > 0]
        if not wrong_lines:
            logger.info("  No wrong-sign lines found — all already correct.")
            return

        logger.info("")
        logger.info("  Lines to fix: %d", len(wrong_lines))
        total_swap = sum(l.debit_amount for l in wrong_lines)
        logger.info("  Total amount to swap: NGN %s", f"{total_swap:,.2f}")

        # ── Step 2: Find the FIX-PENSION-OB-SIGN correction journal ──
        logger.info("")
        logger.info("Step 2: Locate FIX-PENSION-OB-SIGN correction journal")

        fix_journal = db.execute(
            text("""
                SELECT journal_entry_id, journal_number, fiscal_period_id,
                       total_debit, status
                FROM gl.journal_entry
                WHERE reference = 'FIX-PENSION-OB-SIGN'
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
            if fix_journal.fiscal_period_id:
                affected_period_ids.add(str(fix_journal.fiscal_period_id))
        else:
            logger.warning("  FIX-PENSION-OB-SIGN not found — may already be voided.")

        # ── Step 3: Swap debit/credit on journal_entry_line ──
        logger.info("")
        logger.info("Step 3: Swap debit/credit on journal_entry_line")

        if execute:
            for line in wrong_lines:
                # Swap: move debit_amount to credit_amount, zero out debit
                db.execute(
                    text("""
                        UPDATE gl.journal_entry_line
                        SET debit_amount = credit_amount,
                            credit_amount = debit_amount,
                            debit_amount_functional = credit_amount_functional,
                            credit_amount_functional = debit_amount_functional
                        WHERE line_id = :line_id
                    """),
                    {"line_id": str(line.line_id)},
                )
                logger.info(
                    "    %s: DR %s → CR %s",
                    line.reference,
                    f"{line.debit_amount:,.2f}",
                    f"{line.debit_amount:,.2f}",
                )

            # Also need to adjust Retained Earnings (3100) contra-entry in same OB journals.
            # The OB journals balance via Retained Earnings. Flipping pension from DR to CR
            # means RE needs to absorb 2x the pension amount (it was balancing the DR,
            # now it needs to balance the CR). But since the FIX journal already posted
            # the double-swap via RE, removing the FIX journal handles this.
            # The OB journals will be temporarily unbalanced by 2x pension until we
            # adjust RE. Let's adjust RE in the OB journals.
            for line in wrong_lines:
                # Find the Retained Earnings line in the same OB journal
                re_account_id = db.execute(
                    text("""
                        SELECT account_id FROM gl.account
                        WHERE account_code = '3100'
                          AND organization_id = :org_id
                          AND is_active = true
                    """),
                    {"org_id": str(ORG_ID)},
                ).scalar()

                if not re_account_id:
                    logger.error("    Account 3100 not found!")
                    continue

                # Adjust RE: add 2x pension amount to credit side
                # (was balancing pension DR, now needs to balance pension CR)
                double_amount = line.debit_amount * 2
                db.execute(
                    text("""
                        UPDATE gl.journal_entry_line
                        SET credit_amount = credit_amount + :amount,
                            credit_amount_functional = COALESCE(credit_amount_functional, 0) + :amount
                        WHERE journal_entry_id = :je_id
                          AND account_id = :re_id
                    """),
                    {
                        "amount": str(double_amount),
                        "je_id": str(line.journal_entry_id),
                        "re_id": str(re_account_id),
                    },
                )
                logger.info(
                    "    %s: 3100 RE credit += %s",
                    line.reference,
                    f"{double_amount:,.2f}",
                )

            # Update journal totals to stay balanced
            for ref in ("OB-2022", "OB-2023", "OB-2024"):
                db.execute(
                    text("""
                        UPDATE gl.journal_entry je
                        SET total_debit = sub.total_debit,
                            total_credit = sub.total_credit,
                            total_debit_functional = sub.total_debit,
                            total_credit_functional = sub.total_credit
                        FROM (
                            SELECT journal_entry_id,
                                   SUM(debit_amount) AS total_debit,
                                   SUM(credit_amount) AS total_credit
                            FROM gl.journal_entry_line
                            WHERE journal_entry_id = (
                                SELECT journal_entry_id FROM gl.journal_entry
                                WHERE reference = :ref AND organization_id = :org_id
                            )
                            GROUP BY journal_entry_id
                        ) sub
                        WHERE je.journal_entry_id = sub.journal_entry_id
                    """),
                    {"ref": ref, "org_id": str(ORG_ID)},
                )
        else:
            logger.info("    (dry run — no changes)")

        # ── Step 4: Swap on posted_ledger_line ──
        logger.info("")
        logger.info("Step 4: Swap debit/credit on posted_ledger_line")

        if execute:
            for line in wrong_lines:
                updated = db.execute(
                    text("""
                        UPDATE gl.posted_ledger_line
                        SET debit_amount = credit_amount,
                            credit_amount = debit_amount,
                            original_debit_amount = original_credit_amount,
                            original_credit_amount = original_debit_amount
                        WHERE journal_line_id = :line_id
                    """),
                    {"line_id": str(line.line_id)},
                )
                logger.info(
                    "    %s pension PLL: %d rows swapped",
                    line.reference,
                    updated.rowcount,
                )

            # Also update RE posted_ledger_lines in OB journals
            for line in wrong_lines:
                re_account_id = db.execute(
                    text("""
                        SELECT account_id FROM gl.account
                        WHERE account_code = '3100'
                          AND organization_id = :org_id
                          AND is_active = true
                    """),
                    {"org_id": str(ORG_ID)},
                ).scalar()

                if re_account_id:
                    double_amount = line.debit_amount * 2
                    db.execute(
                        text("""
                            UPDATE gl.posted_ledger_line
                            SET credit_amount = credit_amount + :amount,
                                original_credit_amount = COALESCE(original_credit_amount, 0) + :amount
                            WHERE journal_entry_id = :je_id
                              AND account_id = :re_id
                        """),
                        {
                            "amount": str(double_amount),
                            "je_id": str(line.journal_entry_id),
                            "re_id": str(re_account_id),
                        },
                    )
        else:
            logger.info("    (dry run — no changes)")

        # ── Step 5: Remove FIX-PENSION-OB-SIGN correction journal ──
        logger.info("")
        logger.info("Step 5: Remove FIX-PENSION-OB-SIGN correction journal")

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

        # ── Step 6: Rebuild account_balance ──
        logger.info("")
        logger.info(
            "Step 6: Rebuild account_balance for %d affected periods",
            len(affected_period_ids),
        )

        if execute:
            from app.services.finance.gl.account_balance import AccountBalanceService

            for period_id_str in sorted(affected_period_ids):
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
                logger.info("    %-20s  %4d balance records", period_name or "?", count)
        else:
            logger.info("    (dry run — no rebuild)")

        # ── Verification ──
        logger.info("")
        logger.info("=" * 70)
        logger.info("VERIFICATION")
        logger.info("=" * 70)

        # Check pension lines in OB journals
        final_check = db.execute(
            text("""
                SELECT je.reference, jl.debit_amount, jl.credit_amount
                FROM gl.journal_entry_line jl
                JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                JOIN gl.account a ON a.account_id = jl.account_id
                WHERE je.reference IN ('OB-2022', 'OB-2023', 'OB-2024')
                  AND a.account_code = '2130'
                  AND je.organization_id = :org_id
                ORDER BY je.reference
            """),
            {"org_id": str(ORG_ID)},
        ).all()

        for row in final_check:
            side = "CR (correct)" if row.credit_amount > 0 else "DR (STILL WRONG)"
            logger.info(
                "  %s  2130 Pension: DR=%s CR=%s  %s",
                row.reference,
                f"{row.debit_amount:,.2f}",
                f"{row.credit_amount:,.2f}",
                side,
            )

        # Check OB journal balance
        for ref in ("OB-2022", "OB-2023", "OB-2024"):
            balance_check = db.execute(
                text("""
                    SELECT SUM(debit_amount) AS td, SUM(credit_amount) AS tc
                    FROM gl.journal_entry_line jl
                    JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                    WHERE je.reference = :ref AND je.organization_id = :org_id
                """),
                {"ref": ref, "org_id": str(ORG_ID)},
            ).one()
            diff = (balance_check.td or 0) - (balance_check.tc or 0)
            status = (
                "BALANCED" if abs(diff) < Decimal("0.01") else f"IMBALANCE: {diff:,.6f}"
            )
            logger.info(
                "  %s: DR=%s CR=%s  %s",
                ref,
                f"{balance_check.td:,.2f}",
                f"{balance_check.tc:,.2f}",
                status,
            )

        # ── Summary ──
        logger.info("")
        logger.info("=" * 70)
        logger.info("SUMMARY")
        logger.info("=" * 70)
        logger.info("  Pension lines swapped:  %d", len(wrong_lines))
        logger.info("  Total amount swapped:   NGN %s", f"{total_swap:,.2f}")
        logger.info("  FIX journal voided:     %s", "Yes" if fix_journal else "N/A")

        if execute:
            db.commit()
            logger.info("")
            logger.info("All changes committed.")
        else:
            logger.info("")
            logger.info("DRY RUN — no changes made. Run with --execute to apply.")


if __name__ == "__main__":
    main()
