"""Add expense GL integration fields.

Add journal_entry_id to expense_claim table for direct GL posting.
Add indexes for better query performance.

Revision ID: 20260124_expense_gl
Revises:
Create Date: 2026-01-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "20260124_expense_gl"
down_revision = "799a0ecebdd4"  # Fixed: connect to initial schema
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add journal_entry_id column and indexes to expense_claim table."""
    # Add journal_entry_id column
    op.add_column(
        "expense_claim",
        sa.Column(
            "journal_entry_id",
            UUID(as_uuid=True),
            sa.ForeignKey("gl.journal_entry.journal_entry_id"),
            nullable=True,
            comment="GL entry for expense posting",
        ),
        schema="expense",
    )

    # Add indexes for better query performance
    op.create_index(
        "idx_expense_claim_journal",
        "expense_claim",
        ["journal_entry_id"],
        schema="expense",
    )

    # Add index on supplier_invoice_id if it doesn't exist
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_expense_claim_supplier_invoice "
        "ON expense.expense_claim (supplier_invoice_id)"
    )


def downgrade() -> None:
    """Remove journal_entry_id column and indexes."""
    # Drop indexes
    op.drop_index(
        "idx_expense_claim_journal",
        table_name="expense_claim",
        schema="expense",
    )

    op.execute("DROP INDEX IF EXISTS expense.idx_expense_claim_supplier_invoice")

    # Drop column
    op.drop_column("expense_claim", "journal_entry_id", schema="expense")
