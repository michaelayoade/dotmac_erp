"""Post expense claims that never reached the general ledger.

The owner of an operation that lived only in
`scripts/post_unposted_expense_claims.py`, with the same three defects as the
AP and GL backlogs: a hardcoded `ORG_ID`, a `SessionLocal()` priming neither
tenant layer, and no record that it ran. That script also set the RLS GUC by
f-string (``SET app.current_organization_id = '{ORG_ID}'``) — string-built SQL
that primed one isolation layer of two.

The rule it carried: a claim is postable when it is APPROVED or PAID, has no
journal entry yet, and has a non-zero approved amount. `APPROVED` and `PAID`
are both included because payment and GL posting are independent — a claim
can be reimbursed before its journal is cut, and the backlog exists precisely
to catch the ones where that happened.

Opens no session, sets no scope, never commits: the caller owns the
transaction.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.expense.expense_claim import (
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)

logger = logging.getLogger(__name__)

# A claim in either state has been accepted, so it should carry a journal.
# Anything earlier legitimately has none.
POSTABLE_STATUSES = (ExpenseClaimStatus.APPROVED, ExpenseClaimStatus.PAID)


@dataclass
class ExpensePostingResult:
    found: int = 0
    posted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    total_amount: Decimal = Decimal("0")


def find_unposted_claims(
    db: Session, *, organization_id: uuid.UUID
) -> list[ExpenseClaim]:
    """Expense claims that should have a journal entry and do not."""
    stmt = (
        select(ExpenseClaim)
        .where(
            ExpenseClaim.organization_id == organization_id,
            ExpenseClaim.status.in_(POSTABLE_STATUSES),
            ExpenseClaim.journal_entry_id.is_(None),
            ExpenseClaim.total_approved_amount > Decimal("0"),
        )
        .options(
            selectinload(ExpenseClaim.items).selectinload(ExpenseClaimItem.category)
        )
        .order_by(ExpenseClaim.claim_date)
    )
    return list(db.scalars(stmt).all())


def post_unposted_claims(
    db: Session,
    *,
    organization_id: uuid.UUID,
    fallback_user_id: uuid.UUID,
    dry_run: bool = True,
) -> ExpensePostingResult:
    """Post every expense claim missing its GL journal entry.

    Idempotent: a claim that already has `journal_entry_id` is not selected,
    and each posting carries a stable idempotency key.

    Attribution prefers the claim's own creator, then its approver, and only
    then the system actor — a backfill should not rewrite who was responsible
    for a claim just because it ran late.
    """
    from app.services.expense.expense_posting_adapter import ExpensePostingAdapter

    claims = find_unposted_claims(db, organization_id=organization_id)
    result = ExpensePostingResult(found=len(claims))
    result.total_amount = sum(
        (c.total_approved_amount or Decimal("0") for c in claims), Decimal("0")
    )

    if dry_run:
        return result

    for claim in claims:
        try:
            outcome = ExpensePostingAdapter.post_expense_claim(
                db=db,
                organization_id=organization_id,
                claim_id=claim.claim_id,
                posting_date=claim.claim_date,
                posted_by_user_id=(
                    claim.created_by_id or claim.approver_id or fallback_user_id
                ),
                auto_post=True,
                idempotency_key=f"backfill-exp-{claim.claim_id}",
            )
            if outcome.success and outcome.journal_entry_id:
                claim.journal_entry_id = outcome.journal_entry_id
                result.posted += 1
            elif outcome.success:
                result.skipped += 1
            else:
                result.errors.append(f"{claim.claim_number}: {outcome.message}")
        except Exception as exc:
            result.errors.append(f"{claim.claim_number}: {exc}")
            logger.exception("Error posting expense claim %s", claim.claim_id)

    return result
