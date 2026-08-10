"""
Clean Slate: Delete all AR/Expense transactional data for re-sync.

Deletes in FK-safe order:
  1. GL posted_ledger_line (AR/EXPENSE source_module)
  2. GL posting_batch (AR/EXPENSE source_module)
  3. GL journal_entry_line (for AR/EXPENSE journals)
  4. GL journal_entry (AR/EXPENSE source_module)
  5. AR payment_allocation (all)
  6. AR invoice_line (all)
  7. AR customer_payment (all)
  8. AR invoice (all — includes credit notes)
  9. Expense expense_claim_action (all)
 10. Expense expense_claim_item (all)
 11. Expense expense_claim (all)
 12. AR external_sync (SPLYNX source)
 13. Sync sync_entity (relevant doctypes)

Preserves: ar.customer, gl.account, ap.supplier, inventory master data,
           AP invoices/payments, non-AR/Expense GL entries.

Usage:
    python scripts/clean_slate_resync.py --dry-run   # Preview counts
    python scripts/clean_slate_resync.py --execute    # Actually delete
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Source modules to target for GL cleanup
AR_EXPENSE_MODULES = ("AR", "EXPENSE")

# ERPNext doctypes to clean from sync tracking
SYNC_DOCTYPES = (
    "Expense Claim",
    "Expense Claim Detail",
    "Expense Claim Type",
    "Sales Invoice",
    "Sales Invoice Item",
    "Payment Entry",
    "Payment Entry Reference",
)


def _count(conn: object, sql: str, params: dict[str, object] | None = None) -> int:
    """Execute a COUNT query and return the scalar result."""
    from sqlalchemy import text

    result = conn.execute(text(sql), params or {})  # type: ignore[union-attr]
    return result.scalar() or 0


def _execute(conn: object, sql: str, params: dict[str, object] | None = None) -> int:
    """Execute a DELETE/UPDATE and return rowcount."""
    from sqlalchemy import text

    result = conn.execute(text(sql), params or {})  # type: ignore[union-attr]
    return result.rowcount  # type: ignore[union-attr]


def clean_slate(execute: bool = False) -> None:
    """Delete all AR/Expense transactional data."""
    from app.db import SessionLocal

    # Define delete steps in FK-safe order
    steps = [
        (
            "GL posted_ledger_line (AR/EXPENSE)",
            "SELECT COUNT(*) FROM gl.posted_ledger_line WHERE source_module IN ('AR', 'EXPENSE')",
            "DELETE FROM gl.posted_ledger_line WHERE source_module IN ('AR', 'EXPENSE')",
        ),
        (
            "GL posting_batch (AR/EXPENSE)",
            "SELECT COUNT(*) FROM gl.posting_batch WHERE source_module IN ('AR', 'EXPENSE')",
            "DELETE FROM gl.posting_batch WHERE source_module IN ('AR', 'EXPENSE')",
        ),
        (
            "GL journal_entry_line (AR/EXPENSE journals)",
            """SELECT COUNT(*) FROM gl.journal_entry_line jel
               JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
               WHERE je.source_module IN ('AR', 'EXPENSE')""",
            """DELETE FROM gl.journal_entry_line
               WHERE journal_entry_id IN (
                   SELECT journal_entry_id FROM gl.journal_entry
                   WHERE source_module IN ('AR', 'EXPENSE')
               )""",
        ),
        (
            "GL journal_entry (AR/EXPENSE)",
            "SELECT COUNT(*) FROM gl.journal_entry WHERE source_module IN ('AR', 'EXPENSE')",
            "DELETE FROM gl.journal_entry WHERE source_module IN ('AR', 'EXPENSE')",
        ),
        (
            "AR payment_allocation",
            "SELECT COUNT(*) FROM ar.payment_allocation",
            "DELETE FROM ar.payment_allocation",
        ),
        (
            "AR invoice_line",
            "SELECT COUNT(*) FROM ar.invoice_line",
            "DELETE FROM ar.invoice_line",
        ),
        (
            "AR customer_payment",
            "SELECT COUNT(*) FROM ar.customer_payment",
            "DELETE FROM ar.customer_payment",
        ),
        (
            "AR invoice (incl. credit notes)",
            "SELECT COUNT(*) FROM ar.invoice",
            "DELETE FROM ar.invoice",
        ),
        (
            "Expense expense_claim_action",
            "SELECT COUNT(*) FROM expense.expense_claim_action",
            "DELETE FROM expense.expense_claim_action",
        ),
        (
            "Expense expense_claim_item",
            "SELECT COUNT(*) FROM expense.expense_claim_item",
            "DELETE FROM expense.expense_claim_item",
        ),
        (
            "Expense expense_claim",
            "SELECT COUNT(*) FROM expense.expense_claim",
            "DELETE FROM expense.expense_claim",
        ),
        (
            "AR external_sync (SPLYNX)",
            "SELECT COUNT(*) FROM ar.external_sync WHERE source = 'SPLYNX'",
            "DELETE FROM ar.external_sync WHERE source = 'SPLYNX'",
        ),
        (
            "Sync sync_entity (AR/Expense doctypes)",
            """SELECT COUNT(*) FROM sync.sync_entity
               WHERE source_doctype = ANY(:doctypes)""",
            """DELETE FROM sync.sync_entity
               WHERE source_doctype = ANY(:doctypes)""",
        ),
    ]

    with SessionLocal() as db:
        conn = db.connection()

        logger.info("=" * 60)
        logger.info("CLEAN SLATE: AR/Expense transactional data deletion")
        logger.info("=" * 60)

        total_to_delete = 0

        # Phase 1: Count everything
        for label, count_sql, _delete_sql in steps:
            params: dict[str, object] | None = None
            if ":doctypes" in count_sql:
                params = {"doctypes": list(SYNC_DOCTYPES)}
            count = _count(conn, count_sql, params)
            total_to_delete += count
            logger.info("  %-45s %8d records", label, count)

        logger.info("-" * 60)
        logger.info("  TOTAL TO DELETE: %d records", total_to_delete)
        logger.info("")

        if total_to_delete == 0:
            logger.info("Nothing to delete. Database is already clean.")
            return

        if not execute:
            logger.info("[DRY RUN] No changes made. Use --execute to delete.")
            return

        # Phase 2: Delete in order
        logger.info("EXECUTING DELETES...")
        total_deleted = 0

        for label, _count_sql, delete_sql in steps:
            params = None
            if ":doctypes" in delete_sql:
                params = {"doctypes": list(SYNC_DOCTYPES)}
            deleted = _execute(conn, delete_sql, params)
            total_deleted += deleted
            logger.info("  %-45s %8d deleted", label, deleted)

        db.commit()

        logger.info("-" * 60)
        logger.info("  TOTAL DELETED: %d records", total_deleted)
        logger.info("")

        # Phase 3: Reset numbering sequences
        logger.info("Resetting numbering sequences...")
        from sqlalchemy import text

        conn2 = db.connection()
        for seq_type in ("INVOICE", "PAYMENT", "CREDIT_NOTE"):
            result = conn2.execute(
                text(
                    "UPDATE core_config.numbering_sequence "
                    "SET current_number = 0, last_used_at = NULL "
                    "WHERE sequence_type = :seq_type"
                ),
                {"seq_type": seq_type},
            )
            if result.rowcount:
                logger.info("  Reset %s sequence", seq_type)

        db.commit()
        logger.info("Clean slate complete.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete all AR/Expense transactional data for clean re-sync"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Preview counts only")
    group.add_argument("--execute", action="store_true", help="Actually delete records")
    args = parser.parse_args()

    clean_slate(execute=args.execute)


if __name__ == "__main__":
    main()
