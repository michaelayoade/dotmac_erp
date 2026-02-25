"""Add balance staleness fields and refresh queue table.

Revision ID: 20260225_add_balance_staleness_and_refresh_queue
Revises: 20260224_add_settingdomain_coach
Create Date: 2026-02-25
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260225_add_balance_staleness_and_refresh_queue"
down_revision: Union[str, Sequence[str], None] = "20260224_add_settingdomain_coach"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    balance_columns = {
        col["name"] for col in inspector.get_columns("account_balance", schema="gl")
    }
    if "is_stale" not in balance_columns:
        op.add_column(
            "account_balance",
            sa.Column(
                "is_stale",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            schema="gl",
        )
    if "stale_since" not in balance_columns:
        op.add_column(
            "account_balance",
            sa.Column("stale_since", sa.DateTime(timezone=True), nullable=True),
            schema="gl",
        )
    if "refresh_count" not in balance_columns:
        op.add_column(
            "account_balance",
            sa.Column(
                "refresh_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            schema="gl",
        )

    balance_indexes = {
        idx["name"] for idx in inspector.get_indexes("account_balance", schema="gl")
    }
    if "idx_balance_stale" not in balance_indexes:
        op.create_index(
            "idx_balance_stale",
            "account_balance",
            ["organization_id", "is_stale"],
            schema="gl",
        )

    if not inspector.has_table("balance_refresh_queue", schema="gl"):
        op.create_table(
            "balance_refresh_queue",
            sa.Column(
                "queue_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "fiscal_period_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column(
                "invalidated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("queue_id"),
            sa.UniqueConstraint(
                "organization_id",
                "account_id",
                "fiscal_period_id",
                name="uq_balance_refresh_key",
            ),
            schema="gl",
        )
        op.create_index(
            "ix_balance_refresh_pending",
            "balance_refresh_queue",
            ["processed_at", "invalidated_at"],
            schema="gl",
        )
        op.create_index(
            "ix_balance_refresh_org",
            "balance_refresh_queue",
            ["organization_id", "invalidated_at"],
            schema="gl",
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table("balance_refresh_queue", schema="gl"):
        op.drop_index(
            "ix_balance_refresh_org", table_name="balance_refresh_queue", schema="gl"
        )
        op.drop_index(
            "ix_balance_refresh_pending",
            table_name="balance_refresh_queue",
            schema="gl",
        )
        op.drop_table("balance_refresh_queue", schema="gl")

    balance_columns = {
        col["name"] for col in inspector.get_columns("account_balance", schema="gl")
    }
    balance_indexes = {
        idx["name"] for idx in inspector.get_indexes("account_balance", schema="gl")
    }
    if "idx_balance_stale" in balance_indexes:
        op.drop_index("idx_balance_stale", table_name="account_balance", schema="gl")
    if "refresh_count" in balance_columns:
        op.drop_column("account_balance", "refresh_count", schema="gl")
    if "stale_since" in balance_columns:
        op.drop_column("account_balance", "stale_since", schema="gl")
    if "is_stale" in balance_columns:
        op.drop_column("account_balance", "is_stale", schema="gl")
