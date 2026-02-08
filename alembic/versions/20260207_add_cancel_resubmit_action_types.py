"""Add CANCEL and RESUBMIT to expense_claim_action_type enum.

Revision ID: add_cancel_resubmit_actions
Revises: add_expense_claim_action_seq
Create Date: 2026-02-07
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_cancel_resubmit_actions"
down_revision = "add_expense_claim_action_seq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new values idempotently — ALTER TYPE ADD VALUE is safe to repeat
    # if checked first with a DO block.
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS ("
        "  SELECT 1 FROM pg_enum "
        "  WHERE enumtypid = 'expense_claim_action_type'::regtype "
        "  AND enumlabel = 'CANCEL'"
        ") THEN "
        "  ALTER TYPE expense_claim_action_type ADD VALUE 'CANCEL'; "
        "END IF; "
        "END $$;"
    )

    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS ("
        "  SELECT 1 FROM pg_enum "
        "  WHERE enumtypid = 'expense_claim_action_type'::regtype "
        "  AND enumlabel = 'RESUBMIT'"
        ") THEN "
        "  ALTER TYPE expense_claim_action_type ADD VALUE 'RESUBMIT'; "
        "END IF; "
        "END $$;"
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values.
    # The CANCEL and RESUBMIT values will remain in the type.
    pass
