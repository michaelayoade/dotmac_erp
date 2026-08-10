#!/usr/bin/env python
"""
Fix AP subledger/GL gap by:
1. Reversing 21 duplicate AP opening balance journals (₦40.2M)
2. Reclassifying expense module entries from Trade Payables (2000) to Employee Payable (2040)
3. Reclassifying AR entries incorrectly on Trade Payables (2000) to Trade Receivables (1400)

Usage:
  docker exec dotmac_erp_app python scripts/fix_ap_subledger_gap.py
  docker exec dotmac_erp_app python scripts/fix_ap_subledger_gap.py --commit
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text

from app.db import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_USER = UUID("00000000-0000-0000-0000-000000000000")


def run(*, commit: bool = False) -> None:
    with SessionLocal() as db:
        db.execute(text(f"SET app.current_organization_id = '{ORG_ID}'"))

        from app.models.finance.gl.account import Account
        from app.models.finance.gl.journal_entry import (
            JournalEntry,
            JournalStatus,
            JournalType,
        )
        from app.models.finance.gl.journal_entry_line import JournalEntryLine
        from app.models.finance.gl.posted_ledger_line import PostedLedgerLine

        ap_acct = db.scalar(
            select(Account).where(
                Account.account_code == "2000",
                Account.organization_id == ORG_ID,
            )
        )
        emp_payable = db.scalar(
            select(Account).where(
                Account.account_code == "2040",
                Account.organization_id == ORG_ID,
            )
        )
        ar_acct = db.scalar(
            select(Account).where(
                Account.account_code == "1400",
                Account.organization_id == ORG_ID,
            )
        )
        logger.info(
            "AP: %s, EmpPayable: %s, AR: %s",
            ap_acct.account_id,
            emp_payable.account_id,
            ar_acct.account_id,
        )

        # ============================================================
        # FIX 1: Reverse 21 AP OB journals
        # ============================================================
        ob_journals = list(
            db.scalars(
                select(JournalEntry).where(
                    JournalEntry.organization_id == ORG_ID,
                    JournalEntry.source_module.in_(["gl", "GL"]),
                    JournalEntry.source_document_type == "JOURNAL",
                    JournalEntry.status.notin_(
                        [JournalStatus.VOID, JournalStatus.REVERSED]
                    ),
                    JournalEntry.journal_type != JournalType.REVERSAL,
                )
            ).all()
        )

        # Filter to only those with AP credits
        ap_ob_journals: list[tuple[JournalEntry, Decimal]] = []
        for je in ob_journals:
            ledger_lines = list(
                db.scalars(
                    select(PostedLedgerLine).where(
                        PostedLedgerLine.journal_entry_id == je.journal_entry_id,
                        PostedLedgerLine.account_id == ap_acct.account_id,
                    )
                ).all()
            )
            ap_credit = sum(
                pll.credit_amount - pll.debit_amount for pll in ledger_lines
            )
            if ap_credit > 0:
                ap_ob_journals.append((je, ap_credit))

        total_ob = sum(amt for _, amt in ap_ob_journals)
        logger.info(
            "FIX 1: Found %d AP OB journals (total ₦%s)",
            len(ap_ob_journals),
            f"{total_ob:,.2f}",
        )

        reversed_count = 0
        if commit:
            from app.services.finance.gl.reversal import ReversalService

            for je, ap_amt in ap_ob_journals:
                try:
                    result = ReversalService.create_reversal(
                        db=db,
                        organization_id=ORG_ID,
                        original_journal_id=je.journal_entry_id,
                        reversal_date=date.today(),
                        created_by_user_id=SYSTEM_USER,
                        reason="Reverse duplicate AP OB — vendor balance already in imported invoices",
                        auto_post=True,
                    )
                    if result.success:
                        reversed_count += 1
                        logger.info(
                            "  Reversed %s (₦%s)",
                            je.journal_number,
                            f"{ap_amt:,.2f}",
                        )
                    else:
                        logger.warning(
                            "  Failed %s: %s", je.journal_number, result.message
                        )
                except Exception as e:
                    logger.error("  Error %s: %s", je.journal_number, e)
        else:
            for je, ap_amt in ap_ob_journals:
                logger.info(
                    "  Would reverse %s (₦%s)", je.journal_number, f"{ap_amt:,.2f}"
                )

        logger.info("FIX 1: %d/%d reversed", reversed_count, len(ap_ob_journals))

        # ============================================================
        # FIX 2: Reclassify expense lines from 2000 → 2040
        # ============================================================
        expense_lines = list(
            db.scalars(
                select(PostedLedgerLine).where(
                    PostedLedgerLine.source_module == "expense",
                    PostedLedgerLine.account_id == ap_acct.account_id,
                )
            ).all()
        )
        exp_net = sum(pll.credit_amount - pll.debit_amount for pll in expense_lines)
        logger.info(
            "FIX 2: %d expense lines on AP (net ₦%s) → reclassify to 2040",
            len(expense_lines),
            f"{exp_net:,.2f}",
        )

        if commit:
            for pll in expense_lines:
                pll.account_id = emp_payable.account_id
                pll.account_code = emp_payable.account_code

            # Also fix journal_entry_line records
            exp_je_ids = set(pll.journal_entry_id for pll in expense_lines)
            jel_fixed = 0
            for je_id in exp_je_ids:
                for jel in db.scalars(
                    select(JournalEntryLine).where(
                        JournalEntryLine.journal_entry_id == je_id,
                        JournalEntryLine.account_id == ap_acct.account_id,
                    )
                ).all():
                    jel.account_id = emp_payable.account_id
                    jel_fixed += 1
            logger.info(
                "  Fixed %d posted_ledger + %d journal_entry_line",
                len(expense_lines),
                jel_fixed,
            )

        # ============================================================
        # FIX 3: Reclassify AR lines from 2000 → 1400
        # ============================================================
        ar_lines = list(
            db.scalars(
                select(PostedLedgerLine).where(
                    PostedLedgerLine.source_module.in_(["ar", "AR"]),
                    PostedLedgerLine.account_id == ap_acct.account_id,
                )
            ).all()
        )
        ar_net = sum(pll.credit_amount - pll.debit_amount for pll in ar_lines)
        logger.info(
            "FIX 3: %d AR lines on AP (net ₦%s) → reclassify to 1400",
            len(ar_lines),
            f"{ar_net:,.2f}",
        )

        if commit:
            for pll in ar_lines:
                pll.account_id = ar_acct.account_id
                pll.account_code = ar_acct.account_code

            ar_je_ids = set(pll.journal_entry_id for pll in ar_lines)
            ar_jel_fixed = 0
            for je_id in ar_je_ids:
                for jel in db.scalars(
                    select(JournalEntryLine).where(
                        JournalEntryLine.journal_entry_id == je_id,
                        JournalEntryLine.account_id == ap_acct.account_id,
                    )
                ).all():
                    jel.account_id = ar_acct.account_id
                    ar_jel_fixed += 1
            logger.info(
                "  Fixed %d posted_ledger + %d journal_entry_line",
                len(ar_lines),
                ar_jel_fixed,
            )

        if commit:
            db.commit()
            logger.info("All committed")
        else:
            logger.info("DRY RUN — run with --commit to apply")

        # Verify
        ap_gl = db.execute(
            text(
                "SELECT ROUND(SUM(pll.credit_amount - pll.debit_amount)::numeric, 2) "
                "FROM gl.posted_ledger_line pll "
                "JOIN gl.account a ON a.account_id = pll.account_id "
                "WHERE a.subledger_type = 'AP'"
            )
        ).scalar()
        ap_sub = db.execute(
            text(
                "SELECT ROUND(SUM(total_amount - COALESCE(amount_paid, 0))::numeric, 2) "
                "FROM ap.supplier_invoice WHERE status NOT IN ('VOID','DRAFT','SUBMITTED')"
            )
        ).scalar()
        tb = db.execute(
            text(
                "SELECT ROUND((SUM(debit_amount) - SUM(credit_amount))::numeric, 2) FROM gl.posted_ledger_line"
            )
        ).scalar()
        logger.info(
            "VERIFY: AP GL=%s, AP Sub=%s, Gap=%s, TB=%s",
            ap_gl,
            ap_sub,
            float(ap_gl) - float(ap_sub),
            tb,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix AP subledger/GL gap")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    run(commit=args.commit)


if __name__ == "__main__":
    main()
