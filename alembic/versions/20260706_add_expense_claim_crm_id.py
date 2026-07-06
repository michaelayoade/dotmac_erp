"""Add DotMac CRM sync columns to expense.expense_claim.

Adds ``crm_id`` (CRM expense-request omni_id) for idempotent CRM → ERP
expense-claim sync, plus the composite unique constraint and lookup index.
``last_synced_at`` already exists on the table (ERPNextSyncMixin /
create_expense_tables) — the add here is a defensive no-op for older
databases.

Revision ID: 20260706_add_expense_claim_crm_id
Revises: 20260705_add_apikey_scopes
Create Date: 2026-07-06
"""

import sqlalchemy as sa

from alembic import op

revision = "20260706_add_expense_claim_crm_id"
down_revision = "20260705_add_apikey_scopes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("expense_claim", schema="expense"):
        return

    columns = {
        c["name"] for c in inspector.get_columns("expense_claim", schema="expense")
    }
    if "crm_id" not in columns:
        op.add_column(
            "expense_claim",
            sa.Column(
                "crm_id",
                sa.String(36),
                nullable=True,
                comment="DotMac CRM expense request ID (omni_id for idempotency)",
            ),
            schema="expense",
        )
    if "last_synced_at" not in columns:
        op.add_column(
            "expense_claim",
            sa.Column(
                "last_synced_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="Last synchronization timestamp",
            ),
            schema="expense",
        )

    constraints = {
        c.get("name")
        for c in inspector.get_unique_constraints("expense_claim", schema="expense")
    }
    if "uq_expense_claim_org_crm_id" not in constraints:
        op.create_unique_constraint(
            "uq_expense_claim_org_crm_id",
            "expense_claim",
            ["organization_id", "crm_id"],
            schema="expense",
        )

    indexes = {
        idx["name"] for idx in inspector.get_indexes("expense_claim", schema="expense")
    }
    if "idx_expense_claim_crm_id" not in indexes:
        op.create_index(
            "idx_expense_claim_crm_id",
            "expense_claim",
            ["crm_id"],
            schema="expense",
            postgresql_where=sa.text("crm_id IS NOT NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("expense_claim", schema="expense"):
        return

    indexes = {
        idx["name"] for idx in inspector.get_indexes("expense_claim", schema="expense")
    }
    if "idx_expense_claim_crm_id" in indexes:
        op.drop_index(
            "idx_expense_claim_crm_id",
            table_name="expense_claim",
            schema="expense",
        )

    constraints = {
        c.get("name")
        for c in inspector.get_unique_constraints("expense_claim", schema="expense")
    }
    if "uq_expense_claim_org_crm_id" in constraints:
        op.drop_constraint(
            "uq_expense_claim_org_crm_id",
            "expense_claim",
            schema="expense",
            type_="unique",
        )

    columns = {
        c["name"] for c in inspector.get_columns("expense_claim", schema="expense")
    }
    if "crm_id" in columns:
        op.drop_column("expense_claim", "crm_id", schema="expense")
    # last_synced_at predates this revision (create_expense_tables) — leave it.
