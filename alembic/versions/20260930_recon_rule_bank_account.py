"""Scope reconciliation match rules to a selected bank account.

Revision ID: 20260930_recon_rule_bank_account
Revises: 20260929_email_delivery_payload
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260930_recon_rule_bank_account"
down_revision = "20260930_orphan_evidence_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_match_rule",
        sa.Column(
            "bank_account_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        schema="banking",
    )
    op.create_foreign_key(
        "fk_recon_match_rule_bank_account",
        "reconciliation_match_rule",
        "bank_accounts",
        ["bank_account_id"],
        ["bank_account_id"],
        source_schema="banking",
        referent_schema="banking",
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_recon_match_rule_bank_account",
        "reconciliation_match_rule",
        ["bank_account_id"],
        schema="banking",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recon_match_rule_bank_account",
        table_name="reconciliation_match_rule",
        schema="banking",
    )
    op.drop_constraint(
        "fk_recon_match_rule_bank_account",
        "reconciliation_match_rule",
        schema="banking",
        type_="foreignkey",
    )
    op.drop_column(
        "reconciliation_match_rule",
        "bank_account_id",
        schema="banking",
    )
