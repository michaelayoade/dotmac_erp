"""Add PO amendment/variation fields and SUPERSEDED status.

Adds columns for tracking PO amendments (change orders / variations):
- is_amendment, original_po_id, amendment_version, amendment_reason, variation_id
- New POStatus enum value: SUPERSEDED

Revision ID: 20260311_po_amendment
Revises: 20260310_add_settingdomain_expense
Create Date: 2026-03-11
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260311_po_amendment"
down_revision: Union[str, None] = "20260310_add_settingdomain_expense"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add SUPERSEDED to po_status enum (idempotent)
    conn = op.get_bind()
    exists = conn.exec_driver_sql(
        "SELECT 1 FROM pg_enum WHERE enumlabel = 'SUPERSEDED' "
        "AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'po_status')"
    ).fetchone()
    if not exists:
        op.execute("ALTER TYPE po_status ADD VALUE 'SUPERSEDED'")

    # 2. Add amendment/variation columns
    op.add_column(
        "purchase_order",
        sa.Column(
            "is_amendment",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="ap",
    )
    op.add_column(
        "purchase_order",
        sa.Column(
            "original_po_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ap.purchase_order.po_id"),
            nullable=True,
            comment="Links to the baseline PO being amended",
        ),
        schema="ap",
    )
    op.add_column(
        "purchase_order",
        sa.Column(
            "amendment_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
            comment="Version counter: baseline=1, first amendment=2, etc.",
        ),
        schema="ap",
    )
    op.add_column(
        "purchase_order",
        sa.Column(
            "amendment_reason",
            sa.Text(),
            nullable=True,
            comment="Reason for the amendment / variation",
        ),
        schema="ap",
    )
    op.add_column(
        "purchase_order",
        sa.Column(
            "variation_id",
            sa.String(36),
            nullable=True,
            comment="CRM variation identifier for traceability",
        ),
        schema="ap",
    )

    # 3. Index for quick lookup of amendments by original PO
    op.create_index(
        "idx_po_original_po",
        "purchase_order",
        ["original_po_id"],
        schema="ap",
        postgresql_where=sa.text("original_po_id IS NOT NULL"),
    )

    # 4. Add variation_version to crm_sync_mapping.crm_data (no DDL needed — JSONB)


def downgrade() -> None:
    op.drop_index("idx_po_original_po", table_name="purchase_order", schema="ap")
    op.drop_column("purchase_order", "variation_id", schema="ap")
    op.drop_column("purchase_order", "amendment_reason", schema="ap")
    op.drop_column("purchase_order", "amendment_version", schema="ap")
    op.drop_column("purchase_order", "original_po_id", schema="ap")
    op.drop_column("purchase_order", "is_amendment", schema="ap")
    # Note: Cannot remove SUPERSEDED from enum safely
