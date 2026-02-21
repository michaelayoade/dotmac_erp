"""Add withholding tax columns to AR invoice.

Revision ID: 20260218_add_ar_invoice_wht_columns
Revises: 20260218_add_supplier_invoice_comments
Create Date: 2026-02-18
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260218_add_ar_invoice_wht_columns"
down_revision = "20260218_add_supplier_invoice_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {c["name"] for c in inspector.get_columns("invoice", schema="ar")}

    if "withholding_tax_amount" not in columns:
        op.add_column(
            "invoice",
            sa.Column(
                "withholding_tax_amount",
                sa.Numeric(20, 6),
                nullable=False,
                server_default="0",
                comment="Invoice-level WHT deduction amount",
            ),
            schema="ar",
        )

    if "withholding_tax_code_id" not in columns:
        op.add_column(
            "invoice",
            sa.Column(
                "withholding_tax_code_id",
                UUID(as_uuid=True),
                nullable=True,
                comment="WHT tax code applied to this invoice",
            ),
            schema="ar",
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {c["name"] for c in inspector.get_columns("invoice", schema="ar")}

    if "withholding_tax_code_id" in columns:
        op.drop_column("invoice", "withholding_tax_code_id", schema="ar")
    if "withholding_tax_amount" in columns:
        op.drop_column("invoice", "withholding_tax_amount", schema="ar")
