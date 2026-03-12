"""
Fix at Source — Paystack Account Consolidation V2: Merge all Paystack accounts into 1211.

The original fix_source_paystack.py consolidated 1210 → 1211 but missed a third
ERPNext-origin account ``Paystack - DT`` (account_id 3c5eab01-...).

Current state (as of 2026-03-12):
  1. 1210 (Paystack [MERGED INTO 1211]) — inactive, still has 6 JEL lines
     from post-merge activity (Feb 2026 AR payments + settlements).
  2. 1211 (Paystack OPEX Account) — active, BANK category.
  3. Paystack - DT (Paystack) — inactive, ASSETS category, 386 JEL lines
     with ₦275.6M credits from INTERBANK_TRANSFER settlements.

Impact: Management accounts shows ₦279M for Paystack instead of ₦3.15M because
settlement credits sit on ``Paystack - DT`` which has a different category (ASSETS
vs BANK) and isn't consolidated with 1211 in JEL queries.

This script:
  1. Repoints all ``Paystack - DT`` JEL lines → 1211
  2. Repoints all ``Paystack - DT`` PLL lines → 1211 (account_id + account_code)
  3. Repoints any remaining 1210 JEL lines → 1211 (post-merge stragglers)
  4. Repoints any remaining 1210 PLL lines → 1211
  5. Cleans up account names (removes [MERGED INTO 1211] suffix)
  6. Deactivates ``Paystack - DT`` if not already
  7. Rebuilds account_balance for all affected periods

Usage:
    python scripts/fix_paystack_consolidation_v2.py --dry-run
    python scripts/fix_paystack_consolidation_v2.py --execute
"""

from __future__ import annotations

import argparse
import logging
import sys
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fix_paystack_v2")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
ACCT_1210_ID = "22c9d3db-4fbd-4e3e-943c-f2cebdcdba0f"
ACCT_1211_ID = "0ebe38df-36cc-4834-b3be-948410bd9565"
ACCT_PAYSTACK_DT_ID = "3c5eab01-be59-45c6-b5c4-9bab3cac9a68"


def _repoint_jel(db: Session, source_id: str, source_label: str, execute: bool) -> int:
    """Repoint journal_entry_line rows from source account to 1211."""
    stats = db.execute(
        text("""
            SELECT COUNT(*) AS cnt,
                   COALESCE(SUM(debit_amount_functional), 0) AS dr,
                   COALESCE(SUM(credit_amount_functional), 0) AS cr
            FROM gl.journal_entry_line
            WHERE account_id = :src
        """),
        {"src": source_id},
    ).one()

    count = int(stats.cnt)
    if count == 0:
        logger.info("  %s: no JEL lines to repoint", source_label)
        return 0

    logger.info(
        "  %s: %d JEL lines (DR=%s, CR=%s)",
        source_label,
        count,
        f"{stats.dr:,.2f}",
        f"{stats.cr:,.2f}",
    )

    if execute:
        result = db.execute(
            text("""
                UPDATE gl.journal_entry_line
                SET account_id = :target
                WHERE account_id = :src
            """),
            {"target": ACCT_1211_ID, "src": source_id},
        )
        logger.info("    → Repointed %d rows", result.rowcount)
        return int(result.rowcount)

    logger.info("    → Would repoint %d rows", count)
    return count


def _repoint_pll(
    db: Session, source_id: str, source_code: str, source_label: str, execute: bool
) -> int:
    """Repoint posted_ledger_line rows from source account to 1211."""
    count = db.execute(
        text("""
            SELECT COUNT(*)
            FROM gl.posted_ledger_line
            WHERE account_id = :src OR account_code = :code
        """),
        {"src": source_id, "code": source_code},
    ).scalar()

    if not count:
        logger.info("  %s: no PLL lines to repoint", source_label)
        return 0

    logger.info("  %s: %d PLL lines to repoint", source_label, count)

    if execute:
        result = db.execute(
            text("""
                UPDATE gl.posted_ledger_line
                SET account_id = :target,
                    account_code = '1211'
                WHERE account_id = :src OR account_code = :code
            """),
            {"target": ACCT_1211_ID, "src": source_id, "code": source_code},
        )
        logger.info("    → Repointed %d rows", result.rowcount)
        return int(result.rowcount)

    logger.info("    → Would repoint %d rows", count)
    return int(count)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Paystack consolidation V2: merge all Paystack accounts into 1211."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    execute = args.execute

    with SessionLocal() as db:
        logger.info("=" * 70)
        logger.info("PAYSTACK CONSOLIDATION V2 — All accounts → 1211")
        logger.info("=" * 70)

        # ── Verify target account ──
        target = db.execute(
            text("""
                SELECT account_code, account_name, is_active
                FROM gl.account WHERE account_id = :id
            """),
            {"id": ACCT_1211_ID},
        ).one_or_none()

        if not target or not target.is_active:
            logger.error("Target account 1211 not found or inactive!")
            return

        logger.info("Target: %s — %s", target.account_code, target.account_name)

        # ── Step 1: Repoint Paystack - DT → 1211 ──
        logger.info("")
        logger.info("Step 1: Repoint 'Paystack - DT' → 1211")
        jel_dt = _repoint_jel(db, ACCT_PAYSTACK_DT_ID, "Paystack - DT", execute)
        pll_dt = _repoint_pll(
            db, ACCT_PAYSTACK_DT_ID, "Paystack - DT", "Paystack - DT", execute
        )

        # ── Step 2: Repoint remaining 1210 → 1211 ──
        logger.info("")
        logger.info("Step 2: Repoint remaining 1210 → 1211 (post-merge stragglers)")
        jel_1210 = _repoint_jel(db, ACCT_1210_ID, "1210", execute)
        pll_1210 = _repoint_pll(db, ACCT_1210_ID, "1210", "1210", execute)

        # ── Step 3: Clean up account names ──
        logger.info("")
        logger.info("Step 3: Clean up account names")

        if execute:
            # Remove [MERGED INTO 1211] suffix from 1210
            db.execute(
                text("""
                    UPDATE gl.account
                    SET account_name = 'Paystack'
                    WHERE account_id = :id
                """),
                {"id": ACCT_1210_ID},
            )
            logger.info("  1210: 'Paystack [MERGED INTO 1211]' → 'Paystack'")

            # Deactivate Paystack - DT (should already be inactive)
            db.execute(
                text("""
                    UPDATE gl.account
                    SET is_active = false,
                        account_name = 'Paystack (ERPNext legacy — merged into 1211)'
                    WHERE account_id = :id
                """),
                {"id": ACCT_PAYSTACK_DT_ID},
            )
            logger.info("  Paystack - DT: renamed + deactivated")
        else:
            logger.info("  Would rename 1210 → 'Paystack'")
            logger.info(
                "  Would rename Paystack - DT → 'Paystack (ERPNext legacy — merged into 1211)'"
            )

        # ── Step 4: Delete account_balance for source accounts ──
        logger.info("")
        logger.info("Step 4: Delete stale account_balance records for source accounts")

        if execute:
            for acct_id, label in [
                (ACCT_1210_ID, "1210"),
                (ACCT_PAYSTACK_DT_ID, "Paystack - DT"),
            ]:
                deleted = db.execute(
                    text("DELETE FROM gl.account_balance WHERE account_id = :id"),
                    {"id": acct_id},
                )
                logger.info("  %s: deleted %d balance records", label, deleted.rowcount)
        else:
            for acct_id, label in [
                (ACCT_1210_ID, "1210"),
                (ACCT_PAYSTACK_DT_ID, "Paystack - DT"),
            ]:
                count = db.execute(
                    text(
                        "SELECT COUNT(*) FROM gl.account_balance WHERE account_id = :id"
                    ),
                    {"id": acct_id},
                ).scalar()
                logger.info("  %s: would delete %d balance records", label, count)

        # ── Step 5: Rebuild account_balance for 1211 ──
        logger.info("")
        logger.info("Step 5: Rebuild account_balance for 1211")

        affected_periods = db.execute(
            text("""
                SELECT DISTINCT je.fiscal_period_id
                FROM gl.journal_entry_line jl
                JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                WHERE jl.account_id = :acct_id
                  AND je.fiscal_period_id IS NOT NULL
            """),
            {"acct_id": ACCT_1211_ID},
        ).all()

        period_ids = [str(r.fiscal_period_id) for r in affected_periods]
        logger.info("  Affected periods: %d", len(period_ids))

        if execute:
            from app.services.finance.gl.account_balance import AccountBalanceService

            rebuilt = 0
            for pid in period_ids:
                period_name = db.execute(
                    text(
                        "SELECT period_name FROM gl.fiscal_period "
                        "WHERE fiscal_period_id = :pid"
                    ),
                    {"pid": pid},
                ).scalar()

                count = AccountBalanceService.rebuild_balances_for_period(
                    db=db,
                    organization_id=ORG_ID,
                    fiscal_period_id=UUID(pid),
                )
                logger.info("    %-20s  %4d records", period_name or pid[:8], count)
                rebuilt += count

            logger.info("    Total rebuilt: %d", rebuilt)
        else:
            logger.info("  (dry run — no rebuild)")

        # ── Verification ──
        logger.info("")
        logger.info("=" * 70)
        logger.info("VERIFICATION")
        logger.info("=" * 70)

        for acct_id, label in [
            (ACCT_1210_ID, "1210"),
            (ACCT_PAYSTACK_DT_ID, "Paystack - DT"),
        ]:
            remaining = db.execute(
                text(
                    "SELECT COUNT(*) FROM gl.journal_entry_line WHERE account_id = :id"
                ),
                {"id": acct_id},
            ).scalar()
            logger.info("  Remaining JEL on %s: %d", label, remaining or 0)

        # Consolidated 1211 balance
        bal = db.execute(
            text("""
                SELECT SUM(jl.debit_amount_functional) AS dr,
                       SUM(jl.credit_amount_functional) AS cr
                FROM gl.journal_entry_line jl
                JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                WHERE jl.account_id = :acct_id
                  AND je.status = 'POSTED'
                  AND je.organization_id = :org_id
            """),
            {"acct_id": ACCT_1211_ID, "org_id": str(ORG_ID)},
        ).one()

        net = (bal.dr or 0) - (bal.cr or 0)
        logger.info(
            "  1211 consolidated: DR=%s  CR=%s  Net=%s",
            f"{bal.dr or 0:,.2f}",
            f"{bal.cr or 0:,.2f}",
            f"{net:,.2f}",
        )

        # ── Summary ──
        logger.info("")
        logger.info("=" * 70)
        logger.info("SUMMARY")
        logger.info("=" * 70)
        logger.info("  Paystack - DT → 1211:  %d JEL + %d PLL", jel_dt, pll_dt)
        logger.info("  1210 → 1211:           %d JEL + %d PLL", jel_1210, pll_1210)
        logger.info("  Account names cleaned:  1210, Paystack - DT")
        logger.info("  Periods rebuilt:        %d", len(period_ids))

        if execute:
            db.commit()
            logger.info("")
            logger.info("All changes committed.")
        else:
            logger.info("")
            logger.info("DRY RUN — no changes made. Run with --execute to apply.")


if __name__ == "__main__":
    main()
