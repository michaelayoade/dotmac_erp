"""Add reimbursement_journal_id to expense_claim.

Revision ID: 20260124_expense_reimb_journal
Revises: 20260124_expense_task_fk
Create Date: 2026-01-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260124_expense_reimb_journal"
down_revision = "20260124_expense_task_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "expense_claim",
        sa.Column(
            "reimbursement_journal_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="GL entry for reimbursement payment",
        ),
        schema="expense",
    )

    op.create_foreign_key(
        "fk_expense_claim_reimbursement_journal",
        "expense_claim",
        "journal_entry",
        ["reimbursement_journal_id"],
        ["journal_entry_id"],
        source_schema="expense",
        referent_schema="gl",
    )

    op.create_index(
        "idx_expense_claim_reimbursement_journal",
        "expense_claim",
        ["reimbursement_journal_id"],
        schema="expense",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_expense_claim_reimbursement_journal",
        table_name="expense_claim",
        schema="expense",
    )
    op.drop_constraint(
        "fk_expense_claim_reimbursement_journal",
        "expense_claim",
        type_="foreignkey",
        schema="expense",
    )
    op.drop_column("expense_claim", "reimbursement_journal_id", schema="expense")
