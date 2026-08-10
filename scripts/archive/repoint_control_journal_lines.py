"""
Chart of Accounts Cleanup — Phase 4b: Re-point journal lines from CONTROL to POSTING.

Since CONTROL accounts can't receive new journal entries (the JournalService rejects
them), we directly UPDATE journal_entry_line.account_id to move lines from CONTROL
accounts to their POSTING counterparts. This is a data migration, not an accounting
transaction — the trial balance remains unchanged since DR/CR amounts don't change.

Usage:
    python scripts/repoint_control_journal_lines.py --dry-run
    python scripts/repoint_control_journal_lines.py --execute
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
logger = logging.getLogger("repoint_control")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")

# ── CONTROL account NAME → POSTING account CODE mapping ──
CONTROL_MAP: dict[str, str] = {
    "UBA Bank": "1202",
    "Zenith 523 Bank": "1200",
    "Zenith 461 Bank": "1200",
    "Zenith 454 Bank": "1200",
    "Zenith USD": "1200",
    "Paystack": "1210",
    "Paystack OPEX": "1211",
    "Cash CBD": "1220",
    "Accounts Receivable": "1400",
    "Trade and Other Payables (USD)": "2000",
    "Expense Payable": "2020",
    "Current Tax Payable": "2100",
    "Pension Payables": "2130",
    "NHF Payables": "2132",
    "PAYE Payables": "2110",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 4b: Re-point journal lines from CONTROL to POSTING."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    execute = args.execute

    with SessionLocal() as db:
        logger.info("=" * 60)
        logger.info("PHASE 4b: Re-point journal lines from CONTROL → POSTING")
        logger.info("=" * 60)

        # Build lookup: CONTROL name → CONTROL account_id
        control_rows = db.execute(
            text("""
                SELECT a.account_id, a.account_name
                FROM gl.account a
                WHERE a.organization_id = :org_id
                  AND a.account_type = 'CONTROL'
                  AND a.is_active = true
            """),
            {"org_id": str(ORG_ID)},
        ).all()

        control_by_name = {r[1]: str(r[0]) for r in control_rows}

        # Build lookup: POSTING code → account_id
        posting_rows = db.execute(
            text("""
                SELECT a.account_id, a.account_code, a.account_name
                FROM gl.account a
                WHERE a.organization_id = :org_id
                  AND a.account_type = 'POSTING'
                  AND a.is_active = true
                  AND a.account_code ~ '^[0-9]'
            """),
            {"org_id": str(ORG_ID)},
        ).all()

        posting_by_code = {
            r[1]: {"id": str(r[0]), "code": r[1], "name": r[2]} for r in posting_rows
        }

        total_lines_moved = 0
        total_accounts_fixed = 0

        for control_name, target_code in CONTROL_MAP.items():
            control_id = control_by_name.get(control_name)
            if not control_id:
                logger.warning(
                    "  CONTROL '%s' not found (may already be inactive)", control_name
                )
                continue

            target = posting_by_code.get(target_code)
            if not target:
                logger.warning(
                    "  POSTING target '%s' not found for '%s'",
                    target_code,
                    control_name,
                )
                continue

            # Count lines on this CONTROL account
            line_count = db.execute(
                text("""
                    SELECT COUNT(*) FROM gl.journal_entry_line
                    WHERE account_id = :control_id
                """),
                {"control_id": control_id},
            ).scalar()

            if line_count == 0:
                logger.info(
                    "  %-40s → %-6s %-30s  (0 lines — skip)",
                    control_name[:40],
                    target["code"],
                    target["name"][:30],
                )
                continue

            logger.info(
                "  %-40s → %-6s %-30s  (%d lines)",
                control_name[:40],
                target["code"],
                target["name"][:30],
                line_count,
            )

            if execute:
                db.execute(
                    text("""
                        UPDATE gl.journal_entry_line
                        SET account_id = :target_id
                        WHERE account_id = :control_id
                    """),
                    {"target_id": target["id"], "control_id": control_id},
                )

                # Deactivate the CONTROL account
                db.execute(
                    text("""
                        UPDATE gl.account
                        SET is_active = false
                        WHERE account_id = :control_id
                    """),
                    {"control_id": control_id},
                )

            total_lines_moved += line_count
            total_accounts_fixed += 1

        logger.info("")
        logger.info("=" * 60)
        logger.info("SUMMARY")
        logger.info("=" * 60)
        logger.info("  CONTROL accounts re-pointed:   %d", total_accounts_fixed)
        logger.info("  Journal lines moved:           %d", total_lines_moved)

        if execute:
            db.commit()
            logger.info("")
            logger.info("Changes committed.")
        else:
            logger.info("")
            logger.info("DRY RUN — no changes made.")


if __name__ == "__main__":
    main()
