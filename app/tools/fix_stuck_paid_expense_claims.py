"""
Fix expense claims stuck in APPROVED after successful Paystack transfer.

Root cause: `mark_paid()` re-validates approver budgets at payment time.
When the poll task confirms multiple transfers in a single batch, the
cumulative amounts exceed the period budget and `mark_paid()` raises
`ApproverBudgetExhaustedError`.  The transfer intent is marked COMPLETED
(money has left) but the expense claim stays APPROVED.

This tool finds all COMPLETED payment intents whose source expense claim
is still APPROVED and marks them PAID with `skip_budget_check=True`.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.db.session_context import session_for_org
from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus
from app.models.finance.payments.payment_intent import (
    PaymentDirection,
    PaymentIntent,
    PaymentIntentStatus,
)

logger = logging.getLogger(__name__)

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def find_stuck_claims(db) -> list[tuple[PaymentIntent, ExpenseClaim]]:
    """Find COMPLETED outbound intents whose expense claim is still APPROVED."""
    stmt = (
        select(PaymentIntent, ExpenseClaim)
        .join(ExpenseClaim, ExpenseClaim.claim_id == PaymentIntent.source_id)
        .where(
            PaymentIntent.source_type == "EXPENSE_CLAIM",
            PaymentIntent.direction == PaymentDirection.OUTBOUND,
            PaymentIntent.status == PaymentIntentStatus.COMPLETED,
            ExpenseClaim.status == ExpenseClaimStatus.APPROVED,
            PaymentIntent.organization_id == ORG_ID,
        )
        .order_by(PaymentIntent.paid_at)
    )
    return list(db.execute(stmt).all())


def fix_stuck_claims(*, dry_run: bool = True) -> dict[str, Any]:
    """Mark stuck claims as PAID using skip_budget_check."""
    from app.services.expense.expense_service import ExpenseService

    results: dict[str, Any] = {"found": 0, "fixed": 0, "errors": []}

    with session_for_org(ORG_ID) as db:
        pairs = find_stuck_claims(db)
        results["found"] = len(pairs)

        if not pairs:
            logger.info("No stuck claims found")
            return results

        for intent, claim in pairs:
            logger.info(
                "Stuck claim %s (%s) — intent %s paid %s, amount %s",
                claim.claim_number,
                claim.recipient_name,
                intent.intent_id,
                intent.paid_at,
                intent.amount,
            )
            if dry_run:
                continue

            try:
                svc = ExpenseService(db)
                svc.mark_paid(
                    ORG_ID,
                    claim.claim_id,
                    payment_reference=intent.paystack_reference,
                    payment_date=intent.paid_at.date() if intent.paid_at else None,
                    send_notification=False,
                    skip_budget_check=True,
                )
                results["fixed"] += 1
                logger.info("Fixed %s → PAID", claim.claim_number)
            except Exception as e:
                logger.exception("Failed to fix %s: %s", claim.claim_number, e)
                results["errors"].append({"claim": claim.claim_number, "error": str(e)})

        if not dry_run:
            db.commit()
            logger.info("Committed %d fixes", results["fixed"])

    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Apply fixes (default is dry-run)"
    )
    args = parser.parse_args()

    results = fix_stuck_claims(dry_run=not args.apply)
    mode = "APPLIED" if args.apply else "DRY-RUN"
    logger.info(
        "%s — found=%d, fixed=%d, errors=%d",
        mode,
        results["found"],
        results["fixed"],
        len(results["errors"]),
    )


if __name__ == "__main__":
    main()
