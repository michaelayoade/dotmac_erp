"""
Void BANK_RECONCILIATION journal entries and unmatch their bank statement lines.

These JEs were created during bank reconciliation to match bank lines to the GL
but bypassed the AP subledger, leaving supplier invoices showing as overdue
despite having been paid.

This script:
1. Unmatches bank statement lines (deletes match records, resets flags)
2. Voids the underlying journal entries (status → VOID)

After running, the user can record proper AP supplier payments which will:
- Update supplier invoice amount_paid/status
- Create properly-sourced JEs (source_module=AP, source_document_type=SUPPLIER_PAYMENT)
- Be available for matching to the now-unmatched bank statement lines

Usage:
    poetry run python scripts/void_bank_recon_jes.py              # DRY RUN (default)
    poetry run python scripts/void_bank_recon_jes.py --execute     # Apply changes
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict

import psycopg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    logger.error("DATABASE_URL environment variable is not set")
    sys.exit(1)


def get_connection() -> psycopg.Connection:
    return psycopg.connect(DB_URL, row_factory=psycopg.rows.dict_row, autocommit=False)


def find_targets(cur: psycopg.Cursor) -> list[dict]:
    """Find all BANK_RECONCILIATION JEs matched to bank statement lines."""
    cur.execute("""
        SELECT
            m.match_id,
            m.statement_line_id,
            m.journal_line_id,
            bsl.statement_id,
            bsl.is_matched,
            bsl.description AS bank_description,
            bsl.amount AS bank_amount,
            bsl.transaction_date AS bank_date,
            je.journal_entry_id,
            je.journal_number,
            je.description AS je_description,
            je.status AS je_status,
            je.total_debit,
            je.source_document_type
        FROM banking.bank_statement_line_matches m
        JOIN banking.bank_statement_lines bsl
            ON bsl.line_id = m.statement_line_id
        JOIN gl.journal_entry_line jel
            ON jel.line_id = m.journal_line_id
        JOIN gl.journal_entry je
            ON je.journal_entry_id = jel.journal_entry_id
        WHERE je.source_document_type = 'BANK_RECONCILIATION'
          AND je.status = 'POSTED'
        ORDER BY bsl.transaction_date, je.journal_number
    """)
    return list(cur.fetchall())


def execute_void(dry_run: bool = True) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # ── Step 1: Find all targets ──
            targets = find_targets(cur)
            if not targets:
                logger.info("No BANK_RECONCILIATION matches found. Nothing to do.")
                return

            # Collect unique IDs
            match_ids = [r["match_id"] for r in targets]
            line_ids = list({r["statement_line_id"] for r in targets})
            je_ids = list({r["journal_entry_id"] for r in targets})

            # Group by statement to update counters
            stmt_counts: dict[str, int] = defaultdict(int)
            for r in targets:
                stmt_counts[str(r["statement_id"])] += 1

            logger.info("=" * 60)
            logger.info("BANK_RECONCILIATION JE Void & Unmatch")
            logger.info("=" * 60)
            logger.info("Match records to delete:   %d", len(match_ids))
            logger.info("Bank lines to unmatch:     %d", len(line_ids))
            logger.info("Journal entries to void:   %d", len(je_ids))
            logger.info("Statements affected:       %d", len(stmt_counts))
            logger.info("")

            total_amount = sum(r["total_debit"] for r in targets)
            logger.info("Total JE amount:           %s", f"{total_amount:,.2f}")
            logger.info("")

            # Show sample
            logger.info("Sample (first 10):")
            for r in targets[:10]:
                logger.info(
                    "  %s | %s | %s | %s",
                    r["journal_number"],
                    r["bank_date"],
                    f"{r['bank_amount']:>14,.2f}",
                    (r["bank_description"] or "")[:50],
                )
            if len(targets) > 10:
                logger.info("  ... and %d more", len(targets) - 10)
            logger.info("")

            if dry_run:
                logger.info("DRY RUN — no changes made. Run with --execute to apply.")
                return

            # ── Step 2: Delete match records ──
            logger.info("Deleting %d match records...", len(match_ids))
            cur.execute(
                """
                DELETE FROM banking.bank_statement_line_matches
                WHERE match_id = ANY(%s::uuid[])
                """,
                (match_ids,),
            )
            logger.info("  Deleted %d match records", cur.rowcount)

            # ── Step 3: Reset bank statement line flags ──
            logger.info("Resetting %d bank statement lines...", len(line_ids))
            cur.execute(
                """
                UPDATE banking.bank_statement_lines
                SET is_matched = FALSE,
                    matched_at = NULL,
                    matched_by = NULL,
                    matched_journal_line_id = NULL
                WHERE line_id = ANY(%s::uuid[])
                """,
                (line_ids,),
            )
            logger.info("  Updated %d bank lines", cur.rowcount)

            # ── Step 4: Update statement counters ──
            logger.info("Updating %d statement counters...", len(stmt_counts))
            for stmt_id, count in stmt_counts.items():
                cur.execute(
                    """
                    UPDATE banking.bank_statements
                    SET matched_lines   = GREATEST(COALESCE(matched_lines, 0) - %s, 0),
                        unmatched_lines = COALESCE(unmatched_lines, 0) + %s
                    WHERE statement_id = %s::uuid
                    """,
                    (count, count, stmt_id),
                )

            # ── Step 5: Void the journal entries ──
            logger.info("Voiding %d journal entries...", len(je_ids))
            cur.execute(
                """
                UPDATE gl.journal_entry
                SET status = 'VOID'
                WHERE journal_entry_id = ANY(%s::uuid[])
                  AND status = 'POSTED'
                """,
                (je_ids,),
            )
            logger.info("  Voided %d journal entries", cur.rowcount)

            # ── Commit ──
            conn.commit()
            logger.info("")
            logger.info("=" * 60)
            logger.info("SUCCESS — All changes committed.")
            logger.info("  %d matches removed", len(match_ids))
            logger.info("  %d bank lines unmatched", len(line_ids))
            logger.info("  %d JEs voided", len(je_ids))
            logger.info("=" * 60)

    except Exception:
        conn.rollback()
        logger.exception("Error — all changes rolled back")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply changes (default is dry run)",
    )
    args = parser.parse_args()
    execute_void(dry_run=not args.execute)
