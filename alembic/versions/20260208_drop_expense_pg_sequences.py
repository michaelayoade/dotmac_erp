"""Drop raw PostgreSQL sequences used by expense numbering.

These are no longer needed now that expense numbering goes through
SyncNumberingService and the core_config.numbering_sequence table.

Revision ID: 20260208_drop_expense_pg_sequences
Revises: 20260208_bootstrap_numbering_counters
Create Date: 2026-02-08
"""

from alembic import op

revision = "20260208_drop_expense_pg_sequences"
down_revision = "20260208_bootstrap_numbering_counters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS expense.expense_claim_number_seq")
    op.execute("DROP SEQUENCE IF EXISTS expense.expense_supplier_invoice_number_seq")


def downgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS expense.expense_claim_number_seq START 1")
    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS expense.expense_supplier_invoice_number_seq START 1"
    )
