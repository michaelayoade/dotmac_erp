"""Add confirmation state to bank_statement_line_matches (suggest-only model).

Bank reconciliation is moving to a suggest-only model: the matcher/auto-engine
write *suggested* matches and a human confirms each one. Only confirmed matches
set ``BankStatementLine.is_matched`` and count toward a reconciliation.

Adds ``match_state`` (suggested|confirmed), ``confirmed_at`` and ``confirmed_by``
to ``banking.bank_statement_line_matches``. Existing rows are historical
auto-confirmed matches, so they backfill to ``confirmed`` via the server default.

Idempotent: uses ADD COLUMN IF NOT EXISTS.

Revision ID: 20260603_add_match_state
Revises: 20260603_repair_self_parent_customers
Create Date: 2026-06-03
"""

from __future__ import annotations

from alembic import op

revision = "20260603_add_match_state"
down_revision = "20260603_repair_self_parent_customers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE banking.bank_statement_line_matches
            ADD COLUMN IF NOT EXISTS match_state VARCHAR(20)
                NOT NULL DEFAULT 'confirmed';
        ALTER TABLE banking.bank_statement_line_matches
            ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ NULL;
        ALTER TABLE banking.bank_statement_line_matches
            ADD COLUMN IF NOT EXISTS confirmed_by UUID NULL;
        """
    )
    # Existing matches are already-confirmed history; stamp confirmed_at so the
    # audit trail is complete (use matched_at as the best-available timestamp).
    # Bypass RLS so the backfill reaches rows in every organization.
    op.execute("SET LOCAL app.bypass_rls = 'true'")
    op.execute(
        """
        UPDATE banking.bank_statement_line_matches
        SET confirmed_at = matched_at
        WHERE match_state = 'confirmed' AND confirmed_at IS NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_stmt_line_match_state
            ON banking.bank_statement_line_matches (match_state);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS banking.ix_stmt_line_match_state;
        ALTER TABLE banking.bank_statement_line_matches
            DROP COLUMN IF EXISTS confirmed_by;
        ALTER TABLE banking.bank_statement_line_matches
            DROP COLUMN IF EXISTS confirmed_at;
        ALTER TABLE banking.bank_statement_line_matches
            DROP COLUMN IF EXISTS match_state;
        """
    )
