"""Correlate Sub vendors with ERP suppliers.

Revision ID: 20261104_sub_vendor_supplier_identity
Revises: 20261104_expense_reset_permission
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20261104_sub_vendor_supplier_identity"
down_revision = "20261104_expense_reset_permission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supplier", sa.Column("sub_vendor_id", UUID(as_uuid=True)), schema="ap"
    )
    op.create_unique_constraint(
        "uq_supplier_sub_vendor",
        "supplier",
        ["organization_id", "sub_vendor_id"],
        schema="ap",
    )


def downgrade() -> None:
    op.drop_constraint("uq_supplier_sub_vendor", "supplier", schema="ap")
    op.drop_column("supplier", "sub_vendor_id", schema="ap")
