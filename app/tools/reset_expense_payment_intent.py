"""Reset an expense payment intent for a claim so it can be retried."""

from __future__ import annotations

import argparse
from uuid import UUID

from app.db import SessionLocal
from app.models.expense.expense_claim import ExpenseClaim
from app.services.finance.payments import PaymentService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset the latest outbound expense payment intent for a claim.",
    )
    parser.add_argument(
        "claim_id", help="Expense claim UUID to reset payment intent for"
    )
    parser.add_argument(
        "--reason",
        default=None,
        help="Optional reason stored on intent.gateway_response.manual_revert_reason",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow resetting active intents",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Find and validate reset target without writing to DB",
    )
    args = parser.parse_args()

    claim_id = UUID(args.claim_id)

    with SessionLocal() as db:
        claim = db.get(ExpenseClaim, claim_id)
        if not claim:
            raise SystemExit(f"Claim {claim_id} not found")

        svc = PaymentService(db, claim.organization_id)
        intent = svc.reset_expense_payment_intent(
            expense_claim_id=claim_id,
            reason=args.reason,
            force=args.force,
        )

        if args.dry_run:
            db.rollback()
            print(
                "DRY RUN: payment intent reset validation passed. "
                f"claim={claim.claim_number} intent={intent.intent_id} status={intent.status.value}"
            )
            return

        db.commit()
        print(
            f"Reset complete: claim={claim.claim_number} intent={intent.intent_id} "
            f"status={intent.status.value}"
        )


if __name__ == "__main__":
    main()
