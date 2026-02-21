"""Add AP supplier invoice withholding tax code column.

Revision ID: 20260219_add_ap_invoice_wht_code
Revises: 20260219_fix_vat_tax_collected_account
Create Date: 2026-02-19
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260219_add_ap_invoice_wht_code"
down_revision = "20260219_fix_vat_tax_collected_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {
        c["name"] for c in inspector.get_columns("supplier_invoice", schema="ap")
    }

    if "withholding_tax_code_id" not in columns:
        op.add_column(
            "supplier_invoice",
            sa.Column(
                "withholding_tax_code_id",
                UUID(as_uuid=True),
                nullable=True,
                comment="WHT tax code applied to this invoice",
            ),
            schema="ap",
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {
        c["name"] for c in inspector.get_columns("supplier_invoice", schema="ap")
    }
    if "withholding_tax_code_id" in columns:
        op.drop_column("supplier_invoice", "withholding_tax_code_id", schema="ap")
