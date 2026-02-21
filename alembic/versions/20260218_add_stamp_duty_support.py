"""Add stamp duty support to AP and AR invoices.

Adds STAMP_DUTY to the TaxType PostgreSQL enum, and adds
stamp_duty_amount + stamp_duty_code_id columns to both
ap.supplier_invoice and ar.invoice.

Revision ID: 20260218_add_stamp_duty_support
Revises: 20260218_add_ar_invoice_wht_columns
Create Date: 2026-02-19
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260218_add_stamp_duty_support"
down_revision = "20260218_add_ar_invoice_wht_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- 1. Add STAMP_DUTY to the taxtype PostgreSQL enum ---
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
    # We must use autocommit execution mode.
    conn = op.get_bind()

    # Check if value already exists (idempotent)
    existing = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_enum "
            "WHERE enumtypid = 'public.tax_type'::regtype "
            "AND enumlabel = 'STAMP_DUTY'"
        )
    ).scalar()

    if not existing:
        # Run enum ALTER in an Alembic-managed autocommit block.
        with op.get_context().autocommit_block():
            conn.execute(sa.text("ALTER TYPE public.tax_type ADD VALUE 'STAMP_DUTY'"))

    # --- 2. Add stamp duty columns to ap.supplier_invoice ---
    inspector = sa.inspect(conn)
    ap_columns = {
        c["name"] for c in inspector.get_columns("supplier_invoice", schema="ap")
    }

    if "stamp_duty_amount" not in ap_columns:
        op.add_column(
            "supplier_invoice",
            sa.Column(
                "stamp_duty_amount",
                sa.Numeric(20, 6),
                nullable=False,
                server_default="0",
                comment="Invoice-level stamp duty deduction amount",
            ),
            schema="ap",
        )

    if "stamp_duty_code_id" not in ap_columns:
        op.add_column(
            "supplier_invoice",
            sa.Column(
                "stamp_duty_code_id",
                UUID(as_uuid=True),
                nullable=True,
                comment="Stamp duty tax code applied to this invoice",
            ),
            schema="ap",
        )

    # --- 3. Add stamp duty columns to ar.invoice ---
    ar_columns = {c["name"] for c in inspector.get_columns("invoice", schema="ar")}

    if "stamp_duty_amount" not in ar_columns:
        op.add_column(
            "invoice",
            sa.Column(
                "stamp_duty_amount",
                sa.Numeric(20, 6),
                nullable=False,
                server_default="0",
                comment="Invoice-level stamp duty deduction amount",
            ),
            schema="ar",
        )

    if "stamp_duty_code_id" not in ar_columns:
        op.add_column(
            "invoice",
            sa.Column(
                "stamp_duty_code_id",
                UUID(as_uuid=True),
                nullable=True,
                comment="Stamp duty tax code applied to this invoice",
            ),
            schema="ar",
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Drop AR columns
    ar_columns = {c["name"] for c in inspector.get_columns("invoice", schema="ar")}
    if "stamp_duty_code_id" in ar_columns:
        op.drop_column("invoice", "stamp_duty_code_id", schema="ar")
    if "stamp_duty_amount" in ar_columns:
        op.drop_column("invoice", "stamp_duty_amount", schema="ar")

    # Drop AP columns
    ap_columns = {
        c["name"] for c in inspector.get_columns("supplier_invoice", schema="ap")
    }
    if "stamp_duty_code_id" in ap_columns:
        op.drop_column("supplier_invoice", "stamp_duty_code_id", schema="ap")
    if "stamp_duty_amount" in ap_columns:
        op.drop_column("supplier_invoice", "stamp_duty_amount", schema="ap")

    # Note: Cannot remove enum values in PostgreSQL — STAMP_DUTY value remains
