#!/usr/bin/env python
"""
Fix duplicate AR invoice GL journal postings.

438 AR invoices were posted to GL multiple times (1,327 journals total, 889
duplicates). This script:

1. Voids SUBMITTED/DRAFT duplicate journals (not in ledger — safe)
2. Reverses extra POSTED duplicates (keeps the earliest POSTED journal)
3. Ensures each invoice's journal_entry_id points to the surviving journal

Root cause: The AR post_invoice() function lacked idempotency guards, and
ensure_gl_posted() re-called it when journal_entry_id wasn't written back.

Idempotent: re-running produces zero additional changes.

Usage:
  # Dry run (default)
  docker exec dotmac_erp_app python scripts/fix_duplicate_ar_journals.py

  # Execute
  docker exec dotmac_erp_app python scripts/fix_duplicate_ar_journals.py --commit
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text

from app.db import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def run(*, commit: bool = False) -> dict[str, int]:
    """Fix duplicate AR invoice journals."""
    results: dict[str, int] = {
        "invoices_checked": 0,
        "submitted_voided": 0,
        "draft_voided": 0,
        "posted_reversed": 0,
        "references_fixed": 0,
        "errors": 0,
    }

    with SessionLocal() as db:
        db.execute(text(f"SET app.current_organization_id = '{ORG_ID}'"))

        from app.models.finance.ar.invoice import Invoice
        from app.models.finance.gl.journal_entry import (
            JournalEntry,
            JournalStatus,
            JournalType,
        )

        # Find all AR invoices with duplicate journals
        duplicate_subq = (
            select(
                JournalEntry.source_document_id,
                func.count().label("cnt"),
            )
            .where(
                JournalEntry.source_module == "AR",
                JournalEntry.source_document_type == "INVOICE",
                JournalEntry.source_document_id.isnot(None),
                JournalEntry.status.notin_(
                    [JournalStatus.VOID, JournalStatus.REVERSED]
                ),
                JournalEntry.journal_type != JournalType.REVERSAL,
            )
            .group_by(JournalEntry.source_document_id)
            .having(func.count() > 1)
            .subquery()
        )

        invoice_ids = list(
            db.scalars(select(duplicate_subq.c.source_document_id)).all()
        )
        results["invoices_checked"] = len(invoice_ids)
        logger.info("Found %d invoices with duplicate journals", len(invoice_ids))

        for inv_id in invoice_ids:
            # Get all journals for this invoice, ordered by created_at
            journals = list(
                db.scalars(
                    select(JournalEntry)
                    .where(
                        JournalEntry.source_module == "AR",
                        JournalEntry.source_document_type == "INVOICE",
                        JournalEntry.source_document_id == inv_id,
                        JournalEntry.status.notin_(
                            [JournalStatus.VOID, JournalStatus.REVERSED]
                        ),
                        JournalEntry.journal_type != JournalType.REVERSAL,
                    )
                    .order_by(JournalEntry.created_at)
                ).all()
            )

            if len(journals) <= 1:
                continue

            # Keep the earliest POSTED journal, or earliest overall if none posted
            posted_journals = [j for j in journals if j.status == JournalStatus.POSTED]
            keep_journal = posted_journals[0] if posted_journals else journals[0]
            duplicates = [
                j
                for j in journals
                if j.journal_entry_id != keep_journal.journal_entry_id
            ]

            logger.info(
                "Invoice %s: keeping %s (%s), removing %d duplicates",
                inv_id,
                keep_journal.journal_number,
                keep_journal.status.value,
                len(duplicates),
            )

            # Fix the invoice reference
            invoice = db.get(Invoice, inv_id)
            if invoice and invoice.journal_entry_id != keep_journal.journal_entry_id:
                if commit:
                    invoice.journal_entry_id = keep_journal.journal_entry_id
                    db.flush()
                results["references_fixed"] += 1

            for dup in duplicates:
                if dup.status in (JournalStatus.SUBMITTED, JournalStatus.DRAFT):
                    # Safe to void — not in ledger
                    if commit:
                        dup.status = JournalStatus.VOID
                        db.flush()
                    if dup.status == JournalStatus.SUBMITTED:
                        results["submitted_voided"] += 1
                    else:
                        results["draft_voided"] += 1
                    logger.info(
                        "  VOID %s (%s) — not in ledger",
                        dup.journal_number,
                        dup.status.value if not commit else "VOID",
                    )
                elif dup.status == JournalStatus.POSTED:
                    # Need to reverse — it's in the ledger
                    if commit:
                        try:
                            from app.services.finance.gl.reversal import ReversalService

                            reversal_result = ReversalService.create_reversal(
                                db=db,
                                organization_id=ORG_ID,
                                original_journal_id=dup.journal_entry_id,
                                reversal_date=dup.entry_date,
                                created_by_user_id=UUID(
                                    "00000000-0000-0000-0000-000000000000"
                                ),
                                reason=f"Duplicate posting for invoice {inv_id}",
                                auto_post=True,
                            )
                            if reversal_result.success:
                                results["posted_reversed"] += 1
                                logger.info(
                                    "  REVERSED %s → %s",
                                    dup.journal_number,
                                    reversal_result.reversal_journal_number,
                                )
                            else:
                                results["errors"] += 1
                                logger.warning(
                                    "  FAILED to reverse %s: %s",
                                    dup.journal_number,
                                    reversal_result.message,
                                )
                        except Exception as exc:
                            results["errors"] += 1
                            logger.exception(
                                "  Error reversing %s: %s",
                                dup.journal_number,
                                exc,
                            )
                    else:
                        results["posted_reversed"] += 1
                        logger.info(
                            "  WOULD REVERSE %s (POSTED)",
                            dup.journal_number,
                        )

        if commit:
            db.commit()
            logger.info(
                "Committed: %d voided, %d reversed, %d references fixed",
                results["submitted_voided"] + results["draft_voided"],
                results["posted_reversed"],
                results["references_fixed"],
            )
        else:
            logger.info(
                "DRY RUN — would void %d SUBMITTED + %d DRAFT, "
                "reverse %d POSTED, fix %d references. "
                "Run with --commit to execute.",
                results["submitted_voided"],
                results["draft_voided"],
                results["posted_reversed"],
                results["references_fixed"],
            )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix duplicate AR invoice GL journal postings"
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually void/reverse duplicates (default: dry run)",
    )
    args = parser.parse_args()

    results = run(commit=args.commit)
    logger.info("Results: %s", results)


if __name__ == "__main__":
    main()
