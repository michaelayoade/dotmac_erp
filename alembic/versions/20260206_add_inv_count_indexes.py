"""Add indexes for inventory count tables.

Revision ID: 20260206_add_inv_count_indexes
Revises: 20260206_add_module_email_overrides
Create Date: 2026-02-06
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260206_add_inv_count_indexes"
down_revision: Union[str, None] = "20260206_add_module_email_overrides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "inv"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # inventory_count indexes
    if inspector.has_table("inventory_count", schema=SCHEMA):
        existing = {
            idx["name"]
            for idx in inspector.get_indexes("inventory_count", schema=SCHEMA)
        }

        if "idx_inv_count_org" not in existing:
            op.create_index(
                "idx_inv_count_org",
                "inventory_count",
                ["organization_id"],
                schema=SCHEMA,
            )
        if "idx_inv_count_status" not in existing:
            op.create_index(
                "idx_inv_count_status",
                "inventory_count",
                ["status"],
                schema=SCHEMA,
            )
        if "idx_inv_count_date" not in existing:
            op.create_index(
                "idx_inv_count_date",
                "inventory_count",
                ["count_date"],
                schema=SCHEMA,
            )
        if "idx_inv_count_warehouse" not in existing:
            op.create_index(
                "idx_inv_count_warehouse",
                "inventory_count",
                ["warehouse_id"],
                schema=SCHEMA,
            )

    # inventory_count_line indexes
    if inspector.has_table("inventory_count_line", schema=SCHEMA):
        existing = {
            idx["name"]
            for idx in inspector.get_indexes("inventory_count_line", schema=SCHEMA)
        }

        if "idx_inv_count_line_count" not in existing:
            op.create_index(
                "idx_inv_count_line_count",
                "inventory_count_line",
                ["count_id"],
                schema=SCHEMA,
            )
        if "idx_inv_count_line_item" not in existing:
            op.create_index(
                "idx_inv_count_line_item",
                "inventory_count_line",
                ["item_id"],
                schema=SCHEMA,
            )
        if "idx_inv_count_line_warehouse" not in existing:
            op.create_index(
                "idx_inv_count_line_warehouse",
                "inventory_count_line",
                ["warehouse_id"],
                schema=SCHEMA,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("inventory_count_line", schema=SCHEMA):
        existing = {
            idx["name"]
            for idx in inspector.get_indexes("inventory_count_line", schema=SCHEMA)
        }
        for idx_name in [
            "idx_inv_count_line_warehouse",
            "idx_inv_count_line_item",
            "idx_inv_count_line_count",
        ]:
            if idx_name in existing:
                op.drop_index(
                    idx_name, table_name="inventory_count_line", schema=SCHEMA
                )

    if inspector.has_table("inventory_count", schema=SCHEMA):
        existing = {
            idx["name"]
            for idx in inspector.get_indexes("inventory_count", schema=SCHEMA)
        }
        for idx_name in [
            "idx_inv_count_warehouse",
            "idx_inv_count_date",
            "idx_inv_count_status",
            "idx_inv_count_org",
        ]:
            if idx_name in existing:
                op.drop_index(idx_name, table_name="inventory_count", schema=SCHEMA)
