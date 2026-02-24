"""Add missing columns to ap.goods_receipt_line.

Revision ID: 20260224_add_goods_receipt_line_columns
Revises: 20260224_add_settingdomain_banking
Create Date: 2026-02-24

Adds columns that were present in the ORM model but missing from the database:
- item_id (UUID, nullable) — FK to inv.item
- description (TEXT, nullable)
- inspection_required (BOOLEAN, default false)
- inspection_status (inspection_status enum, default 'NOT_REQUIRED')
- serial_numbers (TEXT[], nullable)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from alembic import op

revision: str = "20260224_add_goods_receipt_line_columns"
down_revision: Union[str, None] = "20260224_add_settingdomain_banking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    existing = {
        c["name"] for c in inspector.get_columns("goods_receipt_line", schema="ap")
    }

    # Create the enum type if it doesn't exist
    conn.exec_driver_sql(
        "DO $$ BEGIN "
        "  CREATE TYPE inspection_status AS ENUM "
        "    ('NOT_REQUIRED', 'PENDING', 'PASSED', 'FAILED'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$;"
    )

    if "item_id" not in existing:
        op.add_column(
            "goods_receipt_line",
            sa.Column("item_id", UUID(as_uuid=True), nullable=True),
            schema="ap",
        )

    if "description" not in existing:
        op.add_column(
            "goods_receipt_line",
            sa.Column("description", sa.Text, nullable=True),
            schema="ap",
        )

    if "inspection_required" not in existing:
        op.add_column(
            "goods_receipt_line",
            sa.Column(
                "inspection_required",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("false"),
            ),
            schema="ap",
        )

    if "inspection_status" not in existing:
        op.add_column(
            "goods_receipt_line",
            sa.Column(
                "inspection_status",
                sa.Enum(
                    "NOT_REQUIRED",
                    "PENDING",
                    "PASSED",
                    "FAILED",
                    name="inspection_status",
                    create_type=False,
                ),
                nullable=False,
                server_default=sa.text("'NOT_REQUIRED'"),
            ),
            schema="ap",
        )

    if "serial_numbers" not in existing:
        op.add_column(
            "goods_receipt_line",
            sa.Column("serial_numbers", ARRAY(sa.Text), nullable=True),
            schema="ap",
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {
        c["name"] for c in inspector.get_columns("goods_receipt_line", schema="ap")
    }

    for col in (
        "serial_numbers",
        "inspection_status",
        "inspection_required",
        "description",
        "item_id",
    ):
        if col in existing:
            op.drop_column("goods_receipt_line", col, schema="ap")

    # Drop enum type if it exists
    conn.exec_driver_sql("DROP TYPE IF EXISTS inspection_status")
