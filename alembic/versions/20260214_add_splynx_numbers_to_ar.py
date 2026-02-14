"""Add Splynx generated number fields to AR invoice and payment.

Revision ID: 20260214_add_splynx_numbers_to_ar
Revises: 20260214_add_stmt_line_match_metadata
Create Date: 2026-02-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260214_add_splynx_numbers_to_ar"
down_revision = "20260214_add_stmt_line_match_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    invoice_cols = {c["name"] for c in inspector.get_columns("invoice", schema="ar")}
    if "splynx_number" not in invoice_cols:
        op.add_column(
            "invoice",
            sa.Column("splynx_number", sa.String(length=100), nullable=True),
            schema="ar",
        )

    payment_cols = {
        c["name"] for c in inspector.get_columns("customer_payment", schema="ar")
    }
    if "splynx_receipt_number" not in payment_cols:
        op.add_column(
            "customer_payment",
            sa.Column("splynx_receipt_number", sa.String(length=100), nullable=True),
            schema="ar",
        )

    invoice_indexes = {
        ix["name"] for ix in inspector.get_indexes("invoice", schema="ar")
    }
    if "ix_ar_invoice_splynx_number" not in invoice_indexes:
        op.create_index(
            "ix_ar_invoice_splynx_number",
            "invoice",
            ["splynx_number"],
            schema="ar",
        )

    payment_indexes = {
        ix["name"] for ix in inspector.get_indexes("customer_payment", schema="ar")
    }
    if "ix_ar_customer_payment_splynx_receipt_number" not in payment_indexes:
        op.create_index(
            "ix_ar_customer_payment_splynx_receipt_number",
            "customer_payment",
            ["splynx_receipt_number"],
            schema="ar",
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    payment_indexes = {
        ix["name"] for ix in inspector.get_indexes("customer_payment", schema="ar")
    }
    if "ix_ar_customer_payment_splynx_receipt_number" in payment_indexes:
        op.drop_index(
            "ix_ar_customer_payment_splynx_receipt_number",
            table_name="customer_payment",
            schema="ar",
        )

    invoice_indexes = {
        ix["name"] for ix in inspector.get_indexes("invoice", schema="ar")
    }
    if "ix_ar_invoice_splynx_number" in invoice_indexes:
        op.drop_index(
            "ix_ar_invoice_splynx_number",
            table_name="invoice",
            schema="ar",
        )

    payment_cols = {
        c["name"] for c in inspector.get_columns("customer_payment", schema="ar")
    }
    if "splynx_receipt_number" in payment_cols:
        op.drop_column("customer_payment", "splynx_receipt_number", schema="ar")

    invoice_cols = {c["name"] for c in inspector.get_columns("invoice", schema="ar")}
    if "splynx_number" in invoice_cols:
        op.drop_column("invoice", "splynx_number", schema="ar")
