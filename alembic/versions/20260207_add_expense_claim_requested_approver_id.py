"""Add requested approver to expense claims.

Revision ID: 20260207_add_expense_claim_requested_approver_id
Revises: 20260207_expense_claim_composite_unique, 20260207_add_customer_default_tax_code_id, add_cancel_resubmit_actions
Create Date: 2026-02-07
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260207_add_expense_claim_requested_approver_id"
down_revision = (
    "20260207_expense_claim_composite_unique",
    "20260207_add_customer_default_tax_code_id",
    "add_cancel_resubmit_actions",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        col["name"] for col in inspector.get_columns("expense_claim", schema="expense")
    }
    if "requested_approver_id" not in columns:
        op.add_column(
            "expense_claim",
            sa.Column(
                "requested_approver_id", sa.dialects.postgresql.UUID(), nullable=True
            ),
            schema="expense",
        )
        op.execute(
            """
            ALTER TABLE expense.expense_claim
            ADD CONSTRAINT fk_expense_claim_requested_approver
            FOREIGN KEY (requested_approver_id)
            REFERENCES hr.employee(employee_id)
            ON DELETE SET NULL
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        col["name"] for col in inspector.get_columns("expense_claim", schema="expense")
    }
    if "requested_approver_id" in columns:
        op.execute(
            "ALTER TABLE expense.expense_claim DROP CONSTRAINT IF EXISTS fk_expense_claim_requested_approver"
        )
        op.drop_column("expense_claim", "requested_approver_id", schema="expense")
