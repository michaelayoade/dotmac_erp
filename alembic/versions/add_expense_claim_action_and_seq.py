"""Add expense claim action table and claim number sequence.

Revision ID: add_expense_claim_action_seq
Revises: add_expense_cost_allocation
Create Date: 2026-01-26
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_expense_claim_action_seq"
down_revision = "create_expense_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Create enum for action types
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'expense_claim_action_type') THEN "
        "CREATE TYPE expense_claim_action_type AS ENUM ("
        "'SUBMIT','APPROVE','REJECT','MARK_PAID','LINK_ADVANCE','POST_GL','CREATE_SUPPLIER_INVOICE'"
        "); "
        "END IF; "
        "END $$;"
    )

    # Create enum for action statuses
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'expense_claim_action_status') THEN "
        "CREATE TYPE expense_claim_action_status AS ENUM ("
        "'STARTED','COMPLETED','FAILED'"
        "); "
        "END IF; "
        "END $$;"
    )

    # Create sequence for claim numbers
    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS expense.expense_claim_number_seq"
    )

    # Align sequence with existing claim numbers to avoid duplicates
    op.execute(
        "SELECT setval("
        "'expense.expense_claim_number_seq', "
        "COALESCE((SELECT MAX(CAST(split_part(claim_number, '-', 3) AS INTEGER)) "
        "FROM expense.expense_claim "
        "WHERE claim_number ~ '^EXP-[0-9]{4}-[0-9]+$'), 0)"
        ")"
    )

    # Enforce strict workflow by normalizing legacy pending approvals
    op.execute(
        "UPDATE expense.expense_claim "
        "SET status = 'SUBMITTED' "
        "WHERE status = 'PENDING_APPROVAL'"
    )

    # Create action table if missing
    if not inspector.has_table("expense_claim_action", schema="expense"):
        op.create_table(
            "expense_claim_action",
            sa.Column(
                "action_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "claim_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("expense.expense_claim.claim_id"),
                nullable=False,
            ),
            sa.Column(
                "action_type",
                sa.Text(),
                nullable=False,
            ),
            sa.Column(
                "status",
                sa.Text(),
                nullable=False,
                server_default="STARTED",
            ),
            sa.Column("action_key", sa.String(200), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "organization_id",
                "claim_id",
                "action_type",
                name="uq_expense_claim_action",
            ),
            sa.Index(
                "idx_expense_claim_action_claim",
                "claim_id",
            ),
            schema="expense",
        )
        op.execute(
            "ALTER TABLE expense.expense_claim_action "
            "ALTER COLUMN action_type TYPE expense_claim_action_type "
            "USING action_type::expense_claim_action_type"
        )
        op.execute(
            "ALTER TABLE expense.expense_claim_action "
            "ALTER COLUMN status DROP DEFAULT"
        )
        op.execute(
            "ALTER TABLE expense.expense_claim_action "
            "ALTER COLUMN status TYPE expense_claim_action_status "
            "USING status::expense_claim_action_status"
        )
        op.execute(
            "ALTER TABLE expense.expense_claim_action "
            "ALTER COLUMN status SET DEFAULT 'STARTED'::expense_claim_action_status"
        )
    else:
        columns = {col["name"] for col in inspector.get_columns("expense_claim_action", schema="expense")}
        if "action_type" in columns:
            op.execute(
                "DO $$ BEGIN "
                "IF EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'expense' "
                "AND table_name = 'expense_claim_action' "
                "AND column_name = 'action_type' "
                "AND udt_name <> 'expense_claim_action_type') THEN "
                "ALTER TABLE expense.expense_claim_action "
                "ALTER COLUMN action_type TYPE expense_claim_action_type "
                "USING action_type::expense_claim_action_type; "
                "END IF; "
                "END $$;"
            )
        if "status" not in columns:
            op.add_column(
                "expense_claim_action",
                sa.Column("status", sa.Text(), nullable=False, server_default="STARTED"),
                schema="expense",
            )
            op.execute(
                "ALTER TABLE expense.expense_claim_action "
                "ALTER COLUMN status DROP DEFAULT"
            )
            op.execute(
                "ALTER TABLE expense.expense_claim_action "
                "ALTER COLUMN status TYPE expense_claim_action_status "
                "USING status::expense_claim_action_status"
            )
            op.execute(
                "ALTER TABLE expense.expense_claim_action "
                "ALTER COLUMN status SET DEFAULT 'STARTED'::expense_claim_action_status"
            )
        else:
            op.execute(
                "DO $$ BEGIN "
                "IF EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'expense' "
                "AND table_name = 'expense_claim_action' "
                "AND column_name = 'status' "
                "AND udt_name <> 'expense_claim_action_status') THEN "
                "ALTER TABLE expense.expense_claim_action "
                "ALTER COLUMN status DROP DEFAULT; "
                "ALTER TABLE expense.expense_claim_action "
                "ALTER COLUMN status TYPE expense_claim_action_status "
                "USING status::expense_claim_action_status; "
                "END IF; "
                "END $$;"
            )
            op.execute(
                "ALTER TABLE expense.expense_claim_action "
                "ALTER COLUMN status SET DEFAULT 'STARTED'::expense_claim_action_status"
            )

    # Create sequence for expense supplier invoice numbers
    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS expense.expense_supplier_invoice_number_seq"
    )
    op.execute(
        "SELECT setval("
        "'expense.expense_supplier_invoice_number_seq', "
        "GREATEST(1, COALESCE((SELECT MAX(CAST(split_part(invoice_number, '-', 4) AS INTEGER)) "
        "FROM ap.supplier_invoice "
        "WHERE invoice_number ~ '^EXP-INV-[0-9]{4}-[0-9]+$'), 0))"
        ")"
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("expense_claim_action", schema="expense"):
        op.drop_table("expense_claim_action", schema="expense")

    op.execute("DROP SEQUENCE IF EXISTS expense.expense_claim_number_seq")
    op.execute("DROP SEQUENCE IF EXISTS expense.expense_supplier_invoice_number_seq")

    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'expense_claim_action_type') THEN "
        "DROP TYPE expense_claim_action_type; "
        "END IF; "
        "END $$;"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'expense_claim_action_status') THEN "
        "DROP TYPE expense_claim_action_status; "
        "END IF; "
        "END $$;"
    )
