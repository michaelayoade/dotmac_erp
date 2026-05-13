"""Add explicit Mono transaction sync status.

Revision ID: 20260506_mono_txn_status
Revises: 20260506_form_label_text
Create Date: 2026-05-06
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op


revision = "20260506_mono_txn_status"
down_revision = "20260506_form_label_text"
branch_labels = None
depends_on = None


def _column_exists(conn: sa.engine.Connection, name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'banking' "
            "AND table_name = 'bank_accounts' "
            "AND column_name = :name"
        ),
        {"name": name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "mono_transaction_sync_status"):
        op.add_column(
            "bank_accounts",
            sa.Column(
                "mono_transaction_sync_status",
                sa.String(length=30),
                nullable=False,
                server_default="never",
            ),
            schema="banking",
        )
    if not _column_exists(conn, "mono_last_transaction_sync_at"):
        op.add_column(
            "bank_accounts",
            sa.Column(
                "mono_last_transaction_sync_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            schema="banking",
        )

    conn.execute(
        sa.text(
            """
            UPDATE banking.bank_accounts AS account
            SET mono_transaction_sync_status = CASE
                    WHEN account.mono_last_sync_error ILIKE '%retrieved%not transactions%'
                        THEN 'provider_limited'
                    WHEN account.mono_last_sync_error IS NOT NULL
                        THEN 'failed'
                    WHEN mono_lines.last_imported_at IS NOT NULL
                        THEN 'healthy'
                    WHEN account.mono_last_synced_at IS NOT NULL
                        THEN 'healthy'
                    ELSE 'never'
                END,
                mono_last_transaction_sync_at = CASE
                    WHEN account.mono_last_sync_error IS NULL
                        THEN COALESCE(mono_lines.last_imported_at, account.mono_last_synced_at)
                    ELSE NULL
                END
            FROM (
                SELECT
                    statement.bank_account_id,
                    max(line.created_at) AS last_imported_at
                FROM banking.bank_statement_lines AS line
                JOIN banking.bank_statements AS statement
                    ON statement.statement_id = line.statement_id
                WHERE line.transaction_id LIKE 'mono_%'
                GROUP BY statement.bank_account_id
            ) AS mono_lines
            WHERE account.bank_account_id = mono_lines.bank_account_id
              AND account.mono_account_id IS NOT NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE banking.bank_accounts AS account
            SET mono_transaction_sync_status = CASE
                    WHEN account.mono_last_sync_error ILIKE '%retrieved%not transactions%'
                        THEN 'provider_limited'
                    WHEN account.mono_last_sync_error IS NOT NULL
                        THEN 'failed'
                    WHEN account.mono_last_synced_at IS NOT NULL
                        THEN 'healthy'
                    ELSE 'never'
                END,
                mono_last_transaction_sync_at = CASE
                    WHEN account.mono_last_sync_error IS NULL
                        THEN account.mono_last_synced_at
                    ELSE NULL
                END
            WHERE account.mono_account_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM banking.bank_statements AS statement
                  JOIN banking.bank_statement_lines AS line
                    ON line.statement_id = statement.statement_id
                  WHERE statement.bank_account_id = account.bank_account_id
                    AND line.transaction_id LIKE 'mono_%'
              )
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "mono_last_transaction_sync_at"):
        op.drop_column(
            "bank_accounts", "mono_last_transaction_sync_at", schema="banking"
        )
    if _column_exists(conn, "mono_transaction_sync_status"):
        op.drop_column(
            "bank_accounts", "mono_transaction_sync_status", schema="banking"
        )
