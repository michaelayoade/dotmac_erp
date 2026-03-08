"""Add org-scoped banking reconciliation policy profiles.

Revision ID: 20260308_add_banking_recon_policy_profiles
Revises: 20260308_rebind_expense_hr_fks
Create Date: 2026-03-08
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260308_add_banking_recon_policy_profiles"
down_revision: Union[str, None] = "20260308_rebind_expense_hr_fks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_policy_profile",
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "enabled_provider_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "enabled_strategy_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "decision_thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "keyword_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "gl_mapping_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("amount_tolerance_cents", sa.Integer(), nullable=True),
        sa.Column("date_buffer_days", sa.Integer(), nullable=True),
        sa.Column("settlement_window_days", sa.Integer(), nullable=True),
        sa.Column(
            "journal_creation_strategy_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "auto_post_strategy_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("policy_id"),
        sa.UniqueConstraint(
            "organization_id",
            "name",
            name="uq_reconciliation_policy_profile_org_name",
        ),
        schema="banking",
    )
    op.create_index(
        "ix_reconciliation_policy_profile_org_active",
        "reconciliation_policy_profile",
        ["organization_id", "is_active"],
        unique=False,
        schema="banking",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reconciliation_policy_profile_org_active",
        table_name="reconciliation_policy_profile",
        schema="banking",
    )
    op.drop_table("reconciliation_policy_profile", schema="banking")

