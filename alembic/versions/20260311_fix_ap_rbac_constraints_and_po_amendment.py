"""Fix missing constraints on RBAC + AP tables and add PO amendment fields.

Addresses a systemic issue where RBAC tables (roles, permissions,
role_permissions, person_roles) and all AP-schema tables were created
without primary keys, unique constraints, or indexes.

This migration:
1. Adds missing PKs to 4 RBAC tables and 12 AP tables.
2. Adds missing unique constraints matching SQLAlchemy model definitions.
3. Adds missing performance indexes matching model definitions.
4. Adds PO amendment/variation columns + SUPERSEDED enum value.

All statements are idempotent — safe to run on databases that already
have some or all constraints in place.

Revision ID: 20260311_fix_constraints
Revises: 20260310_add_settingdomain_expense
Create Date: 2026-03-11
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260311_fix_constraints"
down_revision: Union[str, None] = "20260310_add_settingdomain_expense"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Helpers — idempotent DDL via catalog introspection
# ---------------------------------------------------------------------------


def _has_pk(conn, table: str, schema: str = "public") -> bool:
    """Check whether a table already has a primary key constraint."""
    row = conn.exec_driver_sql(
        """
        SELECT 1 FROM pg_constraint c
        JOIN pg_namespace n ON n.oid = c.connamespace
        WHERE c.conrelid = (
            SELECT oid FROM pg_class
            WHERE relname = %s
              AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = %s)
        )
        AND c.contype = 'p'
        """,
        (table, schema),
    ).fetchone()
    return row is not None


def _has_constraint(conn, constraint_name: str) -> bool:
    """Check whether a named constraint exists anywhere in the database."""
    row = conn.exec_driver_sql(
        "SELECT 1 FROM pg_constraint WHERE conname = %s",
        (constraint_name,),
    ).fetchone()
    return row is not None


def _has_index(conn, index_name: str) -> bool:
    """Check whether a named index exists anywhere in the database."""
    row = conn.exec_driver_sql(
        "SELECT 1 FROM pg_indexes WHERE indexname = %s",
        (index_name,),
    ).fetchone()
    return row is not None


def _add_pk(conn, table: str, column: str, schema: str = "public") -> None:
    """Add a primary key if one doesn't exist."""
    if not _has_pk(conn, table, schema):
        qualified = f"{schema}.{table}" if schema != "public" else table
        conn.exec_driver_sql(
            f"ALTER TABLE {qualified} ADD PRIMARY KEY ({column})"  # noqa: S608
        )


def _add_unique(
    conn, name: str, table: str, columns: str, schema: str = "public"
) -> None:
    """Add a unique constraint if it doesn't exist."""
    if not _has_constraint(conn, name):
        qualified = f"{schema}.{table}" if schema != "public" else table
        conn.exec_driver_sql(
            f"ALTER TABLE {qualified} "  # noqa: S608
            f"ADD CONSTRAINT {name} UNIQUE ({columns})"
        )


def _add_index(
    conn,
    name: str,
    table: str,
    columns: str,
    schema: str = "public",
    *,
    where: str | None = None,
    unique: bool = False,
) -> None:
    """Add an index if it doesn't exist."""
    if not _has_index(conn, name):
        qualified = f"{schema}.{table}" if schema != "public" else table
        uq = "UNIQUE " if unique else ""
        sql = f"CREATE {uq}INDEX {name} ON {qualified} ({columns})"  # noqa: S608
        if where:
            sql += f" WHERE {where}"
        conn.exec_driver_sql(sql)


def _has_column(conn, table: str, column: str, schema: str = "public") -> bool:
    """Check whether a column exists on a table."""
    row = conn.exec_driver_sql(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s AND column_name = %s
        """,
        (schema, table, column),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. RBAC table constraints ────────────────────────────────────────

    # roles
    _add_pk(conn, "roles", "id")
    _add_unique(conn, "uq_roles_name", "roles", "name")

    # permissions
    _add_pk(conn, "permissions", "id")
    _add_unique(conn, "uq_permissions_key", "permissions", '"key"')

    # role_permissions
    _add_pk(conn, "role_permissions", "id")
    _add_unique(
        conn,
        "uq_role_permissions_role_permission",
        "role_permissions",
        "role_id, permission_id",
    )

    # person_roles
    _add_pk(conn, "person_roles", "id")
    _add_unique(
        conn,
        "uq_person_roles_person_role",
        "person_roles",
        "person_id, role_id",
    )

    # ── 2. AP table primary keys (parent tables first) ───────────────────

    _add_pk(conn, "supplier", "supplier_id", "ap")
    _add_pk(conn, "purchase_order", "po_id", "ap")
    _add_pk(conn, "supplier_invoice", "invoice_id", "ap")
    _add_pk(conn, "supplier_payment", "payment_id", "ap")
    _add_pk(conn, "payment_batch", "batch_id", "ap")

    # Child tables (depend on parent PKs for FKs)
    _add_pk(conn, "purchase_order_line", "line_id", "ap")
    _add_pk(conn, "supplier_invoice_line", "line_id", "ap")
    _add_pk(conn, "supplier_invoice_line_tax", "line_tax_id", "ap")
    _add_pk(conn, "goods_receipt", "receipt_id", "ap")
    _add_pk(conn, "goods_receipt_line", "line_id", "ap")
    _add_pk(conn, "payment_allocation", "allocation_id", "ap")
    _add_pk(conn, "ap_aging_snapshot", "snapshot_id", "ap")

    # ── 3. AP unique constraints ─────────────────────────────────────────

    _add_unique(
        conn,
        "uq_supplier_code",
        "supplier",
        "organization_id, supplier_code",
        "ap",
    )
    _add_unique(
        conn,
        "uq_po_number",
        "purchase_order",
        "organization_id, po_number",
        "ap",
    )
    _add_unique(
        conn,
        "uq_po_line",
        "purchase_order_line",
        "po_id, line_number",
        "ap",
    )
    _add_unique(
        conn,
        "uq_supplier_invoice",
        "supplier_invoice",
        "organization_id, invoice_number",
        "ap",
    )
    _add_unique(
        conn,
        "uq_supplier_invoice_line",
        "supplier_invoice_line",
        "invoice_id, line_number",
        "ap",
    )
    _add_unique(
        conn,
        "uq_line_tax_code",
        "supplier_invoice_line_tax",
        "line_id, tax_code_id",
        "ap",
    )
    _add_unique(
        conn,
        "uq_supplier_payment",
        "supplier_payment",
        "organization_id, payment_number",
        "ap",
    )
    _add_unique(
        conn,
        "uq_ap_allocation",
        "payment_allocation",
        "payment_id, invoice_id",
        "ap",
    )
    _add_unique(
        conn,
        "uq_receipt_number",
        "goods_receipt",
        "organization_id, receipt_number",
        "ap",
    )
    _add_unique(
        conn,
        "uq_receipt_line",
        "goods_receipt_line",
        "receipt_id, line_number",
        "ap",
    )
    _add_unique(
        conn,
        "uq_payment_batch",
        "payment_batch",
        "organization_id, batch_number",
        "ap",
    )
    _add_unique(
        conn,
        "uq_ap_aging",
        "ap_aging_snapshot",
        "organization_id, fiscal_period_id, supplier_id, aging_bucket",
        "ap",
    )

    # ── 4. AP performance indexes ────────────────────────────────────────

    _add_index(
        conn,
        "idx_supplier_org",
        "supplier",
        "organization_id, is_active",
        "ap",
    )
    _add_index(
        conn,
        "idx_po_supplier",
        "purchase_order",
        "supplier_id",
        "ap",
    )
    _add_index(
        conn,
        "idx_po_status",
        "purchase_order",
        "organization_id, status",
        "ap",
    )
    _add_index(
        conn,
        "idx_supplier_invoice_supplier",
        "supplier_invoice",
        "supplier_id",
        "ap",
    )
    _add_index(
        conn,
        "idx_supplier_invoice_status",
        "supplier_invoice",
        "organization_id, status",
        "ap",
    )
    _add_index(
        conn,
        "idx_supplier_invoice_due_date",
        "supplier_invoice",
        "organization_id, due_date",
        "ap",
        where="status NOT IN ('PAID', 'VOID')",
    )
    _add_index(
        conn,
        "idx_supplier_payment_supplier",
        "supplier_payment",
        "supplier_id",
        "ap",
    )
    _add_index(
        conn,
        "idx_receipt_po",
        "goods_receipt",
        "po_id",
        "ap",
    )

    # ── 5. PO amendment: SUPERSEDED enum value ───────────────────────────

    has_superseded = conn.exec_driver_sql(
        "SELECT 1 FROM pg_enum WHERE enumlabel = 'SUPERSEDED' "
        "AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'po_status')"
    ).fetchone()
    if not has_superseded:
        op.execute("ALTER TYPE po_status ADD VALUE 'SUPERSEDED'")

    # ── 6. PO amendment columns ──────────────────────────────────────────

    if not _has_column(conn, "purchase_order", "is_amendment", "ap"):
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

    if not _has_column(conn, "purchase_order", "original_po_id", "ap"):
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

    if not _has_column(conn, "purchase_order", "amendment_version", "ap"):
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

    if not _has_column(conn, "purchase_order", "amendment_reason", "ap"):
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

    if not _has_column(conn, "purchase_order", "variation_id", "ap"):
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

    # ── 7. PO amendment indexes ──────────────────────────────────────────

    _add_index(
        conn,
        "idx_po_original_po",
        "purchase_order",
        "original_po_id",
        "ap",
        where="original_po_id IS NOT NULL",
    )
    _add_index(
        conn,
        "uq_po_variation_id",
        "purchase_order",
        "organization_id, variation_id",
        "ap",
        where="variation_id IS NOT NULL",
        unique=True,
    )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # PO amendment indexes
    op.drop_index("uq_po_variation_id", table_name="purchase_order", schema="ap")
    op.drop_index("idx_po_original_po", table_name="purchase_order", schema="ap")

    # PO amendment columns
    op.drop_column("purchase_order", "variation_id", schema="ap")
    op.drop_column("purchase_order", "amendment_reason", schema="ap")
    op.drop_column("purchase_order", "amendment_version", schema="ap")
    op.drop_column("purchase_order", "original_po_id", schema="ap")
    op.drop_column("purchase_order", "is_amendment", schema="ap")

    # Note: Cannot remove SUPERSEDED from po_status enum safely.
    # Note: PKs, unique constraints, and indexes are NOT removed on downgrade
    #       as they represent correct schema state that should always exist.
