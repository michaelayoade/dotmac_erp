"""Add GL balance check trigger on journal_entry status transition.

Adds a database-level trigger that prevents a journal_entry from
being set to POSTED if its lines do not balance (debit != credit).
This is a safety net backing the application-level validation in
LedgerPostingService._validate_balance().

Also adds a partial index on journal_entry for approved-but-unposted
invoices, which the auto-post task uses.

Revision ID: 20260218_add_gl_balance_check
Revises: 20260218_add_audit_indexes_drop_unused
Create Date: 2026-02-18
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260218_add_gl_balance_check"
down_revision = "20260218_add_audit_indexes_drop_unused"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Database-level balance check trigger
    op.execute("""
        CREATE OR REPLACE FUNCTION gl.check_journal_balance()
        RETURNS TRIGGER AS $$
        DECLARE
            total_debit  NUMERIC;
            total_credit NUMERIC;
        BEGIN
            -- Only check when status is being set to POSTED
            IF NEW.status = 'POSTED' AND (OLD.status IS NULL OR OLD.status != 'POSTED') THEN
                SELECT
                    COALESCE(SUM(COALESCE(debit_amount_functional, debit_amount, 0)), 0),
                    COALESCE(SUM(COALESCE(credit_amount_functional, credit_amount, 0)), 0)
                INTO total_debit, total_credit
                FROM gl.journal_entry_line
                WHERE journal_entry_id = NEW.journal_entry_id;

                IF ABS(total_debit - total_credit) > 0.01 THEN
                    RAISE EXCEPTION 'Cannot post unbalanced journal %: debit=%, credit=%',
                        NEW.journal_number, total_debit, total_credit;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS trg_check_journal_balance ON gl.journal_entry;
    """)

    op.execute("""
        CREATE TRIGGER trg_check_journal_balance
        BEFORE UPDATE ON gl.journal_entry
        FOR EACH ROW
        EXECUTE FUNCTION gl.check_journal_balance();
    """)

    # Partial index for approved invoices (used by auto-post task)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ar_invoice_approved
        ON ar.invoice (updated_at)
        WHERE status = 'APPROVED'
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_check_journal_balance ON gl.journal_entry")
    op.execute("DROP FUNCTION IF EXISTS gl.check_journal_balance()")
    op.execute("DROP INDEX IF EXISTS ar.idx_ar_invoice_approved")
