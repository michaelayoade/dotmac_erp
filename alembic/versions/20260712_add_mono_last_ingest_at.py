"""Add bank_accounts.mono_last_ingest_at.

``mono_last_synced_at`` records only that Mono's API answered. Because
``/v2/accounts/{id}/transactions`` serves Mono's *indexed cache*, a
de-authorised account still returns 200 with zero rows — so that column
cannot distinguish "the link is alive" from "the link is dead and we read
a stale cache".

``mono_last_ingest_at`` records evidence that data actually flowed:
statement lines were written, or Mono's indexer reported a healthy pull
that included transactions. The webhook-failure suppression window keys on
this column, so a failure webhook can no longer be discarded as "stale"
just because we happened to ping Mono 30 seconds earlier.

Backfilled from ``mono_last_synced_at`` for accounts that already hold Mono
statement lines — for those the two columns had the same meaning in
practice, and seeding NULL would make every healthy account look as though
it had never ingested.

Revision ID: 20260712_add_mono_last_ingest_at
Revises: 20260712_supplier_invoice_source_idempotency
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260712_add_mono_last_ingest_at"
down_revision = "20260712_supplier_invoice_source_idempotency"
branch_labels = None
depends_on = None

COLUMN = "mono_last_ingest_at"
LINK_FAILED = "mono_link_failed"


def _column_exists(conn: sa.engine.Connection, name: str) -> bool:
    return (
        conn.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'banking'
                  AND table_name = 'bank_accounts'
                  AND column_name = :name
                """
            ),
            {"name": name},
        ).first()
        is not None
    )


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, COLUMN):
        op.add_column(
            "bank_accounts",
            sa.Column(COLUMN, sa.DateTime(timezone=True), nullable=True),
            schema="banking",
        )

    # Distinguishes a dead bank link (keeps serving 200s from cache, so it
    # cannot clear itself) from a transient API error (clears the moment Mono
    # answers again). Seeded false: an account currently carrying an error
    # will have it re-asserted by the next webhook if the link is genuinely
    # broken, and a stale banner is the safe side of this default.
    if not _column_exists(conn, LINK_FAILED):
        op.add_column(
            "bank_accounts",
            sa.Column(
                LINK_FAILED,
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            schema="banking",
        )

    # Seed from mono_last_synced_at, but only for accounts that demonstrably
    # ingested Mono lines at some point. An account that has never held a
    # mono_* line has never ingested, whatever its last-synced timestamp says.
    conn.execute(
        sa.text(
            """
            UPDATE banking.bank_accounts AS ba
            SET mono_last_ingest_at = ba.mono_last_synced_at
            WHERE ba.mono_last_ingest_at IS NULL
              AND ba.mono_last_synced_at IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM banking.bank_statements bs
                  JOIN banking.bank_statement_lines bsl
                    ON bsl.statement_id = bs.statement_id
                  WHERE bs.bank_account_id = ba.bank_account_id
                    AND bsl.transaction_id LIKE 'mono_%'
              )
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, LINK_FAILED):
        op.drop_column("bank_accounts", LINK_FAILED, schema="banking")
    if _column_exists(conn, COLUMN):
        op.drop_column("bank_accounts", COLUMN, schema="banking")
