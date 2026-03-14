#!/usr/bin/env python
"""
Post approved/paid expense claims that are missing GL journal entries.

Finds expense claims in APPROVED or PAID status with journal_entry_id IS NULL
and calls the posting adapter to create GL entries.

Requires the code fix in expense_posting_adapter.py (category relationship
loading) to be deployed first — otherwise these would also mispost to 6099.

Idempotent: claims with existing journal_entry_id are skipped.

Usage:
  # Dry run (default) — shows what would be posted
  docker exec dotmac_erp_app python scripts/post_unposted_expense_claims.py

  # Execute — creates GL journal entries and commits
  docker exec dotmac_erp_app python scripts/post_unposted_expense_claims.py --commit
"""

from __future__ import annotations

import argparse
import logging
import sys
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
SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")


def run(*, commit: bool = False) -> dict[str, int]:
    """Post unposted expense claims."""
    results: dict[str, int] = {
        "claims_found": 0,
        "claims_posted": 0,
        "claims_skipped": 0,
        "errors": 0,
        "total_amount": 0,
    }

    with SessionLocal() as db:
        # Set RLS context
        db.execute(text(f"SET app.current_organization_id = '{ORG_ID}'"))

        from app.models.expense.expense_claim import (
            ExpenseClaim,
            ExpenseClaimItem,
            ExpenseClaimStatus,
        )

        # Find unposted claims
        stmt = (
            select(ExpenseClaim)
            .where(
                ExpenseClaim.organization_id == ORG_ID,
                ExpenseClaim.status.in_(
                    [
                        ExpenseClaimStatus.APPROVED,
                        ExpenseClaimStatus.PAID,
                    ]
                ),
                ExpenseClaim.journal_entry_id.is_(None),
                ExpenseClaim.total_approved_amount > Decimal("0"),
            )
            .options(
                selectinload(ExpenseClaim.items).selectinload(ExpenseClaimItem.category)
            )
            .order_by(ExpenseClaim.claim_date)
        )
        claims = list(db.scalars(stmt).all())
        results["claims_found"] = len(claims)
        logger.info("Found %d unposted expense claims", len(claims))

        if not claims:
            logger.info("Nothing to post")
            return results

        total_amount = Decimal("0")
        for claim in claims:
            total_amount += claim.total_approved_amount or Decimal("0")
            logger.info(
                "  %s | %s | %s | ₦%s",
                claim.claim_number,
                claim.claim_date,
                claim.status.value,
                f"{claim.total_approved_amount:,.2f}",
            )

        results["total_amount"] = int(total_amount)

        if not commit:
            logger.info(
                "DRY RUN — %d claims totalling ₦%s would be posted. "
                "Run with --commit to execute.",
                len(claims),
                f"{total_amount:,.2f}",
            )
            return results

        from app.services.expense.expense_posting_adapter import (
            ExpensePostingAdapter,
        )

        for claim in claims:
            try:
                user_id = claim.created_by_id or claim.approver_id or SYSTEM_USER_ID
                result = ExpensePostingAdapter.post_expense_claim(
                    db=db,
                    organization_id=ORG_ID,
                    claim_id=claim.claim_id,
                    posting_date=claim.claim_date,
                    posted_by_user_id=user_id,
                    auto_post=True,
                    idempotency_key=f"backfill-exp-{claim.claim_id}",
                )
                if result.success and result.journal_entry_id:
                    claim.journal_entry_id = result.journal_entry_id
                    results["claims_posted"] += 1
                    logger.info(
                        "Posted %s → journal %s",
                        claim.claim_number,
                        result.journal_entry_id,
                    )
                elif result.success:
                    results["claims_skipped"] += 1
                    logger.info(
                        "Skipped %s: %s",
                        claim.claim_number,
                        result.message,
                    )
                else:
                    results["errors"] += 1
                    logger.warning(
                        "Failed %s: %s",
                        claim.claim_number,
                        result.message,
                    )
            except Exception as exc:
                results["errors"] += 1
                logger.exception("Error posting %s: %s", claim.claim_number, exc)

        if results["claims_posted"] > 0:
            db.commit()
            logger.info("Committed %d GL postings", results["claims_posted"])

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post unposted approved/paid expense claims to GL"
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually post to GL (default: dry run)",
    )
    args = parser.parse_args()

    results = run(commit=args.commit)
    logger.info("Results: %s", results)


if __name__ == "__main__":
    main()
