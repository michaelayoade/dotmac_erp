"""Add item WAC ledger table.

Revision ID: 20260225_add_item_wac_ledger
Revises: 20260225_add_analysis_cubes
Create Date: 2026-02-25
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260225_add_item_wac_ledger"
down_revision: Union[str, Sequence[str], None] = "20260225_add_analysis_cubes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table("item_wac_ledger", schema="inv"):
        return

    op.create_table(
        "item_wac_ledger",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "current_wac",
            sa.Numeric(20, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "quantity_on_hand",
            sa.Numeric(20, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_value",
            sa.Numeric(20, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "last_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["inv.item.item_id"],
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["inv.warehouse.warehouse_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "item_id",
            "warehouse_id",
            name="uq_item_wac",
        ),
        schema="inv",
    )
    op.create_index(
        "ix_item_wac_org_item",
        "item_wac_ledger",
        ["organization_id", "item_id"],
        schema="inv",
    )
    op.create_index(
        "ix_item_wac_org_warehouse",
        "item_wac_ledger",
        ["organization_id", "warehouse_id"],
        schema="inv",
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("item_wac_ledger", schema="inv"):
        return

    op.drop_index("ix_item_wac_org_warehouse", table_name="item_wac_ledger", schema="inv")
    op.drop_index("ix_item_wac_org_item", table_name="item_wac_ledger", schema="inv")
    op.drop_table("item_wac_ledger", schema="inv")
