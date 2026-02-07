"""Replace global claim_number unique constraint with composite (org_id, claim_number).

Revision ID: 20260207_expense_claim_composite_unique
Revises: 20260207_add_customer_vat_category
Create Date: 2026-02-07
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260207_expense_claim_composite_unique"
down_revision = "20260207_add_customer_default_tax_code_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("expense_claim", schema="expense"):
        return

    # Check existing constraints
    uq_constraints = inspector.get_unique_constraints("expense_claim", schema="expense")
    uq_names = {c["name"] for c in uq_constraints}

    # Drop old single-column unique constraint on claim_number
    if "expense_claim_claim_number_key" in uq_names:
        op.drop_constraint(
            "expense_claim_claim_number_key",
            "expense_claim",
            schema="expense",
            type_="unique",
        )

    # Add composite unique constraint (organization_id, claim_number)
    if "uq_expense_claim_org_number" not in uq_names:
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

    uq_constraints = inspector.get_unique_constraints("expense_claim", schema="expense")
    uq_names = {c["name"] for c in uq_constraints}

    if "uq_expense_claim_org_number" in uq_names:
        op.drop_constraint(
            "uq_expense_claim_org_number",
            "expense_claim",
            schema="expense",
            type_="unique",
        )

    if "expense_claim_claim_number_key" not in uq_names:
        op.create_unique_constraint(
            "expense_claim_claim_number_key",
            "expense_claim",
            ["claim_number"],
            schema="expense",
        )
