"""
Chart of Accounts Cleanup — Phase 1.

Actions:
1. Deactivate 121 unused ERPNext shadow accounts (zero activity, zero balance)
2. Deactivate 2 test accounts
3. Fix 8 typos in account names
4. Deactivate duplicate shadow accounts that are unused (subset of #1)

Usage:
    python scripts/cleanup_chart_of_accounts.py --dry-run
    python scripts/cleanup_chart_of_accounts.py --execute
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
logger = logging.getLogger("coa_cleanup")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")

# ── Typo corrections (account_id → corrected name) ──
TYPO_FIXES: dict[str, str] = {
    "4b4d61c3-b6db-4e58-819b-956d48efe71c": "Flutterwave",  # was: Fultterwave
    "370d702d-5b21-4635-992a-3f32e2c6fbae": "Accrued Expenses",  # was: Accurued Expenses
    "9a80477f-e6c8-4aff-88b4-fe87c2d33545": "Other Component Equity",  # was: Other Componrnt Equity
    "b3811a2c-e1d4-4ccd-9d1b-15f932588555": "Purchase of Bandwidth and Interconnect",  # was: bandwitdh
    "6ab49b82-c2fc-4019-95d0-99319f129a41": "Accommodation Expenses",  # was: Accomodation
    "9feea755-680e-49d6-a8e3-47e0723ec65b": "Entertainment",  # was: Entertament
    "fe5a9e2f-6575-49f2-a931-4a3454ec7c2a": "Motor Vehicle Repairs & Maintenance",  # was: Vehichle
    "0b5a61cf-6726-4720-bb48-ce6a20678d52": "Equipment Rental",  # was: leading space " Equipment Rental"
}

# ── Test accounts to deactivate ──
TEST_ACCOUNT_IDS = [
    "97fe7263-bb03-4d6c-a5cb-b33e0587daca",  # tesst / test (STATISTICAL)
    "2606fc5c-3f8e-4a38-b5e4-be5f2c8f0c60",  # TEST-172231 / Test API Account (POSTING)
]


def step1_fix_typos(db: object, *, execute: bool) -> int:
    """Fix account name typos."""
    logger.info("Step 1: Fix account name typos")
    fixed = 0

    for acct_id, corrected_name in TYPO_FIXES.items():
        row = db.execute(
            text("""
                SELECT account_name FROM gl.account
                WHERE account_id = :acct_id AND organization_id = :org_id
            """),
            {"acct_id": acct_id, "org_id": str(ORG_ID)},
        ).one_or_none()

        if not row:
            logger.warning("  Account %s not found", acct_id)
            continue

        old_name = row[0]
        if old_name == corrected_name:
            logger.info("  %s — already correct", corrected_name)
            continue

        logger.info("  '%s' → '%s'", old_name, corrected_name)

        if execute:
            db.execute(
                text("""
                    UPDATE gl.account
                    SET account_name = :new_name
                    WHERE account_id = :acct_id AND organization_id = :org_id
                """),
                {"new_name": corrected_name, "acct_id": acct_id, "org_id": str(ORG_ID)},
            )
        fixed += 1

    logger.info("  %s: %d typos", "Fixed" if execute else "Would fix", fixed)
    return fixed


def step2_deactivate_test_accounts(db: object, *, execute: bool) -> int:
    """Deactivate test/junk accounts."""
    logger.info("")
    logger.info("Step 2: Deactivate test accounts")
    deactivated = 0

    for acct_id in TEST_ACCOUNT_IDS:
        row = db.execute(
            text("""
                SELECT account_code, account_name, is_active FROM gl.account
                WHERE account_id = :acct_id AND organization_id = :org_id
            """),
            {"acct_id": acct_id, "org_id": str(ORG_ID)},
        ).one_or_none()

        if not row:
            logger.warning("  %s not found", acct_id)
            continue

        if not row[2]:
            logger.info("  %s (%s) — already inactive", row[0], row[1])
            continue

        logger.info("  %s (%s)", row[0], row[1])

        if execute:
            db.execute(
                text("""
                    UPDATE gl.account
                    SET is_active = false
                    WHERE account_id = :acct_id AND organization_id = :org_id
                """),
                {"acct_id": acct_id, "org_id": str(ORG_ID)},
            )
        deactivated += 1

    logger.info(
        "  %s: %d accounts",
        "Deactivated" if execute else "Would deactivate",
        deactivated,
    )
    return deactivated


def step3_deactivate_unused_shadow_accounts(db: object, *, execute: bool) -> int:
    """Deactivate ERPNext shadow accounts with zero activity.

    These are accounts imported from ERPNext with text-based codes,
    all miscategorized under ASSETS, with zero journal entries.
    """
    logger.info("")
    logger.info("Step 3: Deactivate unused ERPNext shadow accounts")

    # Find all active posting accounts that:
    # 1. Have text-based codes (not numeric 1xxx-6xxx pattern)
    # 2. Have zero journal entry lines
    # 3. Belong to this org
    rows = db.execute(
        text("""
            SELECT a.account_id, a.account_code, a.account_name
            FROM gl.account a
            WHERE a.organization_id = :org_id
              AND a.is_active = true
              AND a.account_type = 'POSTING'
              AND a.account_code !~ '^[0-9]'
              AND NOT EXISTS (
                  SELECT 1 FROM gl.journal_entry_line jel
                  WHERE jel.account_id = a.account_id
              )
            ORDER BY a.account_name
        """),
        {"org_id": str(ORG_ID)},
    ).all()

    logger.info("  Found %d unused shadow accounts", len(rows))

    for r in rows:
        logger.info("    %s", r[2])

    if execute and rows:
        ids = [str(r[0]) for r in rows]
        db.execute(
            text("""
                UPDATE gl.account
                SET is_active = false
                WHERE account_id = ANY(:ids)
                  AND organization_id = :org_id
            """),
            {"ids": ids, "org_id": str(ORG_ID)},
        )

    logger.info(
        "  %s: %d accounts", "Deactivated" if execute else "Would deactivate", len(rows)
    )
    return len(rows)


def step4_summary_remaining(db: object) -> None:
    """Report remaining shadow accounts with activity (Phase 2 targets)."""
    logger.info("")
    logger.info("Step 4: Remaining shadow accounts WITH activity (Phase 2)")

    rows = db.execute(
        text("""
            SELECT a.account_id, a.account_code, a.account_name,
                   COALESCE(SUM(jel.debit_amount), 0) - COALESCE(SUM(jel.credit_amount), 0) as net_balance,
                   COUNT(DISTINCT je.journal_entry_id) as journal_count
            FROM gl.account a
            JOIN gl.journal_entry_line jel ON jel.account_id = a.account_id
            JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
                AND je.status = 'POSTED'
            WHERE a.organization_id = :org_id
              AND a.is_active = true
              AND a.account_type = 'POSTING'
              AND a.account_code !~ '^[0-9]'
            GROUP BY a.account_id, a.account_code, a.account_name
            ORDER BY ABS(COALESCE(SUM(jel.debit_amount), 0) - COALESCE(SUM(jel.credit_amount), 0)) DESC
        """),
        {"org_id": str(ORG_ID)},
    ).all()

    total_balance = sum(abs(r[3]) for r in rows)
    logger.info("  %d shadow accounts still active (have journal activity)", len(rows))
    logger.info(f"  Total absolute balance: NGN {total_balance:,.2f}")
    logger.info("")
    for r in rows:
        logger.info("    %-45s  %10d JEs  NGN %15s", r[2][:45], r[4], f"{r[3]:,.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chart of Accounts Phase 1 Cleanup.")
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    execute = args.execute

    with SessionLocal() as db:
        logger.info("=" * 60)
        logger.info("CHART OF ACCOUNTS CLEANUP — PHASE 1")
        logger.info("=" * 60)

        typos_fixed = step1_fix_typos(db, execute=execute)
        tests_deactivated = step2_deactivate_test_accounts(db, execute=execute)
        shadows_deactivated = step3_deactivate_unused_shadow_accounts(
            db, execute=execute
        )

        # Always show Phase 2 summary
        step4_summary_remaining(db)

        logger.info("")
        logger.info("=" * 60)
        logger.info("SUMMARY")
        logger.info("=" * 60)
        logger.info("  Typos fixed:                    %d", typos_fixed)
        logger.info("  Test accounts deactivated:      %d", tests_deactivated)
        logger.info("  Shadow accounts deactivated:    %d", shadows_deactivated)
        logger.info(
            "  Total changes:                  %d",
            typos_fixed + tests_deactivated + shadows_deactivated,
        )

        if execute:
            db.commit()
            logger.info("")
            logger.info("Changes committed.")
        else:
            logger.info("")
            logger.info("DRY RUN — no changes made.")


if __name__ == "__main__":
    main()
