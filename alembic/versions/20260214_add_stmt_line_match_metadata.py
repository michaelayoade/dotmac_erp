"""Add metadata fields to bank_statement_line_matches for contra tracking.

Revision ID: 20260214_add_stmt_line_match_metadata
Revises: 20260214_add_statement_line_multi_match
Create Date: 2026-02-14
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260214_add_stmt_line_match_metadata"
down_revision = "20260214_add_statement_line_multi_match"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    columns = {
        col["name"]
        for col in inspector.get_columns(
            "bank_statement_line_matches",
            schema="banking",
        )
    }

    if "match_type" not in columns:
        op.add_column(
            "bank_statement_line_matches",
            sa.Column("match_type", sa.String(length=30), nullable=True),
            schema="banking",
        )
    if "match_group_id" not in columns:
        op.add_column(
            "bank_statement_line_matches",
            sa.Column("match_group_id", UUID(as_uuid=True), nullable=True),
            schema="banking",
        )
    if "match_reason" not in columns:
        op.add_column(
            "bank_statement_line_matches",
            sa.Column("match_reason", JSONB(astext_type=sa.Text()), nullable=True),
            schema="banking",
        )
    if "idempotency_key" not in columns:
        op.add_column(
            "bank_statement_line_matches",
            sa.Column("idempotency_key", sa.String(length=200), nullable=True),
            schema="banking",
        )

    existing_unique = {
        uc["name"]
        for uc in inspector.get_unique_constraints(
            "bank_statement_line_matches",
            schema="banking",
        )
        if uc.get("name")
    }
    if "uq_stmt_line_match_idempotency_key" not in existing_unique:
        op.create_unique_constraint(
            "uq_stmt_line_match_idempotency_key",
            "bank_statement_line_matches",
            ["idempotency_key"],
            schema="banking",
        )

    # Backfill legacy rows as generic AUTO matches for analytics/audit.
    op.execute(
        sa.text("""
            UPDATE banking.bank_statement_line_matches
            SET match_type = COALESCE(match_type, 'AUTO')
            WHERE match_type IS NULL
        """)
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    existing_unique = {
        uc["name"]
        for uc in inspector.get_unique_constraints(
            "bank_statement_line_matches",
            schema="banking",
        )
        if uc.get("name")
    }
    if "uq_stmt_line_match_idempotency_key" in existing_unique:
        op.drop_constraint(
            "uq_stmt_line_match_idempotency_key",
            "bank_statement_line_matches",
            schema="banking",
            type_="unique",
        )

    columns = {
        col["name"]
        for col in inspector.get_columns(
            "bank_statement_line_matches",
            schema="banking",
        )
    }
    if "idempotency_key" in columns:
        op.drop_column(
            "bank_statement_line_matches",
            "idempotency_key",
            schema="banking",
        )
    if "match_reason" in columns:
        op.drop_column(
            "bank_statement_line_matches",
            "match_reason",
            schema="banking",
        )
    if "match_group_id" in columns:
        op.drop_column(
            "bank_statement_line_matches",
            "match_group_id",
            schema="banking",
        )
    if "match_type" in columns:
        op.drop_column(
            "bank_statement_line_matches",
            "match_type",
            schema="banking",
        )
