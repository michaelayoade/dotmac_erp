"""Delete voided ERPNext duplicate invoices and their GL entries.

The ERPNext/Splynx billing sync created duplicate invoices — ERPNext copies
are 1 day ahead of the Splynx originals (same customer, same amount). The sync
voided 22,975 ERPNext copies but never deleted them. This migration removes:
- ~22,975 VOID invoices (erpnext_id set, splynx_id NULL)
- ~24,532 invoice lines
- ~22,628 journal entries (VOID + POSTED)
- ~22,628+ journal entry lines
- ~10,157 posted ledger lines (NGN 848M debit/credit)

A tracking table `_migration_void_erpnext_invoices` is created for audit.
Account balances for the 13 affected fiscal periods are marked stale.

Revision ID: 20260301_delete_voided_erpnext_invoices
Revises: 20260227_add_shift_pattern_lines
Create Date: 2026-03-01
"""

from sqlalchemy import text as sa_text

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260301_delete_voided_erpnext_invoices"
down_revision = "20260227_add_shift_pattern_lines"
branch_labels = None
depends_on = None

# The single org affected by the ERPNext/Splynx sync duplication
ORG_ID = "00000000-0000-0000-0000-000000000001"

# 13 fiscal periods spanning Jan 2025 — Jan 2026
AFFECTED_PERIOD_IDS = [
    "9dd78ba6-a02d-4554-bd2b-fb98d7aa05be",  # Jan 2025
    "36acbe73-d949-475d-beab-6bbe556e57a0",  # Feb 2025
    "553aa0d1-f11e-4ce3-b1b7-a9da9b1e7251",  # Mar 2025
    "c7239527-fe4a-4d6d-a58a-2bc11af2959b",  # Apr 2025
    "15633447-3702-4983-8ef5-33d05db47c82",  # May 2025
    "ea5e26cd-2a11-4e07-b71f-a5ca442b4530",  # Jun 2025
    "7403406d-5c4a-41ed-8fb0-59d508ee7a27",  # Jul 2025
    "4714e7d4-b06b-46d0-b9e2-13be515e0f57",  # Aug 2025
    "5eef793a-4dba-4595-9ad7-56a02e9780e8",  # Sep 2025
    "3d7bdf66-e04b-4240-98be-f8ab41a16944",  # Oct 2025
    "b7fb70a9-4e54-4f27-aaa5-da73293cf5b8",  # Nov 2025
    "24b4727d-ca2e-48b5-8925-401098fdc2bb",  # Dec 2025
    "09c14950-61e4-4504-88d7-ba1ec8f8cc88",  # Jan 2026
]


def upgrade() -> None:
    conn = op.get_bind()

    # Step 0: Create tracking table (idempotent)
    conn.execute(
        sa_text(
            """
            CREATE TABLE IF NOT EXISTS _migration_void_erpnext_invoices (
                invoice_id UUID PRIMARY KEY,
                journal_entry_id UUID,
                invoice_number TEXT,
                total_amount NUMERIC(15,2)
            )
            """
        )
    )

    # Step 1: Record affected invoices (skip duplicates)
    conn.execute(
        sa_text(
            """
            INSERT INTO _migration_void_erpnext_invoices
                (invoice_id, journal_entry_id, invoice_number, total_amount)
            SELECT invoice_id, journal_entry_id, invoice_number, total_amount
            FROM ar.invoice
            WHERE status = 'VOID'
              AND erpnext_id IS NOT NULL
              AND splynx_id IS NULL
              AND organization_id = :org_id
            ON CONFLICT DO NOTHING
            """
        ),
        {"org_id": ORG_ID},
    )

    # Step 2: Delete posted_ledger_line for affected journals
    conn.execute(
        sa_text(
            """
            DELETE FROM gl.posted_ledger_line
            WHERE journal_entry_id IN (
                SELECT journal_entry_id
                FROM _migration_void_erpnext_invoices
                WHERE journal_entry_id IS NOT NULL
            )
            """
        )
    )

    # Step 3: Delete journal_entry_line for affected journals
    conn.execute(
        sa_text(
            """
            DELETE FROM gl.journal_entry_line
            WHERE journal_entry_id IN (
                SELECT journal_entry_id
                FROM _migration_void_erpnext_invoices
                WHERE journal_entry_id IS NOT NULL
            )
            """
        )
    )

    # Step 4: NULL out invoice FK to journal (break circular reference)
    conn.execute(
        sa_text(
            """
            UPDATE ar.invoice SET journal_entry_id = NULL
            WHERE invoice_id IN (
                SELECT invoice_id FROM _migration_void_erpnext_invoices
            )
            """
        )
    )

    # Step 5: Delete journal entries
    conn.execute(
        sa_text(
            """
            DELETE FROM gl.journal_entry
            WHERE journal_entry_id IN (
                SELECT journal_entry_id
                FROM _migration_void_erpnext_invoices
                WHERE journal_entry_id IS NOT NULL
            )
            """
        )
    )

    # Step 6: Delete invoice lines
    conn.execute(
        sa_text(
            """
            DELETE FROM ar.invoice_line
            WHERE invoice_id IN (
                SELECT invoice_id FROM _migration_void_erpnext_invoices
            )
            """
        )
    )

    # Step 7: Delete invoices
    conn.execute(
        sa_text(
            """
            DELETE FROM ar.invoice
            WHERE invoice_id IN (
                SELECT invoice_id FROM _migration_void_erpnext_invoices
            )
            """
        )
    )

    # Step 8: Mark account_balance stale for affected periods
    conn.execute(
        sa_text(
            """
            UPDATE gl.account_balance
            SET is_stale = true, stale_since = NOW()
            WHERE fiscal_period_id = ANY(:period_ids)
              AND organization_id = :org_id
            """
        ),
        {"period_ids": AFFECTED_PERIOD_IDS, "org_id": ORG_ID},
    )


def downgrade() -> None:
    # Data deletion is irreversible. The tracking table
    # _migration_void_erpnext_invoices is preserved for audit.
    # To restore, reimport from ERPNext source system.
    pass
