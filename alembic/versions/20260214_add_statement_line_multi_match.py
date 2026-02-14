"""Add bank_statement_line_matches junction table for multi-match.

Revision ID: 20260214_add_statement_line_multi_match
Revises: 20260213_add_purchase_order_crm_entity_type
Create Date: 2026-02-14

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260214_add_statement_line_multi_match"
down_revision = "20260213_add_purchase_order_crm_entity_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if table already exists (idempotent)
    existing_tables = inspector.get_table_names(schema="banking")
    if "bank_statement_line_matches" in existing_tables:
        return

    op.create_table(
        "bank_statement_line_matches",
        sa.Column("match_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "statement_line_id",
            UUID(as_uuid=True),
            sa.ForeignKey("banking.bank_statement_lines.line_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("journal_line_id", UUID(as_uuid=True), nullable=False),
        sa.Column("match_score", sa.Numeric(5, 1), nullable=True),
        sa.Column(
            "matched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("matched_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "is_primary",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.UniqueConstraint(
            "statement_line_id",
            "journal_line_id",
            name="uq_statement_line_journal_line",
        ),
        schema="banking",
    )

    op.create_index(
        "ix_stmt_line_match_line_id",
        "bank_statement_line_matches",
        ["statement_line_id"],
        schema="banking",
    )

    # Backfill: copy existing 1:1 matches into the junction table
    op.execute(
        sa.text("""
            INSERT INTO banking.bank_statement_line_matches
                (match_id, statement_line_id, journal_line_id, matched_at, matched_by, is_primary)
            SELECT
                gen_random_uuid(),
                line_id,
                matched_journal_line_id,
                COALESCE(matched_at, NOW()),
                matched_by,
                true
            FROM banking.bank_statement_lines
            WHERE matched_journal_line_id IS NOT NULL
              AND is_matched = true
        """)
    )


def downgrade() -> None:
    op.drop_index(
        "ix_stmt_line_match_line_id",
        table_name="bank_statement_line_matches",
        schema="banking",
    )
    op.drop_table("bank_statement_line_matches", schema="banking")
