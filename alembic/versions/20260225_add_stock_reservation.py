"""Add stock reservation table and enums.

Revision ID: 20260225_add_stock_reservation
Revises: 20260225_add_balance_staleness_and_refresh_queue
Create Date: 2026-02-25
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260225_add_stock_reservation"
down_revision: Union[str, Sequence[str], None] = (
    "20260225_add_balance_staleness_and_refresh_queue"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_enum(enum_name: str, values: tuple[str, ...]) -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    enums = {enum["name"] for enum in inspector.get_enums(schema="inv")}
    if enum_name in enums:
        return

    literals = ", ".join(f"'{value}'" for value in values)
    op.execute(f"CREATE TYPE inv.{enum_name} AS ENUM ({literals})")


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    _ensure_enum(
        "reservation_status",
        (
            "RESERVED",
            "PARTIALLY_FULFILLED",
            "FULFILLED",
            "CANCELLED",
            "EXPIRED",
        ),
    )
    _ensure_enum(
        "reservation_source_type",
        ("SALES_ORDER", "TRANSFER_ORDER", "MANUFACTURING_ORDER"),
    )

    if not inspector.has_table("stock_reservation", schema="inv"):
        op.create_table(
            "stock_reservation",
            sa.Column(
                "reservation_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("lot_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("quantity_reserved", sa.Numeric(20, 6), nullable=False),
            sa.Column(
                "quantity_fulfilled",
                sa.Numeric(20, 6),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "quantity_cancelled",
                sa.Numeric(20, 6),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "source_type",
                postgresql.ENUM(
                    "SALES_ORDER",
                    "TRANSFER_ORDER",
                    "MANUFACTURING_ORDER",
                    name="reservation_source_type",
                    schema="inv",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("source_line_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "RESERVED",
                    "PARTIALLY_FULFILLED",
                    "FULFILLED",
                    "CANCELLED",
                    "EXPIRED",
                    name="reservation_status",
                    schema="inv",
                    create_type=False,
                ),
                nullable=False,
                server_default=sa.text("'RESERVED'::inv.reservation_status"),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "priority",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("10"),
            ),
            sa.Column(
                "reserved_by_user_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column(
                "reserved_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancellation_reason", sa.String(length=200), nullable=True),
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["core_org.organization.organization_id"],
            ),
            sa.ForeignKeyConstraint(["item_id"], ["inv.item.item_id"]),
            sa.ForeignKeyConstraint(["warehouse_id"], ["inv.warehouse.warehouse_id"]),
            sa.ForeignKeyConstraint(["lot_id"], ["inv.inventory_lot.lot_id"]),
            sa.PrimaryKeyConstraint("reservation_id"),
            sa.UniqueConstraint(
                "source_type",
                "source_line_id",
                "lot_id",
                name="uq_reservation_source_lot",
            ),
            schema="inv",
        )

    index_names = {
        idx["name"] for idx in inspector.get_indexes("stock_reservation", schema="inv")
    }
    if "ix_reservation_org_status" not in index_names:
        op.create_index(
            "ix_reservation_org_status",
            "stock_reservation",
            ["organization_id", "status"],
            schema="inv",
        )
    if "ix_reservation_expires" not in index_names:
        op.create_index(
            "ix_reservation_expires",
            "stock_reservation",
            ["status", "expires_at"],
            schema="inv",
        )
    if "ix_reservation_item" not in index_names:
        op.create_index(
            "ix_reservation_item",
            "stock_reservation",
            ["organization_id", "item_id", "warehouse_id"],
            schema="inv",
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table("stock_reservation", schema="inv"):
        index_names = {
            idx["name"]
            for idx in inspector.get_indexes("stock_reservation", schema="inv")
        }
        if "ix_reservation_item" in index_names:
            op.drop_index(
                "ix_reservation_item",
                table_name="stock_reservation",
                schema="inv",
            )
        if "ix_reservation_expires" in index_names:
            op.drop_index(
                "ix_reservation_expires",
                table_name="stock_reservation",
                schema="inv",
            )
        if "ix_reservation_org_status" in index_names:
            op.drop_index(
                "ix_reservation_org_status",
                table_name="stock_reservation",
                schema="inv",
            )
        op.drop_table("stock_reservation", schema="inv")

    enums = {enum["name"] for enum in inspector.get_enums(schema="inv")}
    if "reservation_status" in enums:
        op.execute("DROP TYPE inv.reservation_status")
    if "reservation_source_type" in enums:
        op.execute("DROP TYPE inv.reservation_source_type")
