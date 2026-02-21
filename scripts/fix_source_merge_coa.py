"""
Fix at Source — MERGE-COA: Repoint journal lines from shadow accounts to canonical.

Instead of correction journals that DR canonical / CR shadow, this script changes
the account_id on the ORIGINAL journal lines so every period's trial balance is
inherently correct. Then removes the 36 MERGE-COA correction journals.

Tables updated:
  1. gl.journal_entry_line  — account_id
  2. gl.posted_ledger_line  — account_id + account_code (denormalized)
  3. gl.journal_entry       — MERGE-COA journals marked VOID
  4. gl.account_balance     — rebuilt for affected periods

Usage:
    python scripts/fix_source_merge_coa.py --dry-run
    python scripts/fix_source_merge_coa.py --execute
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
logger = logging.getLogger("fix_source_merge_coa")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix at source: repoint shadow account journal lines to canonical."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    execute = args.execute

    with SessionLocal() as db:
        logger.info("=" * 70)
        logger.info("FIX AT SOURCE — MERGE-COA: Repoint shadow → canonical accounts")
        logger.info("=" * 70)

        # ── Step 1: Extract the shadow→canonical mapping from MERGE-COA journals ──
        logger.info("")
        logger.info("Step 1: Extract shadow → canonical account mapping")

        # Each MERGE-COA journal has exactly 2 lines:
        #   - DEBIT on canonical (active) account  (receiving the balance)
        #   - CREDIT on shadow (inactive) account  (zeroing the shadow)
        # Exception: liability/equity/revenue accounts are reversed (debit shadow, credit canonical)
        merge_pairs = db.execute(
            text("""
                SELECT
                    je.journal_entry_id,
                    je.reference,
                    shadow.account_id   AS shadow_account_id,
                    shadow.account_code AS shadow_account_code,
                    shadow.account_name AS shadow_account_name,
                    canon.account_id    AS canonical_account_id,
                    canon.account_code  AS canonical_account_code,
                    canon.account_name  AS canonical_account_name
                FROM gl.journal_entry je
                -- Get the line on the INACTIVE account (shadow)
                JOIN gl.journal_entry_line jl_shadow
                    ON jl_shadow.journal_entry_id = je.journal_entry_id
                JOIN gl.account shadow
                    ON shadow.account_id = jl_shadow.account_id
                    AND shadow.is_active = false
                -- Get the line on the ACTIVE account (canonical)
                JOIN gl.journal_entry_line jl_canon
                    ON jl_canon.journal_entry_id = je.journal_entry_id
                    AND jl_canon.line_id != jl_shadow.line_id
                JOIN gl.account canon
                    ON canon.account_id = jl_canon.account_id
                    AND canon.is_active = true
                WHERE je.organization_id = :org_id
                  AND je.reference LIKE 'MERGE-COA%%'
                  AND je.status = 'POSTED'
                ORDER BY je.reference
            """),
            {"org_id": str(ORG_ID)},
        ).all()

        if not merge_pairs:
            logger.info("  No MERGE-COA journals found. Nothing to do.")
            return

        logger.info("  Found %d MERGE-COA journals", len(merge_pairs))

        # Build mapping: shadow_account_id → canonical_account_id
        shadow_map: dict[str, dict[str, str]] = {}
        merge_journal_ids: list[str] = []

        for row in merge_pairs:
            shadow_id = str(row.shadow_account_id)
            shadow_map[shadow_id] = {
                "shadow_code": row.shadow_account_code,
                "shadow_name": row.shadow_account_name,
                "canonical_id": str(row.canonical_account_id),
                "canonical_code": row.canonical_account_code,
                "canonical_name": row.canonical_account_name,
            }
            merge_journal_ids.append(str(row.journal_entry_id))

        logger.info("")
        logger.info("  Shadow → Canonical mapping:")
        for _shadow_id, info in shadow_map.items():
            logger.info(
                "    %-35s → %s %s",
                info["shadow_name"][:35],
                info["canonical_code"],
                info["canonical_name"][:30],
            )

        # ── Step 2: Count source lines to repoint ──
        logger.info("")
        logger.info("Step 2: Count original journal lines on shadow accounts")

        total_repointed = 0
        affected_period_ids: set[str] = set()

        for shadow_id, info in shadow_map.items():
            # Count lines on shadow account (excluding MERGE-COA journals themselves)
            result = db.execute(
                text("""
                    SELECT
                        COUNT(jl.line_id) AS line_count,
                        COALESCE(SUM(jl.debit_amount), 0) AS total_debit,
                        COALESCE(SUM(jl.credit_amount), 0) AS total_credit
                    FROM gl.journal_entry_line jl
                    JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                    WHERE jl.account_id = :shadow_id
                      AND je.reference NOT LIKE 'MERGE-COA%%'
                """),
                {"shadow_id": shadow_id},
            ).one()

            line_count = int(result.line_count)

            if line_count == 0:
                continue

            logger.info(
                "    %-35s  %6d lines  DR %s  CR %s",
                info["shadow_name"][:35],
                line_count,
                f"{result.total_debit:,.2f}",
                f"{result.total_credit:,.2f}",
            )

            # Collect affected fiscal periods
            period_rows = db.execute(
                text("""
                    SELECT DISTINCT je.fiscal_period_id
                    FROM gl.journal_entry_line jl
                    JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id
                    WHERE jl.account_id = :shadow_id
                      AND je.reference NOT LIKE 'MERGE-COA%%'
                      AND je.fiscal_period_id IS NOT NULL
                """),
                {"shadow_id": shadow_id},
            ).all()
            for pr in period_rows:
                affected_period_ids.add(str(pr.fiscal_period_id))

            total_repointed += line_count

        logger.info("")
        logger.info("  Total lines to repoint:     %d", total_repointed)
        logger.info("  Affected fiscal periods:    %d", len(affected_period_ids))

        # Also get period for the MERGE-COA journals themselves (Feb 2026)
        merge_period_rows = db.execute(
            text("""
                SELECT DISTINCT fiscal_period_id
                FROM gl.journal_entry
                WHERE journal_entry_id = ANY(:ids)
                  AND fiscal_period_id IS NOT NULL
            """),
            {"ids": merge_journal_ids},
        ).all()
        for pr in merge_period_rows:
            affected_period_ids.add(str(pr.fiscal_period_id))

        # ── Step 3: Repoint journal_entry_line ──
        logger.info("")
        logger.info("Step 3: Repoint journal_entry_line.account_id")

        if execute:
            for shadow_id, info in shadow_map.items():
                updated = db.execute(
                    text("""
                        UPDATE gl.journal_entry_line
                        SET account_id = :canonical_id
                        WHERE account_id = :shadow_id
                          AND journal_entry_id NOT IN (
                              SELECT journal_entry_id FROM gl.journal_entry
                              WHERE reference LIKE 'MERGE-COA%%'
                          )
                    """),
                    {"canonical_id": info["canonical_id"], "shadow_id": shadow_id},
                )
                if updated.rowcount > 0:
                    logger.info(
                        "    %-35s  %6d lines repointed",
                        info["shadow_name"][:35],
                        updated.rowcount,
                    )
        else:
            logger.info("    (dry run — no changes)")

        # ── Step 4: Repoint posted_ledger_line ──
        logger.info("")
        logger.info("Step 4: Repoint posted_ledger_line.account_id + account_code")

        if execute:
            for shadow_id, info in shadow_map.items():
                updated = db.execute(
                    text("""
                        UPDATE gl.posted_ledger_line
                        SET account_id = :canonical_id,
                            account_code = :canonical_code
                        WHERE account_id = :shadow_id
                          AND journal_entry_id NOT IN (
                              SELECT journal_entry_id FROM gl.journal_entry
                              WHERE reference LIKE 'MERGE-COA%%'
                          )
                    """),
                    {
                        "canonical_id": info["canonical_id"],
                        "canonical_code": info["canonical_code"],
                        "shadow_id": shadow_id,
                    },
                )
                if updated.rowcount > 0:
                    logger.info(
                        "    %-35s  %6d PLL rows repointed",
                        info["shadow_name"][:35],
                        updated.rowcount,
                    )
        else:
            logger.info("    (dry run — no changes)")

        # ── Step 5: Remove MERGE-COA correction journals ──
        logger.info("")
        logger.info("Step 5: Remove MERGE-COA correction journals")

        if execute:
            # Delete posted_ledger_line entries for MERGE-COA journals
            deleted_pll = db.execute(
                text("""
                    DELETE FROM gl.posted_ledger_line
                    WHERE journal_entry_id = ANY(:ids)
                """),
                {"ids": merge_journal_ids},
            )
            logger.info("    Deleted %d posted_ledger_line rows", deleted_pll.rowcount)

            # Delete journal_entry_line entries
            deleted_jel = db.execute(
                text("""
                    DELETE FROM gl.journal_entry_line
                    WHERE journal_entry_id = ANY(:ids)
                """),
                {"ids": merge_journal_ids},
            )
            logger.info("    Deleted %d journal_entry_line rows", deleted_jel.rowcount)

            # Mark journals as VOID
            voided = db.execute(
                text("""
                    UPDATE gl.journal_entry
                    SET status = 'VOID',
                        description = 'VOIDED: ' || description || ' [Replaced by source fix]'
                    WHERE journal_entry_id = ANY(:ids)
                """),
                {"ids": merge_journal_ids},
            )
            logger.info("    Voided %d MERGE-COA journals", voided.rowcount)
        else:
            logger.info(
                "    Would remove %d MERGE-COA journals", len(merge_journal_ids)
            )

        # ── Step 6: Rebuild account_balance for affected periods ──
        logger.info("")
        logger.info(
            "Step 6: Rebuild account_balance for %d affected periods",
            len(affected_period_ids),
        )

        if execute:
            from app.services.finance.gl.account_balance import AccountBalanceService

            rebuilt_count = 0
            for period_id_str in sorted(affected_period_ids):
                period_id = UUID(period_id_str)

                # Get period name for logging
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
                    "    %-20s  %4d balance records rebuilt",
                    period_name or period_id_str[:8],
                    count,
                )
                rebuilt_count += count

            logger.info("    Total balance records rebuilt: %d", rebuilt_count)
        else:
            logger.info("    (dry run — no rebuild)")

        # ── Verification ──
        logger.info("")
        logger.info("=" * 70)
        logger.info("VERIFICATION")
        logger.info("=" * 70)

        # Check remaining lines on shadow accounts
        remaining = db.execute(
            text("""
                SELECT a.account_name, COUNT(jl.line_id) AS remaining_lines
                FROM gl.journal_entry_line jl
                JOIN gl.account a ON a.account_id = jl.account_id
                WHERE a.is_active = false
                  AND a.organization_id = :org_id
                  AND a.account_code !~ '^[0-9]'
                GROUP BY a.account_name
                HAVING COUNT(jl.line_id) > 0
                ORDER BY remaining_lines DESC
            """),
            {"org_id": str(ORG_ID)},
        ).all()

        if remaining:
            logger.warning("  Lines still on shadow accounts:")
            for row in remaining:
                logger.warning(
                    "    %-40s  %d lines", row.account_name[:40], row.remaining_lines
                )
        else:
            logger.info("  All shadow account lines repointed successfully.")

        # Check MERGE-COA journal status
        merge_status = db.execute(
            text("""
                SELECT status, COUNT(*) AS cnt
                FROM gl.journal_entry
                WHERE reference LIKE 'MERGE-COA%%'
                  AND organization_id = :org_id
                GROUP BY status
            """),
            {"org_id": str(ORG_ID)},
        ).all()
        for row in merge_status:
            logger.info("  MERGE-COA journals: %s = %d", row.status, row.cnt)

        # ── Summary ──
        logger.info("")
        logger.info("=" * 70)
        logger.info("SUMMARY")
        logger.info("=" * 70)
        logger.info("  Shadow accounts repointed:    %d", len(shadow_map))
        logger.info("  Journal lines repointed:      %d", total_repointed)
        logger.info("  MERGE-COA journals voided:    %d", len(merge_journal_ids))
        logger.info("  Affected periods rebuilt:      %d", len(affected_period_ids))

        if execute:
            db.commit()
            logger.info("")
            logger.info("All changes committed.")
        else:
            logger.info("")
            logger.info("DRY RUN — no changes made. Run with --execute to apply.")


if __name__ == "__main__":
    main()
