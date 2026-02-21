"""Add columns for expense claim approval corrections.

Stores original values (category, description, amount) so approvers can fix
minor errors without rejecting the entire claim. Also adds approval_notes
on the parent claim.

Revision ID: 20260221_add_expense_approve_corrections
Revises: 20260219_add_ap_invoice_wht_code
Create Date: 2026-02-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260221_add_expense_approve_corrections"
down_revision = "20260219_add_ap_invoice_wht_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # -- expense_claim: approval_notes --
    claim_cols = {
        c["name"] for c in inspector.get_columns("expense_claim", schema="expense")
    }
    if "approval_notes" not in claim_cols:
        op.add_column(
            "expense_claim",
            sa.Column("approval_notes", sa.Text(), nullable=True),
            schema="expense",
        )

    # -- expense_claim_item: correction-tracking columns --
    item_cols = {
        c["name"] for c in inspector.get_columns("expense_claim_item", schema="expense")
    }

    if "original_category_id" not in item_cols:
        op.add_column(
            "expense_claim_item",
            sa.Column(
                "original_category_id",
                UUID(as_uuid=True),
                nullable=True,
                comment="Snapshot of category_id before approval correction",
            ),
            schema="expense",
        )

    if "original_description" not in item_cols:
        op.add_column(
            "expense_claim_item",
            sa.Column(
                "original_description",
                sa.String(500),
                nullable=True,
                comment="Snapshot of description before approval correction",
            ),
            schema="expense",
        )

    if "original_claimed_amount" not in item_cols:
        op.add_column(
            "expense_claim_item",
            sa.Column(
                "original_claimed_amount",
                sa.Numeric(12, 2),
                nullable=True,
                comment="Snapshot of claimed_amount before approval correction",
            ),
            schema="expense",
        )

    if "was_corrected" not in item_cols:
        op.add_column(
            "expense_claim_item",
            sa.Column(
                "was_corrected",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
                comment="True if approver modified this item during approval",
            ),
            schema="expense",
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    item_cols = {
        c["name"] for c in inspector.get_columns("expense_claim_item", schema="expense")
    }
    for col in (
        "was_corrected",
        "original_claimed_amount",
        "original_description",
        "original_category_id",
    ):
        if col in item_cols:
            op.drop_column("expense_claim_item", col, schema="expense")

    claim_cols = {
        c["name"] for c in inspector.get_columns("expense_claim", schema="expense")
    }
    if "approval_notes" in claim_cols:
        op.drop_column("expense_claim", "approval_notes", schema="expense")
