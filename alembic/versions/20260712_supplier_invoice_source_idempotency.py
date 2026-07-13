"""Make external supplier-invoice correlation keys idempotent.

Revision ID: 20260712_supplier_invoice_source_idempotency
Revises: 20260711_add_dotmac_sub_sync_watermark
"""

import sqlalchemy as sa
from alembic import op

revision = "20260712_supplier_invoice_source_idempotency"
down_revision = "20260711_add_dotmac_sub_sync_watermark"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_supplier_invoice_source_correlation",
        "supplier_invoice",
        ["organization_id", "correlation_id"],
        schema="ap",
        unique=True,
        postgresql_where=sa.text("correlation_id LIKE 'sub-invoice:%'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_supplier_invoice_source_correlation",
        "supplier_invoice",
        schema="ap",
    )
