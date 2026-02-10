"""Add composite unique constraint to expense claims.

Revision ID: 20260207_expense_claim_composite_unique
Revises: 20260207_add_expense_claim_recipient_name
Create Date: 2026-02-07
"""

import sqlalchemy as sa

from alembic import op

revision = "20260207_expense_claim_composite_unique"
down_revision = "20260207_add_expense_claim_recipient_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("expense_claim", schema="expense"):
        return

    uniques = {
        uc["name"]
        for uc in inspector.get_unique_constraints("expense_claim", schema="expense")
        if uc.get("name")
    }
    if "uq_expense_claim_org_number" not in uniques:
        op.create_unique_constraint(
            "uq_expense_claim_org_number",
            "expense_claim",
            ["organization_id", "claim_number"],
            schema="expense",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("expense_claim", schema="expense"):
        return

    uniques = {
        uc["name"]
        for uc in inspector.get_unique_constraints("expense_claim", schema="expense")
        if uc.get("name")
    }
    if "uq_expense_claim_org_number" in uniques:
        op.drop_constraint(
            "uq_expense_claim_org_number",
            "expense_claim",
            schema="expense",
            type_="unique",
        )
