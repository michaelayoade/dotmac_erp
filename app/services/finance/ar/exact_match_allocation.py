"""Tier-A allocation: unallocated payments with exactly one matching invoice.

The owner of an operation that lived in
`scripts/allocate_exact_match_payments.py`. Only unambiguous matches are
allocated — one cleared payment, exactly one open invoice for the same
customer whose total is within `AMOUNT_TOLERANCE` of the payment. A payment
matching two invoices is left alone, because guessing which one it settles is
how misallocations happen.

Tier-B (`FIFOAllocationService`) handles what is left over.

## The two defects this extraction fixes

**The query had no organization filter at all.** Not a wrong one — none. It
selected from `ar.customer_payment` and joined `ar.invoice` on `customer_id`
alone, and the script opened a raw `SessionLocal()`, so neither the ORM
listener nor the RLS GUC was primed either. Nothing at any layer bounded the
query to a tenant. It survived because customer ids do not collide across
organizations in practice, which is a property of the data rather than
anything the code arranged. `organization_id` is now filtered explicitly on
both sides of the join; the RLS GUC is a second line of defence, never the
only one.

**Year and limit were spliced in as strings.** ``f"AND cp.payment_date >=
'{year}-01-01'"`` and ``f"LIMIT {limit}"``. Both came from argparse `int`, so
this was not exploitable, but SQL assembled by formatting is a habit that
stops being safe the moment an argument becomes a string. Both are bound
parameters now.

Opens no session, sets no scope and never commits — the caller owns the
transaction.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.finance.ar.invoice import Invoice
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.services.finance.ar.payment_status import apply_payment_status

logger = logging.getLogger(__name__)

# How close a payment must be to an invoice total to count as "the same
# amount". Sub-cent, like every other money tolerance in this codebase.
AMOUNT_TOLERANCE = Decimal("0.01")

# Invoice states that can still receive a payment.
OPEN_INVOICE_STATUSES = ("POSTED", "OVERDUE", "PARTIALLY_PAID")

_CANDIDATE_SQL = text("""
    WITH unalloc AS (
        SELECT cp.payment_id, cp.payment_number, cp.payment_date,
               cp.amount, cp.customer_id
        FROM ar.customer_payment cp
        WHERE cp.organization_id = :org_id
          AND cp.status::text = 'CLEARED'
          AND cp.amount > 0
          AND (:date_from::date IS NULL OR cp.payment_date >= :date_from::date)
          AND (:date_to::date IS NULL OR cp.payment_date < :date_to::date)
          AND NOT EXISTS (
              SELECT 1 FROM ar.payment_allocation pa
              WHERE pa.payment_id = cp.payment_id
          )
    ),
    with_matches AS (
        SELECT u.payment_id, u.payment_number, u.payment_date,
               u.amount AS payment_amount, u.customer_id,
               c.legal_name AS customer_name,
               i.invoice_id, i.invoice_number, i.total_amount,
               (i.total_amount - COALESCE(i.amount_paid, 0)) AS outstanding,
               count(*) OVER (PARTITION BY u.payment_id) AS match_count
        FROM unalloc u
        JOIN ar.customer c
          ON c.customer_id = u.customer_id
         AND c.organization_id = :org_id
        JOIN ar.invoice i
          ON i.customer_id = u.customer_id
         AND i.organization_id = :org_id
         AND abs(i.total_amount - u.amount) < :tolerance
         AND i.status::text = ANY(:open_statuses)
         AND (i.total_amount - COALESCE(i.amount_paid, 0)) > 0
    )
    SELECT payment_id, payment_number, payment_date, payment_amount,
           customer_id, customer_name,
           invoice_id, invoice_number, total_amount, outstanding
    FROM with_matches
    WHERE match_count = 1
    ORDER BY payment_date, payment_number
    LIMIT :row_limit
""")


@dataclass(frozen=True)
class MatchCandidate:
    """A payment matched to exactly one invoice."""

    payment_id: uuid.UUID
    payment_number: str
    payment_date: dt.date
    payment_amount: Decimal
    customer_id: uuid.UUID
    customer_name: str
    invoice_id: uuid.UUID
    invoice_number: str
    invoice_total: Decimal
    invoice_outstanding: Decimal


@dataclass
class ExactMatchResult:
    candidates: int = 0
    allocated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    total_allocated: Decimal = Decimal("0")


def find_exact_match_candidates(
    db: Session,
    *,
    organization_id: uuid.UUID,
    year: int | None = None,
    limit: int | None = None,
) -> list[MatchCandidate]:
    """Cleared, unallocated payments with exactly one matching open invoice."""
    params = {
        "org_id": str(organization_id),
        "tolerance": float(AMOUNT_TOLERANCE),
        "open_statuses": list(OPEN_INVOICE_STATUSES),
        "date_from": f"{year}-01-01" if year else None,
        "date_to": f"{year + 1}-01-01" if year else None,
        # NULL LIMIT is "no limit" in Postgres, which keeps the bound
        # parameter rather than reintroducing a formatted clause.
        "row_limit": limit,
    }
    rows = db.execute(_CANDIDATE_SQL, params).fetchall()
    return [
        MatchCandidate(
            payment_id=row[0],
            payment_number=row[1],
            payment_date=row[2],
            payment_amount=row[3],
            customer_id=row[4],
            customer_name=row[5],
            invoice_id=row[6],
            invoice_number=row[7],
            invoice_total=row[8],
            invoice_outstanding=row[9],
        )
        for row in rows
    ]


def allocate_candidate(db: Session, candidate: MatchCandidate) -> bool:
    """Create the allocation and update the invoice. False if nothing to do.

    Re-reads the invoice for its live balance rather than trusting the
    snapshot from the candidate query: several payments in one batch can
    target the same invoice, and the second must see the first's effect.
    """
    invoice = db.get(Invoice, candidate.invoice_id)
    if invoice is None:
        raise ValueError(f"Invoice {candidate.invoice_id} not found")

    live_outstanding = invoice.total_amount - invoice.amount_paid
    if live_outstanding <= 0:
        logger.info(
            "Skipping %s -> %s: already settled",
            candidate.payment_number,
            candidate.invoice_number,
        )
        return False

    allocated_amount = min(candidate.payment_amount, live_outstanding)
    db.add(
        PaymentAllocation(
            payment_id=candidate.payment_id,
            invoice_id=candidate.invoice_id,
            allocated_amount=allocated_amount,
            discount_taken=Decimal("0"),
            write_off_amount=Decimal("0"),
            exchange_difference=Decimal("0"),
            allocation_date=candidate.payment_date,
        )
    )
    invoice.amount_paid = invoice.amount_paid + allocated_amount
    # The status rule is owned by ar.payment_status, not by this allocator.
    apply_payment_status(invoice)
    db.flush()
    return True


def allocate_exact_matches(
    db: Session,
    *,
    organization_id: uuid.UUID,
    year: int | None = None,
    limit: int | None = None,
    dry_run: bool = True,
) -> ExactMatchResult:
    """Allocate every unambiguous payment/invoice match. Does not commit."""
    candidates = find_exact_match_candidates(
        db, organization_id=organization_id, year=year, limit=limit
    )
    result = ExactMatchResult(candidates=len(candidates))
    if dry_run:
        result.total_allocated = sum(
            (c.payment_amount for c in candidates), Decimal("0")
        )
        return result

    for candidate in candidates:
        try:
            if allocate_candidate(db, candidate):
                result.allocated += 1
                result.total_allocated += min(
                    candidate.payment_amount, candidate.invoice_outstanding
                )
            else:
                result.skipped += 1
        except Exception as exc:
            result.errors.append(f"{candidate.payment_number}: {exc}")
            logger.exception("Error allocating %s", candidate.payment_number)

    return result


__all__ = [
    "AMOUNT_TOLERANCE",
    "OPEN_INVOICE_STATUSES",
    "ExactMatchResult",
    "MatchCandidate",
    "allocate_candidate",
    "allocate_exact_matches",
    "find_exact_match_candidates",
]
