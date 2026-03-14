#!/usr/bin/env python
"""
Reclassify misposted expense GL journals.

Fixes expense claims where GL journal debited 6099 (Other Expenses) instead
of the correct account from the expense category mapping.

Root cause: RLS on expense_category table caused db.get() to return None in
Celery task sessions, falling through to the org default (6099).

For each misposted claim, creates a reclassification journal:
  - Credit 6099 (Other Expenses) for the misposted amount
  - Debit the correct expense account per category mapping

Idempotent: re-running produces zero additional changes (checks for existing
reclassification journals by correlation_id).

Usage:
  # Dry run (default) — shows what would change, no DB writes
  docker exec dotmac_erp_app python scripts/reclassify_expense_accounts.py

  # Execute — creates reclassification journals and commits
  docker exec dotmac_erp_app python scripts/reclassify_expense_accounts.py --commit
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.db import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
MISPOSTED_ACCOUNT_CODE = "6099"
RECLASSIFICATION_PREFIX = "reclass-exp-"


def run(*, commit: bool = False) -> dict[str, int]:
    """Run the reclassification."""
    results: dict[str, int] = {
        "claims_checked": 0,
        "claims_needing_fix": 0,
        "journals_created": 0,
        "already_fixed": 0,
        "errors": 0,
        "total_reclassified_amount": 0,
    }

    with SessionLocal() as db:
        # Set RLS context
        db.execute(
            text("SET app.current_organization_id = :org_id"),
            {"org_id": str(ORG_ID)},
        )

        # Import models
        from app.models.expense.expense_claim import (
            ExpenseClaim,
            ExpenseClaimItem,
        )
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.journal_entry import (
            JournalEntry,
            JournalStatus,
            JournalType,
        )
        from app.models.finance.gl.journal_entry_line import JournalEntryLine

        # Find the 6099 account
        misposted_account = db.scalar(
            select(Account).where(
                Account.organization_id == ORG_ID,
                Account.account_code == MISPOSTED_ACCOUNT_CODE,
            )
        )
        if not misposted_account:
            logger.error("Account %s not found", MISPOSTED_ACCOUNT_CODE)
            return results

        logger.info(
            "Misposted account: %s - %s (ID: %s)",
            misposted_account.account_code,
            misposted_account.account_name,
            misposted_account.account_id,
        )

        # Find all expense claims that have journals posting to 6099
        # but whose categories specify a different account
        stmt = (
            select(ExpenseClaim)
            .where(
                ExpenseClaim.organization_id == ORG_ID,
                ExpenseClaim.journal_entry_id.isnot(None),
            )
            .options(
                selectinload(ExpenseClaim.items).selectinload(ExpenseClaimItem.category)
            )
        )
        claims = list(db.scalars(stmt).all())
        results["claims_checked"] = len(claims)
        logger.info("Checking %d expense claims with GL journals", len(claims))

        total_reclass_amount = Decimal("0")

        for claim in claims:
            journal = db.get(JournalEntry, claim.journal_entry_id)
            if not journal or journal.status not in (
                JournalStatus.POSTED,
                JournalStatus.APPROVED,
            ):
                continue

            # Check if reclassification already exists
            correlation_id = f"{RECLASSIFICATION_PREFIX}{claim.claim_id}"
            existing = db.scalar(
                select(JournalEntry).where(
                    JournalEntry.correlation_id == correlation_id,
                    JournalEntry.status != JournalStatus.VOID,
                )
            )
            if existing:
                results["already_fixed"] += 1
                continue

            # Get the journal lines that debit 6099
            journal_lines_6099 = [
                jel
                for jel in journal.lines
                if jel.account_id == misposted_account.account_id
                and jel.debit_amount > 0
            ]
            if not journal_lines_6099:
                continue  # No misposted debit lines

            # Build the reclassification: for each item, determine the
            # correct account from its category
            reclass_lines: dict[UUID, Decimal] = defaultdict(Decimal)
            total_misposted = Decimal("0")

            for item in claim.items:
                # Determine correct account
                correct_account_id: UUID | None = None
                if item.expense_account_id:
                    correct_account_id = item.expense_account_id
                elif item.category and item.category.expense_account_id:
                    correct_account_id = item.category.expense_account_id

                if not correct_account_id:
                    continue

                # Skip if the correct account IS 6099 (legitimately mapped)
                if correct_account_id == misposted_account.account_id:
                    continue

                amount = item.approved_amount or item.claimed_amount
                if amount and amount > 0:
                    reclass_lines[correct_account_id] += amount
                    total_misposted += amount

            if not reclass_lines or total_misposted <= 0:
                continue

            results["claims_needing_fix"] += 1
            total_reclass_amount += total_misposted

            # Log what we'd do
            for acct_id, amount in reclass_lines.items():
                acct = db.get(Account, acct_id)
                acct_label = (
                    f"{acct.account_code} {acct.account_name}" if acct else str(acct_id)
                )
                logger.info(
                    "  %s: DR %s ₦%s / CR 6099 ₦%s",
                    claim.claim_number,
                    acct_label,
                    f"{amount:,.2f}",
                    f"{amount:,.2f}",
                )

            if not commit:
                continue

            # Create the reclassification journal
            try:
                from app.services.finance.gl.numbering import SyncNumberingService

                journal_number = SyncNumberingService.next_journal_number(db, ORG_ID)

                reclass_journal = JournalEntry(
                    organization_id=ORG_ID,
                    journal_number=journal_number,
                    journal_type=JournalType.STANDARD,
                    entry_date=date.today(),
                    posting_date=date.today(),
                    description=(
                        f"Reclassification: {claim.claim_number} — "
                        f"correct expense accounts (was 6099)"
                    ),
                    reference=claim.claim_number,
                    source_module="EXPENSE",
                    source_document_type="EXPENSE_RECLASSIFICATION",
                    source_document_id=claim.claim_id,
                    correlation_id=correlation_id,
                    status=JournalStatus.DRAFT,
                    created_by_user_id=UUID("00000000-0000-0000-0000-000000000000"),
                )
                db.add(reclass_journal)
                db.flush()

                line_num = 1
                # Debit lines: correct accounts
                for acct_id, amount in reclass_lines.items():
                    line = JournalEntryLine(
                        journal_entry_id=reclass_journal.journal_entry_id,
                        line_number=line_num,
                        account_id=acct_id,
                        debit_amount=amount,
                        credit_amount=Decimal("0"),
                        debit_amount_functional=amount,
                        credit_amount_functional=Decimal("0"),
                        description=(f"Reclassify from 6099: {claim.claim_number}"),
                        cost_center_id=claim.cost_center_id,
                    )
                    db.add(line)
                    line_num += 1

                # Credit line: 6099 for the total
                credit_line = JournalEntryLine(
                    journal_entry_id=reclass_journal.journal_entry_id,
                    line_number=line_num,
                    account_id=misposted_account.account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=total_misposted,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=total_misposted,
                    description=(
                        f"Reclassify to correct accounts: {claim.claim_number}"
                    ),
                )
                db.add(credit_line)
                db.flush()

                results["journals_created"] += 1
                logger.info(
                    "Created reclassification journal %s for %s (₦%s)",
                    journal_number,
                    claim.claim_number,
                    f"{total_misposted:,.2f}",
                )

            except Exception as exc:
                logger.exception(
                    "Failed to create reclassification for %s: %s",
                    claim.claim_number,
                    exc,
                )
                results["errors"] += 1

        results["total_reclassified_amount"] = int(total_reclass_amount)

        if commit and results["journals_created"] > 0:
            db.commit()
            logger.info(
                "Committed %d reclassification journals", results["journals_created"]
            )
        elif commit:
            logger.info("No journals to commit")
        else:
            logger.info(
                "DRY RUN — %d claims need reclassification (₦%s total). "
                "Run with --commit to create journals.",
                results["claims_needing_fix"],
                f"{total_reclass_amount:,.2f}",
            )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reclassify misposted expense GL journals (6099 → correct accounts)"
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually create reclassification journals (default: dry run)",
    )
    args = parser.parse_args()

    results = run(commit=args.commit)
    logger.info("Results: %s", results)


if __name__ == "__main__":
    main()
