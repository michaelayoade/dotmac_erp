"""
Delete VOID BANK_RECONCILIATION journal entries completely.

Removes JE headers, JE lines, and audit_log rows for these entries.
Run AFTER void_bank_recon_jes.py has already voided them and unmatched bank lines.

Usage:
    poetry run python scripts/delete_void_bank_recon_jes.py              # DRY RUN
    poetry run python scripts/delete_void_bank_recon_jes.py --execute     # Apply
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import psycopg

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    logger.error("DATABASE_URL environment variable is not set")
    sys.exit(1)


def run(dry_run: bool = True) -> None:
    conn = psycopg.connect(DB_URL, row_factory=psycopg.rows.dict_row, autocommit=False)
    try:
        with conn.cursor() as cur:
            # ── Count targets ──
            cur.execute("""
                SELECT COUNT(*) as cnt FROM gl.journal_entry
                WHERE source_document_type = 'BANK_RECONCILIATION' AND status = 'VOID'
            """)
            je_count = cur.fetchone()["cnt"]

            cur.execute("""
                SELECT COUNT(*) as cnt FROM gl.journal_entry_line jel
                JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
                WHERE je.source_document_type = 'BANK_RECONCILIATION' AND je.status = 'VOID'
            """)
            line_count = cur.fetchone()["cnt"]

            cur.execute("""
                SELECT COUNT(*) as cnt FROM audit.audit_log
                WHERE table_name = 'journal_entry' AND record_id::uuid IN (
                    SELECT journal_entry_id FROM gl.journal_entry
                    WHERE source_document_type = 'BANK_RECONCILIATION' AND status = 'VOID'
                )
            """)
            audit_count = cur.fetchone()["cnt"]

            logger.info("=" * 60)
            logger.info("DELETE VOID BANK_RECONCILIATION JEs")
            logger.info("=" * 60)
            logger.info("Journal entries to delete:    %d", je_count)
            logger.info("Journal entry lines to delete: %d", line_count)
            logger.info("Audit log rows to delete:     %d", audit_count)
            logger.info("")

            if je_count == 0:
                logger.info("Nothing to delete.")
                return

            if dry_run:
                logger.info("DRY RUN — no changes made. Run with --execute to apply.")
                return

            # ── Step 1: Delete audit_log rows (disable append-only trigger) ──
            logger.info("Deleting %d audit_log rows...", audit_count)
            cur.execute("ALTER TABLE audit.audit_log DISABLE TRIGGER ALL")
            cur.execute("""
                DELETE FROM audit.audit_log
                WHERE table_name = 'journal_entry' AND record_id::uuid IN (
                    SELECT journal_entry_id FROM gl.journal_entry
                    WHERE source_document_type = 'BANK_RECONCILIATION' AND status = 'VOID'
                )
            """)
            cur.execute("ALTER TABLE audit.audit_log ENABLE TRIGGER ALL")
            logger.info("  Deleted %d audit rows", cur.rowcount)

            # ── Step 2: Delete journal entry lines ──
            logger.info("Deleting %d journal entry lines...", line_count)
            cur.execute("""
                DELETE FROM gl.journal_entry_line
                WHERE journal_entry_id IN (
                    SELECT journal_entry_id FROM gl.journal_entry
                    WHERE source_document_type = 'BANK_RECONCILIATION' AND status = 'VOID'
                )
            """)
            logger.info("  Deleted %d JE lines", cur.rowcount)

            # ── Step 3: Delete journal entries ──
            logger.info("Deleting %d journal entries...", je_count)
            cur.execute("""
                DELETE FROM gl.journal_entry
                WHERE source_document_type = 'BANK_RECONCILIATION' AND status = 'VOID'
            """)
            logger.info("  Deleted %d JEs", cur.rowcount)

            # ── Commit ──
            conn.commit()
            logger.info("")
            logger.info("=" * 60)
            logger.info("SUCCESS — All records permanently deleted.")
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
        "--execute", action="store_true", help="Apply changes (default is dry run)"
    )
    args = parser.parse_args()
    run(dry_run=not args.execute)
