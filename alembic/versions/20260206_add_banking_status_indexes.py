"""Add status indexes to banking tables.

Revision ID: 20260206_add_banking_status_indexes
Revises: 20260206_add_offer_portal_token
Create Date: 2026-02-06
"""

import sqlalchemy as sa

from alembic import op

revision = "20260206_add_banking_status_indexes"
down_revision = "20260206_add_offer_portal_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # bank_accounts: add (organization_id, status) index for filtered list queries
    if inspector.has_table("bank_accounts", schema="banking"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("bank_accounts", schema="banking")
        }
        if "ix_bank_accounts_org_status" not in indexes:
            op.create_index(
                "ix_bank_accounts_org_status",
                "bank_accounts",
                ["organization_id", "status"],
                schema="banking",
            )

    # bank_statements: add (organization_id, status) index for filtered list queries
    if inspector.has_table("bank_statements", schema="banking"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("bank_statements", schema="banking")
        }
        if "ix_bank_statements_org_status" not in indexes:
            op.create_index(
                "ix_bank_statements_org_status",
                "bank_statements",
                ["organization_id", "status"],
                schema="banking",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("bank_statements", schema="banking"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("bank_statements", schema="banking")
        }
        if "ix_bank_statements_org_status" in indexes:
            op.drop_index(
                "ix_bank_statements_org_status",
                table_name="bank_statements",
                schema="banking",
            )

    if inspector.has_table("bank_accounts", schema="banking"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("bank_accounts", schema="banking")
        }
        if "ix_bank_accounts_org_status" in indexes:
            op.drop_index(
                "ix_bank_accounts_org_status",
                table_name="bank_accounts",
                schema="banking",
            )
