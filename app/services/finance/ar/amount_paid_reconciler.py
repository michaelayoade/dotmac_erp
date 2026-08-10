"""Repair `invoice.amount_paid` from the allocations that are its authority.

`ar.payment_allocation` records what was actually applied to an invoice.
`invoice.amount_paid` is a running total maintained alongside it — a
projection. When the two disagree, the allocations are right and the total is
stale, so this recomputes the total from them and re-derives the status
through `ar.payment_status` (the owner established in #242).

That this exists at all is the interesting part: a projection needing a
repair job is the signature of a missing invariant, and it is exactly the
case ADR-0015 argues about. Under that ADR, *coverage* stops being stored
and this reconciler shrinks to `amount_paid` alone.

## Rules it carries, previously buried in a 206-line script

**It only repairs upward.** The candidate query asks for
``alloc_sum > amount_paid + dust``, never the reverse. A reconciler that
could also *reduce* `amount_paid` would fight the payment path — every
in-flight allocation looks like an over-count until it commits — so a total
that is somehow too high is left for a human. That asymmetry was implicit in
a comparison operator.

**VOID and DRAFT are excluded.** A voided invoice's allocations are not a
claim about coverage, and a draft has no ledger presence to reconcile.

**A write happens only if something actually changes** — a correction above
the dust threshold, or a different status. Without that, every run would
rewrite every row it looked at and destroy the audit value of `updated_at`.

## Two defects the extraction fixes

**No organization filter.** The query selected across every tenant, and the
script opened a raw `SessionLocal()`, so neither the ORM listener nor the RLS
GUC bounded it either. This one *writes*, which makes it worse than the
read-only case found in the exact-match allocator.

**Money went through `float()`.** The old UPDATE bound
``float(new_amount_paid)``, converting an exact `Decimal` into a binary float
on the way into a numeric column. Amounts stay `Decimal` end to end now.

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

from app.models.finance.ar.invoice import InvoiceStatus
from app.services.finance.ar.payment_status import PAYMENT_DUST, resolve_payment_status

logger = logging.getLogger(__name__)

# Statuses whose allocations are not a claim about coverage.
EXCLUDED_STATUSES = ("VOID", "DRAFT")

_STALE_SQL = text("""
    SELECT i.invoice_id,
           i.invoice_number,
           i.total_amount,
           i.amount_paid   AS current_amount_paid,
           i.status        AS current_status,
           alloc.alloc_sum AS allocation_total,
           i.due_date
    FROM ar.invoice i
    JOIN LATERAL (
        SELECT COALESCE(SUM(pa.allocated_amount), 0) AS alloc_sum
        FROM ar.payment_allocation pa
        WHERE pa.invoice_id = i.invoice_id
    ) alloc ON true
    WHERE i.organization_id = :org_id
      AND i.status <> ALL(:excluded_statuses)
      AND alloc.alloc_sum > i.amount_paid + :dust
      AND (:month_start::date IS NULL OR i.invoice_date >= :month_start::date)
      AND (:month_end::date IS NULL OR i.invoice_date < :month_end::date)
    ORDER BY (alloc.alloc_sum - i.amount_paid) DESC
""")

_UPDATE_SQL = text("""
    UPDATE ar.invoice
    SET amount_paid = :new_paid,
        status = :new_status
    WHERE invoice_id = :invoice_id
      AND organization_id = :org_id
""")


@dataclass(frozen=True)
class StaleInvoice:
    invoice_id: uuid.UUID
    invoice_number: str
    total_amount: Decimal
    current_amount_paid: Decimal
    current_status: str
    allocation_total: Decimal
    due_date: dt.date | None

    @property
    def correction(self) -> Decimal:
        return self.allocation_total - self.current_amount_paid


@dataclass
class ReconcileResult:
    examined: int = 0
    updated: int = 0
    total_correction: Decimal = Decimal("0")
    status_changes: dict[str, int] = field(default_factory=dict)


def find_stale_amount_paid(
    db: Session,
    *,
    organization_id: uuid.UUID,
    month: str | None = None,
) -> list[StaleInvoice]:
    """Invoices whose allocations exceed their recorded `amount_paid`.

    `month` is `YYYY-MM`; the range is computed here rather than by the
    caller so the month-end rollover is in one place.
    """
    month_start = month_end = None
    if month:
        year, mon = (int(part) for part in month.split("-"))
        month_start = f"{year}-{mon:02d}-01"
        month_end = f"{year + 1}-01-01" if mon == 12 else f"{year}-{mon + 1:02d}-01"

    rows = db.execute(
        _STALE_SQL,
        {
            "org_id": str(organization_id),
            "excluded_statuses": list(EXCLUDED_STATUSES),
            "dust": PAYMENT_DUST,
            "month_start": month_start,
            "month_end": month_end,
        },
    ).fetchall()
    return [
        StaleInvoice(
            invoice_id=uuid.UUID(str(row[0])),
            invoice_number=row[1],
            total_amount=Decimal(str(row[2])),
            current_amount_paid=Decimal(str(row[3])),
            current_status=str(row[4]),
            allocation_total=Decimal(str(row[5])),
            due_date=row[6],
        )
        for row in rows
    ]


def reconcile_amount_paid(
    db: Session,
    *,
    organization_id: uuid.UUID,
    month: str | None = None,
    today: dt.date | None = None,
    dry_run: bool = True,
) -> ReconcileResult:
    """Recompute `amount_paid` from allocations and re-derive the status.

    Does not commit — the caller owns the transaction.
    """
    # One clock for the whole run: an invoice due today must not resolve
    # differently depending on where in the batch it fell.
    as_of = today or dt.date.today()

    stale = find_stale_amount_paid(db, organization_id=organization_id, month=month)
    result = ReconcileResult(examined=len(stale))

    for invoice in stale:
        new_status = resolve_payment_status(
            total_amount=invoice.total_amount,
            amount_paid=invoice.allocation_total,
            current_status=InvoiceStatus(invoice.current_status),
            due_date=invoice.due_date,
            today=as_of,
        ).value

        status_changed = new_status != invoice.current_status
        if invoice.correction <= PAYMENT_DUST and not status_changed:
            # Nothing meaningful to write. Rewriting anyway would churn
            # updated_at on every run and destroy its diagnostic value.
            continue

        result.total_correction += invoice.correction
        if status_changed:
            key = f"{invoice.current_status} -> {new_status}"
            result.status_changes[key] = result.status_changes.get(key, 0) + 1

        if not dry_run:
            db.execute(
                _UPDATE_SQL,
                {
                    # Decimal, not float: this is money going into a numeric
                    # column, and float() would round-trip it through binary.
                    "new_paid": invoice.allocation_total,
                    "new_status": new_status,
                    "invoice_id": str(invoice.invoice_id),
                    "org_id": str(organization_id),
                },
            )
        result.updated += 1

    return result
