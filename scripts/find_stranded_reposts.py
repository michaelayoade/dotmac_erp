#!/usr/bin/env python
"""Detect invoices stranded by the pre-fix repost idempotency collision.

Before the reversal-aware idempotent-replay fix in
``LedgerPostingService.post_journal_entry``, a reverse-and-repost of a
posted invoice (dotmac_sub resync path) stranded the repost: the fresh
journal stayed APPROVED with zero posted ledger lines while the invoice
claimed ``posting_status='POSTED'`` against the stale, now-REVERSED
original batch. The document's GL netted to zero — AR and revenue were
silently understated.

This script is READ-ONLY remediation tooling: a single SELECT-based scan
listing every invoice where

    * ``posting_status = 'POSTED'``            (invoice claims it is posted)
    * the journal on the invoice's linked posting batch is REVERSED
    * no successor POSTED (non-reversal) journal exists for the same
      source document

and the understated total per organization + currency.

Usage:
    python scripts/find_stranded_reposts.py --database-url postgresql://...
    DATABASE_URL=postgresql://... python scripts/find_stranded_reposts.py

Deliberately does NOT import ``app.main`` — only models and a local
session factory — so it can run against production with just DATABASE_URL.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.models.finance.ar.invoice import Invoice
from app.models.finance.gl.journal_entry import (
    JournalEntry,
    JournalStatus,
    JournalType,
)


@dataclass(frozen=True)
class StrandedRepost:
    """One invoice whose claimed posting is backed by a reversed batch."""

    organization_id: UUID
    invoice_id: UUID
    invoice_number: str
    currency_code: str
    total_amount: Decimal
    functional_currency_amount: Decimal
    original_journal_number: str
    reversal_date: date | None


def find_stranded_reposts(db: Session) -> list[StrandedRepost]:
    """Return invoices stranded POSTED against a reversed batch.

    An invoice is stranded when it claims ``posting_status='POSTED'`` while
    the journal that owns its linked posting batch is REVERSED and no
    successor POSTED (non-reversal) AR INVOICE journal exists for the same
    source document. Cross-organization by design (remediation sweep); every
    journal correlation is still org-scoped.
    """
    original = aliased(JournalEntry)
    reversal = aliased(JournalEntry)

    successor_exists = (
        select(JournalEntry.journal_entry_id)
        .where(
            JournalEntry.organization_id == Invoice.organization_id,
            JournalEntry.source_module == "AR",
            JournalEntry.source_document_type == "INVOICE",
            JournalEntry.source_document_id == Invoice.invoice_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.journal_type != JournalType.REVERSAL,
        )
        .exists()
    )

    stmt = (
        select(
            Invoice.organization_id,
            Invoice.invoice_id,
            Invoice.invoice_number,
            Invoice.currency_code,
            Invoice.total_amount,
            Invoice.functional_currency_amount,
            original.journal_number,
            reversal.posting_date,
        )
        .join(
            original,
            (original.posting_batch_id == Invoice.posting_batch_id)
            & (original.organization_id == Invoice.organization_id),
        )
        .outerjoin(
            reversal,
            reversal.journal_entry_id == original.reversal_journal_id,
        )
        .where(
            Invoice.posting_status == "POSTED",
            Invoice.posting_batch_id.is_not(None),
            original.status == JournalStatus.REVERSED,
            ~successor_exists,
        )
        .order_by(Invoice.organization_id, Invoice.invoice_number)
    )

    return [
        StrandedRepost(
            organization_id=row.organization_id,
            invoice_id=row.invoice_id,
            invoice_number=row.invoice_number,
            currency_code=row.currency_code,
            total_amount=row.total_amount,
            functional_currency_amount=row.functional_currency_amount,
            original_journal_number=row.journal_number,
            reversal_date=row.posting_date,
        )
        for row in db.execute(stmt).all()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "READ-ONLY scan for invoices stranded POSTED against a "
            "reversed GL batch (pre-fix repost idempotency collision)."
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="SQLAlchemy database URL (default: env DATABASE_URL)",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or env DATABASE_URL is required")

    engine = create_engine(args.database_url)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        stranded = find_stranded_reposts(db)

    if not stranded:
        print("No stranded reposts found.")
        return 0

    print(
        f"{'organization_id':<38} {'invoice_id':<38} {'invoice_number':<22} "
        f"{'currency':<8} {'total':>16} {'functional':>16} "
        f"{'orig_journal':<16} reversal_date"
    )
    for row in stranded:
        print(
            f"{str(row.organization_id):<38} {str(row.invoice_id):<38} "
            f"{row.invoice_number:<22} {row.currency_code:<8} "
            f"{row.total_amount:>16} {row.functional_currency_amount:>16} "
            f"{row.original_journal_number:<16} {row.reversal_date or '-'}"
        )

    totals: dict[tuple[UUID, str], Decimal] = {}
    for row in stranded:
        key = (row.organization_id, row.currency_code)
        totals[key] = totals.get(key, Decimal("0")) + row.total_amount

    print(f"\n{len(stranded)} stranded invoice(s).")
    print("Understated totals per organization + currency:")
    for (org_id, currency), total in sorted(
        totals.items(), key=lambda item: (str(item[0][0]), item[0][1])
    ):
        print(f"  {org_id}  {currency}  {total}")

    return 1


if __name__ == "__main__":
    sys.exit(main())
