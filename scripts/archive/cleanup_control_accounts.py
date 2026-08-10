"""
Chart of Accounts Cleanup — Phase 4: Merge CONTROL accounts into POSTING accounts.

ERPNext imported structural CONTROL accounts with journal entries attached.
This script moves balances from CONTROL → POSTING counterparts,
then deactivates all CONTROL accounts.

Usage:
    python scripts/cleanup_control_accounts.py --dry-run
    python scripts/cleanup_control_accounts.py --execute
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
logger = logging.getLogger("cleanup_control")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
CREATOR_USER_ID = UUID("ef36328f-2343-4649-afa0-ab1bfd4ec6f0")  # Michael Ayoade
APPROVER_USER_ID = UUID("2364e957-d6b0-4702-9b35-2e314cd1d22c")  # Shade Ayoade

# ── CONTROL account NAME → POSTING account CODE mapping ──
CONTROL_MAP: dict[str, str] = {
    # Bank CONTROL → POSTING counterparts
    "UBA Bank": "1202",  # UBA
    "Zenith 523 Bank": "1200",  # Zenith Bank (main)
    "Zenith 461 Bank": "1200",  # Zenith Bank (main)
    "Zenith 454 Bank": "1200",  # Zenith Bank (main)
    "Zenith USD": "1200",  # Zenith Bank (main, USD sub)
    "Paystack": "1210",  # Paystack
    "Paystack OPEX": "1211",  # Paystack OPEX Account
    "Cash CBD": "1220",  # Cash at Hand
    "Cash Garki": "1220",  # Cash at Hand
    "Cash Lagos": "1220",  # Cash at Hand
    # Receivables/Payables CONTROL → POSTING
    "Accounts Receivable": "1400",  # Trade Receivables
    "Trade and Other Payables (USD)": "2000",  # Trade Payables
    "Expense Payable": "2020",  # Accrued Expenses
    "Employee Reimbursements": "2030",  # Employee Reimbursables
    # Tax CONTROL → POSTING
    "Current Tax Payable": "2100",  # Income Tax
    "Pension Payables": "2130",  # Pension
    "NHF Payables": "2132",  # NHF Payables
    "PAYE Payables": "2110",  # WHT (closest match for PAYE deductions)
    # These CONTROL accounts can just be deactivated (no clear POSTING target):
    # Duties and Taxes → deactivate (structural)
    # Tax Deducted at Source → deactivate
    # Fixed Asset Account → deactivate
    # Office Equipment (CONTROL) → deactivate
    # Plant and Machinery (CONTROL) → deactivate
    # Vehicle (CONTROL) → deactivate
    # Inventory / Inventory Asset / Finished Goods / WIP → deactivate
    # Equity / Income / Expenses / Application of Funds / Source of Funds → deactivate
    # Petty Cash / Undeposited Funds → deactivate
    # Fuel card accounts → deactivate
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 4: Merge CONTROL accounts into POSTING accounts."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    execute = args.execute

    with SessionLocal() as db:
        logger.info("=" * 60)
        logger.info("CHART OF ACCOUNTS CLEANUP — PHASE 4")
        logger.info("Merge CONTROL accounts into POSTING accounts")
        logger.info("=" * 60)

        # ── Get all active CONTROL accounts ──
        control_rows = db.execute(
            text("""
                SELECT a.account_id, a.account_code, a.account_name,
                       COALESCE(b.total_debit, 0) as total_debit,
                       COALESCE(b.total_credit, 0) as total_credit,
                       COALESCE(b.total_debit, 0) - COALESCE(b.total_credit, 0) as net_balance,
                       COALESCE(b.journal_count, 0) as journal_count
                FROM gl.account a
                LEFT JOIN LATERAL (
                    SELECT SUM(jel.debit_amount) as total_debit,
                           SUM(jel.credit_amount) as total_credit,
                           COUNT(DISTINCT je.journal_entry_id) as journal_count
                    FROM gl.journal_entry_line jel
                    JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
                        AND je.status = 'POSTED'
                    WHERE jel.account_id = a.account_id
                ) b ON true
                WHERE a.organization_id = :org_id
                  AND a.is_active = true
                  AND a.account_type = 'CONTROL'
                ORDER BY ABS(COALESCE(b.total_debit, 0) - COALESCE(b.total_credit, 0)) DESC
            """),
            {"org_id": str(ORG_ID)},
        ).all()

        logger.info("  Found %d active CONTROL accounts", len(control_rows))

        # ── Get canonical POSTING accounts by code ──
        canonical_rows = db.execute(
            text("""
                SELECT account_id, account_code, account_name
                FROM gl.account
                WHERE organization_id = :org_id
                  AND is_active = true
                  AND account_type = 'POSTING'
                  AND account_code ~ '^[0-9]'
            """),
            {"org_id": str(ORG_ID)},
        ).all()

        canonicals = {
            r[1]: {"id": str(r[0]), "code": r[1], "name": r[2]} for r in canonical_rows
        }

        # ── Categorize CONTROL accounts ──
        to_merge: list[tuple[dict, dict]] = []
        zero_balance: list[dict] = []
        just_deactivate: list[dict] = []

        for r in control_rows:
            info = {
                "id": str(r[0]),
                "code": r[1],
                "name": r[2],
                "debit": Decimal(str(r[3])),
                "credit": Decimal(str(r[4])),
                "net": Decimal(str(r[5])),
                "journals": int(r[6]),
            }

            target_code = CONTROL_MAP.get(info["name"])

            if target_code:
                canonical = canonicals.get(target_code)
                if canonical:
                    if abs(info["net"]) < Decimal("0.01"):
                        zero_balance.append(info)
                    else:
                        to_merge.append((info, canonical))
                else:
                    logger.warning(
                        "  Target POSTING account %s not found for '%s'",
                        target_code,
                        info["name"],
                    )
                    just_deactivate.append(info)
            else:
                just_deactivate.append(info)

        # ── Step 1: Deactivate CONTROL accounts with no merge target ──
        logger.info("")
        logger.info(
            "Step 1: Deactivate CONTROL accounts (no merge needed) (%d)",
            len(just_deactivate) + len(zero_balance),
        )

        for info in just_deactivate:
            if info["journals"] > 0:
                logger.info(
                    "  %-40s  %5d JEs  net: NGN %s  (no POSTING target — deactivate only)",
                    info["name"][:40],
                    info["journals"],
                    f"{info['net']:,.2f}",
                )
            else:
                logger.info("  %-40s  (no activity)", info["name"][:40])

        for info in zero_balance:
            logger.info(
                "  %-40s  %5d JEs  (zero balance)",
                info["name"][:40],
                info["journals"],
            )

        if execute:
            all_deactivate = just_deactivate + zero_balance
            if all_deactivate:
                ids = [info["id"] for info in all_deactivate]
                db.execute(
                    text("""
                        UPDATE gl.account
                        SET is_active = false
                        WHERE account_id = ANY(:ids)
                          AND organization_id = :org_id
                    """),
                    {"ids": ids, "org_id": str(ORG_ID)},
                )

        # ── Step 2: Reclassify and deactivate CONTROL accounts with balances ──
        logger.info("")
        logger.info("Step 2: Reclassify CONTROL accounts (%d)", len(to_merge))

        posted = 0
        errors = 0
        total_reclassified = Decimal("0")

        for info, canonical in to_merge:
            net = info["net"]
            abs_net = abs(net)

            if net > 0:
                shadow_debit = Decimal("0")
                shadow_credit = abs_net
                canonical_debit = abs_net
                canonical_credit = Decimal("0")
            else:
                shadow_debit = abs_net
                shadow_credit = Decimal("0")
                canonical_debit = Decimal("0")
                canonical_credit = abs_net

            logger.info(
                "  %-40s  NGN %15s  → %s %s",
                info["name"][:40],
                f"{net:,.2f}",
                canonical["code"],
                canonical["name"],
            )

            if not execute:
                continue

            try:
                journal_input = JournalInput(
                    journal_type=JournalType.ADJUSTMENT,
                    entry_date=date.today(),
                    posting_date=date.today(),
                    description=(
                        f"COA cleanup: merge CONTROL '{info['name']}' into "
                        f"'{canonical['code']} {canonical['name']}'"
                    ),
                    reference=f"MERGE-CTRL-{info['code'][:20]}",
                    currency_code="NGN",
                    lines=[
                        JournalLineInput(
                            account_id=UUID(info["id"]),
                            debit_amount=shadow_debit,
                            credit_amount=shadow_credit,
                            description=f"Zero out CONTROL: {info['name']}",
                        ),
                        JournalLineInput(
                            account_id=UUID(canonical["id"]),
                            debit_amount=canonical_debit,
                            credit_amount=canonical_credit,
                            description=f"Absorb from CONTROL: {info['name']}",
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
                    posted += 1
                    total_reclassified += abs_net

                    db.execute(
                        text("""
                            UPDATE gl.account
                            SET is_active = false
                            WHERE account_id = :id AND organization_id = :org_id
                        """),
                        {"id": info["id"], "org_id": str(ORG_ID)},
                    )
                    logger.info("    Posted %s", result.entry_number)
                else:
                    logger.error("    FAILED: %s", result.message)
                    errors += 1
            except Exception as e:
                logger.exception("    ERROR merging %s: %s", info["name"], e)
                errors += 1

        # ── Step 3: Deactivate STATISTICAL test account ──
        logger.info("")
        logger.info("Step 3: Deactivate STATISTICAL test account")
        test_result = db.execute(
            text("""
                SELECT account_id, account_code, account_name
                FROM gl.account
                WHERE organization_id = :org_id
                  AND is_active = true
                  AND account_type = 'STATISTICAL'
            """),
            {"org_id": str(ORG_ID)},
        ).all()

        for r in test_result:
            logger.info("  %s (%s)", r[1], r[2])
            if execute:
                db.execute(
                    text("""
                        UPDATE gl.account
                        SET is_active = false
                        WHERE account_id = :id AND organization_id = :org_id
                    """),
                    {"id": str(r[0]), "org_id": str(ORG_ID)},
                )

        # ── Summary ──
        logger.info("")
        logger.info("=" * 60)
        logger.info("SUMMARY")
        logger.info("=" * 60)
        logger.info(
            "  CONTROL accounts deactivated (no merge):  %d",
            len(just_deactivate) + len(zero_balance),
        )
        logger.info(
            "  CONTROL accounts reclassified:            %d posted, %d errors",
            posted,
            errors,
        )
        logger.info("  STATISTICAL accounts deactivated:         %d", len(test_result))
        logger.info(
            "  Total reclassified:                       NGN %s",
            f"{total_reclassified:,.2f}",
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
