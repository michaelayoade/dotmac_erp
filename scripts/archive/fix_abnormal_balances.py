"""
Fix abnormal account balances.

Fix 1: Pension 2130 — Reverse wrong-sign opening balances.
    OB-2022/2023/2024 posted DEBITS of 11,875,594.37 to a credit-normal liability.
    Should have been credits. Correction: CR 2130 23,751,188.74 / DR 3100 23,751,188.74.

Fix 2: Paystack netting — Net 1210 into 1211.
    1210 has -1,415,660,749.84 (settlement credits), 1211 has +1,569,607,853.68 (collection debits).
    Combined net = +153,947,103.84. Fix: DR 1210 / CR 1211 to zero out 1210.

Usage:
    python scripts/fix_abnormal_balances.py --dry-run
    python scripts/fix_abnormal_balances.py --execute
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
logger = logging.getLogger("fix_abnormal")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
CREATOR_USER_ID = UUID("ef36328f-2343-4649-afa0-ab1bfd4ec6f0")
APPROVER_USER_ID = UUID("2364e957-d6b0-4702-9b35-2e314cd1d22c")


def get_account_id(db: object, code: str) -> str | None:
    """Look up account_id by numeric code."""
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


def get_balance(db: object, code: str) -> Decimal:
    """Get current net balance for an account."""
    row = db.execute(
        text("""
            SELECT COALESCE(SUM(jel.debit_amount), 0) - COALESCE(SUM(jel.credit_amount), 0)
            FROM gl.journal_entry_line jel
            JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
                AND je.status = 'POSTED'
            JOIN gl.account a ON a.account_id = jel.account_id
            WHERE a.account_code = :code
              AND a.organization_id = :org_id
        """),
        {"code": code, "org_id": str(ORG_ID)},
    ).one()
    return Decimal(str(row[0]))


def fix_pension(db: object, *, execute: bool) -> bool:
    """Fix Pension 2130 wrong-sign opening balances."""
    logger.info("Fix 1: Pension 2130 — wrong-sign opening balances")

    pension_balance = get_balance(db, "2130")
    logger.info(
        "  Current 2130 balance: NGN %s (should be credit/negative)",
        f"{pension_balance:,.2f}",
    )

    if pension_balance <= 0:
        logger.info("  Balance is already credit — no fix needed.")
        return True

    # The wrong-sign OBs debited 11,875,594.37. To flip them to credits,
    # we need to credit 2x that amount = 23,751,188.74.
    # But the current balance also includes correct credits (1,677,615.34).
    # So the fix amount = current_debit_balance + what the correct credit balance should be.
    #
    # Actually, the simplest approach: the total wrong debits are 11,875,594.37.
    # To reverse them AND re-post as credits: correction = 2 × 11,875,594.37.
    # After correction, balance = 10,197,979.03 - 23,751,188.74 = -13,553,209.71 (credit).

    wrong_ob_debits = Decimal("11875594.37")  # OB-2022 + OB-2023 + OB-2024
    correction_amount = wrong_ob_debits * 2

    logger.info("  Wrong OB debits: NGN %s", f"{wrong_ob_debits:,.2f}")
    logger.info("  Correction (2x): NGN %s", f"{correction_amount:,.2f}")
    logger.info(
        "  Post-fix balance: NGN %s", f"{pension_balance - correction_amount:,.2f}"
    )

    if not execute:
        return True

    pension_id = get_account_id(db, "2130")
    retained_id = get_account_id(db, "3100")

    if not pension_id or not retained_id:
        logger.error("  Account lookup failed!")
        return False

    journal_input = JournalInput(
        journal_type=JournalType.ADJUSTMENT,
        entry_date=date.today(),
        posting_date=date.today(),
        description=(
            "Fix wrong-sign pension opening balances (OB-2022/2023/2024 "
            "were posted as debits instead of credits)"
        ),
        reference="FIX-PENSION-OB-SIGN",
        currency_code="NGN",
        lines=[
            JournalLineInput(
                account_id=UUID(retained_id),
                debit_amount=correction_amount,
                credit_amount=Decimal("0"),
                description="Offset pension OB sign correction",
            ),
            JournalLineInput(
                account_id=UUID(pension_id),
                debit_amount=Decimal("0"),
                credit_amount=correction_amount,
                description="Reverse wrong-sign OB debits and post correct credits",
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
        return True
    else:
        logger.error("  FAILED: %s", result.message)
        return False


def fix_paystack_netting(db: object, *, execute: bool) -> bool:
    """Net Paystack 1210 into 1211."""
    logger.info("")
    logger.info("Fix 2: Net Paystack 1210 → 1211")

    balance_1210 = get_balance(db, "1210")
    balance_1211 = get_balance(db, "1211")
    combined = balance_1210 + balance_1211

    logger.info("  1210 Paystack balance:      NGN %s", f"{balance_1210:,.2f}")
    logger.info("  1211 Paystack OPEX balance:  NGN %s", f"{balance_1211:,.2f}")
    logger.info("  Combined net:                NGN %s", f"{combined:,.2f}")

    if balance_1210 >= 0:
        logger.info("  1210 already has debit balance — no netting needed.")
        return True

    # DR 1210 to zero it out, CR 1211 by the same amount
    netting_amount = abs(balance_1210)

    logger.info("  Netting amount:              NGN %s", f"{netting_amount:,.2f}")
    logger.info("  Post-fix 1210 balance:       NGN 0.00")
    logger.info(
        "  Post-fix 1211 balance:       NGN %s", f"{balance_1211 - netting_amount:,.2f}"
    )

    if not execute:
        return True

    id_1210 = get_account_id(db, "1210")
    id_1211 = get_account_id(db, "1211")

    if not id_1210 or not id_1211:
        logger.error("  Account lookup failed!")
        return False

    journal_input = JournalInput(
        journal_type=JournalType.ADJUSTMENT,
        entry_date=date.today(),
        posting_date=date.today(),
        description=(
            "Net Paystack clearing accounts: move accumulated settlement "
            "credits from 1210 to 1211 (both are Paystack wallets, credits "
            "were posted to 1210 but customer receipts went to 1211)"
        ),
        reference="FIX-PAYSTACK-NET",
        currency_code="NGN",
        lines=[
            JournalLineInput(
                account_id=UUID(id_1210),
                debit_amount=netting_amount,
                credit_amount=Decimal("0"),
                description="Zero out 1210 Paystack (settlement credits absorbed by 1211)",
            ),
            JournalLineInput(
                account_id=UUID(id_1211),
                debit_amount=Decimal("0"),
                credit_amount=netting_amount,
                description="Absorb 1210 settlement credits into 1211 Paystack OPEX",
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
        return True
    else:
        logger.error("  FAILED: %s", result.message)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix abnormal account balances.")
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    execute = args.execute

    with SessionLocal() as db:
        logger.info("=" * 60)
        logger.info("FIX ABNORMAL BALANCES")
        logger.info("=" * 60)

        ok1 = fix_pension(db, execute=execute)
        ok2 = fix_paystack_netting(db, execute=execute)

        logger.info("")
        logger.info("=" * 60)
        logger.info("REMAINING: Trade Receivables 1400")
        logger.info("=" * 60)
        balance_1400 = get_balance(db, "1400")
        logger.info("  1400 balance: NGN %s", f"{balance_1400:,.2f}")
        logger.info("  Root cause: Splynx sync credits exceed debits since Jan 2025")
        logger.info("  Action: Investigate sync service — NOT a journal fix")

        if execute and (ok1 or ok2):
            db.commit()
            logger.info("")
            logger.info("Changes committed.")
        elif not execute:
            logger.info("")
            logger.info("DRY RUN — no changes made.")
        else:
            logger.info("")
            logger.info("All fixes failed — no commit.")


if __name__ == "__main__":
    main()
