"""
Chart of Accounts Cleanup — Phase 2: Merge ERPNext shadow accounts.

For each shadow account with journal activity, this script:
1. Creates a reclassification journal (DR shadow / CR canonical or vice versa)
   to zero out the shadow account balance
2. Deactivates the shadow account

For shadow accounts with zero net balance (debits = credits already cancel),
we simply deactivate them (no journal needed).

Usage:
    python scripts/merge_shadow_accounts.py --dry-run
    python scripts/merge_shadow_accounts.py --execute
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
logger = logging.getLogger("merge_shadow_accounts")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
CREATOR_USER_ID = UUID("ef36328f-2343-4649-afa0-ab1bfd4ec6f0")  # Michael Ayoade
APPROVER_USER_ID = UUID("2364e957-d6b0-4702-9b35-2e314cd1d22c")  # Shade Ayoade

# ── Shadow account NAME → canonical account CODE mapping ──
# Shadow name (exact match) → canonical numeric account code
MERGE_MAP: dict[str, str] = {
    # Expense shadows → proper 6xxx expense accounts
    "Bank Fees and Charges": "6080",  # Finance Cost
    "Subscriptions and Renewals": "6012",  # Subscription & Renewal
    "Government Tender Fees": "6063",  # Contract Tender Fees
    "Car Repairs and Maintenance": "6053",  # Motor Vehicle Repairs & Maintenance
    "Staff training": "6003",  # Staff Training
    "Training": "6003",  # Staff Training
    "Fuel/Mileage Expenses": "6024",  # Fuel & Lubricant
    "fueling": "6024",  # Fuel & Lubricant
    "Transportation Expense": "6081",  # Transportation & Travelling
    "Travel Expense": "6081",  # Transportation & Travelling
    "Office Repairs and Maintenance": "6050",  # Office Repairs & Maintenance
    "Repairs and Maintenance": "6050",  # Office Repairs & Maintenance
    "Utilities": "6022",  # Utilities
    "Medical Expenses": "6013",  # Medical Expenses
    "Subcontractors - COS": "6004",  # Contract Labour & Logistics
    "Direct labour - COS": "6004",  # Contract Labour & Logistics
    "Direct Labour Project": "6004",  # Contract Labour & Logistics
    "Staff Welfare": "6099",  # Other Expenses
    "Site Logistics": "6082",  # Shipping & Delivery
    "Shipping and delivery expense": "6082",  # Shipping & Delivery
    "Salaries": "6000",  # Staff Salaries & Wage
    "Stationery and printing - DT": "6020",  # Printing & stationery
    "Income tax expense": "6092",  # Tax Audit Expense
    "Stamp duty Paid": "6010",  # Statutory Expenses
    # Cost shadows → proper 5xxx COGS accounts
    "Cost of Goods Sold": "5000",  # Purchases
    "Purchases": "5000",  # Purchases
    "Materials - COS - DT": "5000",  # Purchases
    "Cost of sales": "5000",  # Purchases
    "Bandwidth and Interconnect": "5030",  # Purchase of Bandwidth
    # Liability/asset shadows → proper accounts
    "Withholding Tax": "1420",  # Withholding Taxes (asset)
    "Withholding Tax Liabilities": "2110",  # WHT (liability)
    "Short term loan": "2500",  # Long Term Borrowings
    # Payroll shadows
    "Paye Expense": "6001",  # PAYE expenses
    "Salaries": "6000",  # Staff Salaries
    # Operations shadows
    "Base Station Repairs and Maintenance": "6064",
    "Security and guards": "6011",  # Security Expenses
    "Telephone Expense": "6023",  # Telephone bills
    # Special cases
    "Opening Balance Adjustments": "3100",  # Retained Earnings
    "Retained Earnings": "3100",  # Retained Earnings
    "Internet Sales": "4000",  # Internet Revenue
    "Sales Without Invoice": "4010",  # Other Business Revenue
    "Staff Loan": "1410",  # Staff Loan (proper)
}


def lookup_accounts(db: object) -> tuple[dict[str, dict], dict[str, dict]]:
    """Look up shadow accounts (text codes) and canonical accounts (numeric codes).

    Returns:
        (shadow_accounts, canonical_accounts)
        Each is a dict keyed by name or code, containing account details.
    """
    # Shadow accounts: text-based codes, active, with journal activity
    shadow_rows = db.execute(
        text("""
            SELECT a.account_id, a.account_code, a.account_name,
                   COALESCE(SUM(jel.debit_amount), 0) as total_debit,
                   COALESCE(SUM(jel.credit_amount), 0) as total_credit,
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
        """),
        {"org_id": str(ORG_ID)},
    ).all()

    shadows: dict[str, dict] = {}
    for r in shadow_rows:
        shadows[r[2]] = {  # keyed by account_name
            "id": str(r[0]),
            "code": r[1],
            "name": r[2],
            "debit": Decimal(str(r[3])),
            "credit": Decimal(str(r[4])),
            "net": Decimal(str(r[3])) - Decimal(str(r[4])),
            "journals": r[5],
        }

    # Canonical accounts: numeric codes
    canonical_rows = db.execute(
        text("""
            SELECT a.account_id, a.account_code, a.account_name
            FROM gl.account a
            WHERE a.organization_id = :org_id
              AND a.is_active = true
              AND a.account_code ~ '^[0-9]'
        """),
        {"org_id": str(ORG_ID)},
    ).all()

    canonicals: dict[str, dict] = {}
    for r in canonical_rows:
        canonicals[r[1]] = {  # keyed by account_code
            "id": str(r[0]),
            "code": r[1],
            "name": r[2],
        }

    return shadows, canonicals


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 2: Merge ERPNext shadow accounts."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    execute = args.execute

    with SessionLocal() as db:
        logger.info("=" * 60)
        logger.info("CHART OF ACCOUNTS CLEANUP — PHASE 2")
        logger.info("Merge shadow accounts into canonical accounts")
        logger.info("=" * 60)

        shadows, canonicals = lookup_accounts(db)
        logger.info("  Found %d active shadow accounts with activity", len(shadows))
        logger.info("  Found %d canonical accounts", len(canonicals))

        # Build merge plan
        zero_balance: list[tuple[dict, dict]] = []
        needs_reclass: list[tuple[dict, dict]] = []
        unmapped: list[dict] = []

        for shadow_name, info in shadows.items():
            target_code = MERGE_MAP.get(shadow_name)
            if not target_code:
                unmapped.append(info)
                continue

            canonical = canonicals.get(target_code)
            if not canonical:
                logger.warning(
                    "  Target account %s not found for shadow '%s'",
                    target_code,
                    shadow_name,
                )
                continue

            if abs(info["net"]) < Decimal("0.01"):
                zero_balance.append((info, canonical))
            else:
                needs_reclass.append((info, canonical))

        if unmapped:
            logger.info("")
            logger.info("  %d shadow accounts NOT in merge map:", len(unmapped))
            for info in sorted(unmapped, key=lambda x: abs(x["net"]), reverse=True):
                logger.info(
                    "    %-40s  %5d JEs  net: NGN %s",
                    info["name"][:40],
                    info["journals"],
                    f"{info['net']:,.2f}",
                )

        # ── Step 1: Deactivate zero-balance shadow accounts ──
        logger.info("")
        logger.info(
            "Step 1: Deactivate zero-balance shadow accounts (%d)",
            len(zero_balance),
        )

        for info, canonical in zero_balance:
            logger.info(
                "  %-40s  %5d JEs  (net: 0)  → %s %s",
                info["name"][:40],
                info["journals"],
                canonical["code"],
                canonical["name"],
            )

            if execute:
                db.execute(
                    text("""
                        UPDATE gl.account
                        SET is_active = false
                        WHERE account_id = :id AND organization_id = :org_id
                    """),
                    {"id": info["id"], "org_id": str(ORG_ID)},
                )

        # ── Step 2: Reclassify and deactivate non-zero shadow accounts ──
        logger.info("")
        logger.info(
            "Step 2: Reclassify non-zero shadow accounts (%d)",
            len(needs_reclass),
        )

        posted = 0
        errors = 0
        total_reclassified = Decimal("0")

        for info, canonical in sorted(
            needs_reclass, key=lambda x: abs(x[0]["net"]), reverse=True
        ):
            net = info["net"]
            abs_net = abs(net)

            # Determine journal direction:
            # If shadow has debit balance (net > 0):
            #   CR shadow (reduce it) / DR canonical (increase it)
            # If shadow has credit balance (net < 0):
            #   DR shadow (reduce it) / CR canonical (increase it)

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
                        f"COA cleanup: merge '{info['name']}' into "
                        f"'{canonical['code']} {canonical['name']}'"
                    ),
                    reference=f"MERGE-COA-{info['code'][:20]}",
                    currency_code="NGN",
                    lines=[
                        JournalLineInput(
                            account_id=UUID(info["id"]),
                            debit_amount=shadow_debit,
                            credit_amount=shadow_credit,
                            description=f"Zero out shadow: {info['name']}",
                        ),
                        JournalLineInput(
                            account_id=UUID(canonical["id"]),
                            debit_amount=canonical_debit,
                            credit_amount=canonical_credit,
                            description=f"Absorb from: {info['name']}",
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

                    # Deactivate shadow account
                    db.execute(
                        text("""
                            UPDATE gl.account
                            SET is_active = false
                            WHERE account_id = :id AND organization_id = :org_id
                        """),
                        {"id": info["id"], "org_id": str(ORG_ID)},
                    )
                    logger.info("    ✓ Posted %s", result.entry_number)
                else:
                    logger.error("    FAILED: %s", result.message)
                    errors += 1
            except Exception as e:
                logger.exception("    ERROR merging %s: %s", info["name"], e)
                errors += 1

        # ── Summary ──
        logger.info("")
        logger.info("=" * 60)
        logger.info("SUMMARY")
        logger.info("=" * 60)
        logger.info("  Zero-balance deactivated:     %d", len(zero_balance))
        logger.info(
            "  Reclassification JEs:         %d posted, %d errors", posted, errors
        )
        logger.info(
            "  Total amount reclassified:    NGN %s",
            f"{total_reclassified:,.2f}",
        )
        if unmapped:
            logger.info("  Unmapped (need manual review): %d", len(unmapped))

        if execute:
            db.commit()
            logger.info("")
            logger.info("Changes committed.")
        else:
            logger.info("")
            logger.info("DRY RUN — no changes made.")


if __name__ == "__main__":
    main()
