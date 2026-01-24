"""Add project and ticket FKs to expense_claim.

Revision ID: add_expense_project_ticket_fk
Revises: create_support_schema
Create Date: 2026-01-23

This migration adds:
- FK constraint for project_id on expense_claim (links to Project sync'd from ERPNext)
- ticket_id column and FK on expense_claim (links to Ticket sync'd from ERPNext)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "add_expense_project_ticket_fk"
down_revision: Union[str, None] = "create_support_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add ticket_id column
    op.add_column(
        "expense_claim",
        sa.Column(
            "ticket_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="Related support ticket from ERPNext",
        ),
        schema="expense",
    )

    # Add FK constraint for ticket_id
    op.create_foreign_key(
        "fk_expense_claim_ticket",
        "expense_claim",
        "ticket",
        ["ticket_id"],
        ["ticket_id"],
        source_schema="expense",
        referent_schema="support",
    )

    # Add FK constraint for project_id (column already exists)
    op.create_foreign_key(
        "fk_expense_claim_project",
        "expense_claim",
        "project",
        ["project_id"],
        ["project_id"],
        source_schema="expense",
        referent_schema="core_org",
    )

    # Add indexes for the new FK columns
    op.create_index(
        "idx_expense_claim_ticket",
        "expense_claim",
        ["ticket_id"],
        schema="expense",
    )
    op.create_index(
        "idx_expense_claim_project",
        "expense_claim",
        ["project_id"],
        schema="expense",
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("idx_expense_claim_project", table_name="expense_claim", schema="expense")
    op.drop_index("idx_expense_claim_ticket", table_name="expense_claim", schema="expense")

    # Drop FK constraints
    op.drop_constraint("fk_expense_claim_project", "expense_claim", type_="foreignkey", schema="expense")
    op.drop_constraint("fk_expense_claim_ticket", "expense_claim", type_="foreignkey", schema="expense")

    # Drop ticket_id column
    op.drop_column("expense_claim", "ticket_id", schema="expense")
