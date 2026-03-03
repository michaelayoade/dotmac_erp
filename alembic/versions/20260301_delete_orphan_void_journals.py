"""Delete orphan VOID journals with no posted ledger lines.

After the ERPNext sync and subsequent voiding operations, 15,227 VOID journal
entries remain in the database with zero posted ledger lines:
- 15,185 with source_document_type = 'CUSTOMER_PAYMENT'
- 42 with NULL source_document_type

These journals:
- Have no financial impact (no posted ledger lines)
- Have no FK references from ar.customer_payment, ar.invoice, or other tables
- Clutter the journal list and slow queries

This migration:
1. Records affected journals in `_migration_void_orphan_journals` audit table
2. Deletes journal_entry_line rows (FK child)
3. NULLs ar.invoice.journal_entry_id references (safety — no FK constraint)
4. Deletes the journal_entry rows

Revision ID: 20260301_delete_orphan_void_journals
Revises: 20260301_remap_erpnext_accounts
Create Date: 2026-03-01
"""

from sqlalchemy import text as sa_text

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260301_delete_orphan_void_journals"
down_revision = "20260301_remap_erpnext_accounts"
branch_labels = None
depends_on = None

ORG_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # Step 0: Create audit tracking table
    # ------------------------------------------------------------------
    conn.execute(
        sa_text(
            """
            CREATE TABLE IF NOT EXISTS _migration_void_orphan_journals (
                journal_entry_id UUID PRIMARY KEY,
                journal_number TEXT,
                source_document_type TEXT,
                entry_date DATE,
                total_debit NUMERIC(15,2)
            )
            """
        )
    )

    # ------------------------------------------------------------------
    # Step 1: Record affected journals (VOID + no posted ledger lines)
    # ------------------------------------------------------------------
    conn.execute(
        sa_text(
            """
            INSERT INTO _migration_void_orphan_journals
                (journal_entry_id, journal_number, source_document_type,
                 entry_date, total_debit)
            SELECT
                je.journal_entry_id, je.journal_number,
                je.source_document_type, je.entry_date, je.total_debit
            FROM gl.journal_entry je
            WHERE je.status = 'VOID'
              AND je.organization_id = :org_id
              AND NOT EXISTS (
                  SELECT 1 FROM gl.posted_ledger_line pll
                  WHERE pll.journal_entry_id = je.journal_entry_id
              )
            ON CONFLICT DO NOTHING
            """
        ),
        {"org_id": ORG_ID},
    )

    # ------------------------------------------------------------------
    # Step 2: Delete journal_entry_line rows (FK child of journal_entry)
    # ------------------------------------------------------------------
    conn.execute(
        sa_text(
            """
            DELETE FROM gl.journal_entry_line
            WHERE journal_entry_id IN (
                SELECT journal_entry_id
                FROM _migration_void_orphan_journals
            )
            """
        )
    )

    # ------------------------------------------------------------------
    # Step 3: NULL out ar.invoice FK references (safety — no FK constraint
    #         exists, but NULL dangling references for data integrity)
    # ------------------------------------------------------------------
    conn.execute(
        sa_text(
            """
            UPDATE ar.invoice
            SET journal_entry_id = NULL
            WHERE journal_entry_id IN (
                SELECT journal_entry_id
                FROM _migration_void_orphan_journals
            )
            """
        )
    )

    # ------------------------------------------------------------------
    # Step 4: Delete journal entries
    # ------------------------------------------------------------------
    conn.execute(
        sa_text(
            """
            DELETE FROM gl.journal_entry
            WHERE journal_entry_id IN (
                SELECT journal_entry_id
                FROM _migration_void_orphan_journals
            )
            """
        )
    )


def downgrade() -> None:
    # Journal deletion is irreversible. The tracking table
    # _migration_void_orphan_journals is preserved for audit.
    # To restore, reimport from ERPNext source system.
    pass
