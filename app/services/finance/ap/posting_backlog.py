"""Post AP supplier invoices that are missing their GL journal entry.

The owner of an operation that used to exist only inside
`scripts/post_unposted_ap_invoices.py`. That script decided which invoices
were postable, drove the posting adapter, and owned the transaction — so the
capability could only be exercised by a human at a shell, with no schedule,
no retry, no audit trail and no authorization.

Three defects came with living in a script, and all three are fixed by the
move rather than by patching the script:

* **A hardcoded organization.** `ORG_ID = UUID("0000...0001")` sat at module
  level, so the script could only ever serve one tenant of a multi-tenant
  system. `organization_id` is now a required argument.
* **Tenant scope set by string interpolation.**
  ``SET app.current_organization_id = '{ORG_ID}'`` primed the Postgres GUC
  directly and left the ORM listener layer unset, so only half the isolation
  was in place. Callers now use `session_for_org`, which sets both.
* **No record that it ran.** Callers wrap this in `batch_operation()`, so the
  run appears in the admin UI with its counts and its outcome.

This function does not open a session, set scope, or commit. It works inside
the caller's transaction, which is what lets the Celery task, the CLI and any
future admin action share one implementation.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)

logger = logging.getLogger(__name__)

# An invoice in any of these has been accepted into the ledger, so it should
# already carry a journal entry. Anything earlier legitimately has none.
POSTABLE_STATUSES = (
    SupplierInvoiceStatus.APPROVED,
    SupplierInvoiceStatus.POSTED,
    SupplierInvoiceStatus.PAID,
    SupplierInvoiceStatus.PARTIALLY_PAID,
)


@dataclass
class PostingBacklogResult:
    found: int = 0
    posted: int = 0
    skipped: int = 0
    errors: int = 0
    total_amount: Decimal = Decimal("0")


def find_unposted(db: Session, *, organization_id: uuid.UUID) -> list[SupplierInvoice]:
    """Supplier invoices that should have a journal entry and do not."""
    stmt = (
        select(SupplierInvoice)
        .where(
            SupplierInvoice.organization_id == organization_id,
            SupplierInvoice.status.in_(POSTABLE_STATUSES),
            SupplierInvoice.journal_entry_id.is_(None),
            SupplierInvoice.total_amount > Decimal("0"),
        )
        .order_by(SupplierInvoice.invoice_date)
    )
    return list(db.scalars(stmt).all())


def post_unposted_invoices(
    db: Session,
    *,
    organization_id: uuid.UUID,
    fallback_user_id: uuid.UUID,
    dry_run: bool = True,
) -> PostingBacklogResult:
    """Post every supplier invoice missing its GL journal entry.

    Idempotent: an invoice that already has `journal_entry_id` is not
    selected, and each posting carries a stable idempotency key, so a repeat
    run over the same backlog is a no-op rather than a double posting.

    Does not commit — the caller owns the transaction.
    """
    from app.services.finance.ap.ap_posting_adapter import APPostingAdapter

    invoices = find_unposted(db, organization_id=organization_id)
    result = PostingBacklogResult(found=len(invoices))
    result.total_amount = sum(
        (inv.total_amount or Decimal("0") for inv in invoices), Decimal("0")
    )

    if not invoices or dry_run:
        if dry_run and invoices:
            logger.info(
                "Dry run: %d unposted AP invoice(s) totalling %s",
                result.found,
                result.total_amount,
            )
        return result

    for inv in invoices:
        try:
            outcome = APPostingAdapter.post_invoice(
                db=db,
                organization_id=organization_id,
                invoice_id=inv.invoice_id,
                posting_date=inv.invoice_date,
                posted_by_user_id=inv.created_by_user_id or fallback_user_id,
                idempotency_key=f"backfill-ap-{inv.invoice_id}",
            )
            if outcome.success and outcome.journal_entry_id:
                inv.journal_entry_id = outcome.journal_entry_id
                result.posted += 1
            elif outcome.success:
                result.skipped += 1
            else:
                result.errors += 1
                logger.warning(
                    "Failed to post %s: %s", inv.invoice_number, outcome.message
                )
        except Exception:
            result.errors += 1
            logger.exception("Error posting supplier invoice %s", inv.invoice_id)

    return result
