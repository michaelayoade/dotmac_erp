"""Persist stable Sub expense-line identities for approval v4.

Revision ID: 20260921_sub_expense_approval_v4
Revises: 20260921_calendar_recipient_targets
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260921_sub_expense_approval_v4"
down_revision = "20260921_calendar_recipient_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "expense_claim_item",
        sa.Column(
            "source_line_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Stable source-system line identity for deterministic decisions",
        ),
        schema="expense",
    )
    op.create_unique_constraint(
        "uq_expense_claim_item_org_claim_source_line",
        "expense_claim_item",
        ["organization_id", "claim_id", "source_line_id"],
        schema="expense",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_expense_claim_item_org_claim_source_line",
        "expense_claim_item",
        type_="unique",
        schema="expense",
    )
    op.drop_column("expense_claim_item", "source_line_id", schema="expense")
