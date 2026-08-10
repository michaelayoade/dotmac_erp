"""
Reclassify AR invoice VAT from 2000 (Trade Payables) to 2120 (VAT Payables).

Background:
The VAT-7.5 tax code had tax_collected_account_id pointing to account 2000
(Trade Payables). This caused 18,951 AR invoice journals to credit Trade
Payables for NGN 107.9M instead of VAT Payables (2120). This script creates
a single reclassification journal entry to correct the balance.

Usage:
    python scripts/reclassify_vat_to_2120.py --dry-run
    python scripts/reclassify_vat_to_2120.py --execute
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
from app.services.finance.gl.journal import (  # noqa: E402
    JournalInput,
    JournalLineInput,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reclassify_vat")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
# Two different admin users required for SoD (creator != approver)
CREATOR_USER_ID = UUID("ef36328f-2343-4649-afa0-ab1bfd4ec6f0")  # Michael Ayoade
APPROVER_USER_ID = UUID("2364e957-d6b0-4702-9b35-2e314cd1d22c")  # Shade Ayoade
ACCT_2000_TRADE_PAYABLES = UUID("d6fcaecf-e1b7-4dce-9743-368eb5b1775c")
ACCT_2120_VAT_PAYABLES = UUID("f46dd075-1c51-4cb3-8033-41c89735e438")
REFERENCE = "RECLASS-VAT-2000-TO-2120"


def get_ar_vat_on_2000(db: object) -> Decimal:
    """Get the total AR invoice VAT amount misposted to account 2000."""
    row = db.execute(
        text("""
            SELECT
                COALESCE(SUM(jel.credit_amount), 0) AS total_credit,
                COALESCE(SUM(jel.debit_amount), 0) AS total_debit
            FROM gl.journal_entry_line jel
            JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
            JOIN gl.account a ON a.account_id = jel.account_id
            WHERE a.account_code = '2000'
              AND je.status = 'POSTED'
              AND je.organization_id = :org_id
              AND je.source_module = 'AR'
              AND je.source_document_type = 'INVOICE'
        """),
        {"org_id": str(ORG_ID)},
    ).one()
    return Decimal(str(row[0])) - Decimal(str(row[1]))


def check_existing(db: object) -> bool:
    """Check if reclassification already exists."""
    result = db.execute(
        text("""
            SELECT COUNT(*) FROM gl.journal_entry
            WHERE organization_id = :org_id
              AND reference = :ref
              AND status = 'POSTED'
        """),
        {"org_id": str(ORG_ID), "ref": REFERENCE},
    ).scalar()
    return (result or 0) > 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reclassify AR invoice VAT from 2000 to 2120."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument(
        "--execute", action="store_true", help="Create reclassification JE"
    )
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Specify --dry-run or --execute")

    with SessionLocal() as db:
        amount = get_ar_vat_on_2000(db)

        logger.info("=" * 60)
        logger.info("VAT RECLASSIFICATION: 2000 (Trade Payables) → 2120 (VAT Payables)")
        logger.info("=" * 60)
        logger.info("  AR invoice VAT misposted to 2000: NGN %s", f"{amount:,.2f}")
        logger.info("  Target account: 2120 (VAT Payables)")
        logger.info("=" * 60)

        if amount <= 0:
            logger.info("Nothing to reclassify (amount <= 0).")
            return

        if args.dry_run:
            logger.info("DRY RUN — no changes made.")
            logger.info("")
            logger.info("Proposed journal entry:")
            logger.info("  DR 2000 Trade Payables:    NGN %s", f"{amount:,.2f}")
            logger.info("  CR 2120 VAT Payables:      NGN %s", f"{amount:,.2f}")
            return

        if check_existing(db):
            logger.info("Reclassification (%s) already exists. Skipping.", REFERENCE)
            return

        logger.info("Creating reclassification journal entry...")

        journal_input = JournalInput(
            journal_type=JournalType.ADJUSTMENT,
            entry_date=date.today(),
            posting_date=date.today(),
            description=(
                "Reclassify AR invoice VAT from 2000 (Trade Payables) to "
                "2120 (VAT Payables) — corrects VAT-7.5 tax code misconfiguration"
            ),
            reference=REFERENCE,
            currency_code="NGN",
            lines=[
                JournalLineInput(
                    account_id=ACCT_2000_TRADE_PAYABLES,
                    debit_amount=amount,
                    credit_amount=Decimal("0"),
                    description="Reclassify AR invoice VAT out of Trade Payables",
                ),
                JournalLineInput(
                    account_id=ACCT_2120_VAT_PAYABLES,
                    debit_amount=Decimal("0"),
                    credit_amount=amount,
                    description="Reclassify AR invoice VAT to VAT Payables",
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
            db.commit()
            logger.info(
                "SUCCESS: Journal %s posted (ID: %s)",
                result.entry_number,
                result.journal_entry_id,
            )
            logger.info("  DR 2000 Trade Payables:  NGN %s", f"{amount:,.2f}")
            logger.info("  CR 2120 VAT Payables:    NGN %s", f"{amount:,.2f}")
        else:
            logger.error("FAILED: %s", result.message)
            db.rollback()
            sys.exit(1)


if __name__ == "__main__":
    main()
