"""Add ERPNext sync fields to HR assets, promotions, and transfers.

Revision ID: 20260125_add_hr_asset_promotion_transfer_erpnext_fields
Revises: 20260125_merge_heads_for_hr_erpnext_fields
Create Date: 2026-01-25
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260125_add_hr_asset_promotion_transfer_erpnext_fields"
down_revision = "20260125_merge_heads_for_hr_erpnext_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "asset_assignment",
        sa.Column("erpnext_id", sa.String(length=255), nullable=True),
        schema="hr",
    )
    op.add_column(
        "asset_assignment",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        schema="hr",
    )
    op.create_index(
        "ix_asset_assignment_erpnext_id",
        "asset_assignment",
        ["erpnext_id"],
        schema="hr",
    )

    op.add_column(
        "employee_promotion",
        sa.Column("erpnext_id", sa.String(length=255), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_promotion",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        schema="hr",
    )
    op.create_index(
        "ix_employee_promotion_erpnext_id",
        "employee_promotion",
        ["erpnext_id"],
        schema="hr",
    )

    op.add_column(
        "employee_transfer",
        sa.Column("erpnext_id", sa.String(length=255), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_transfer",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        schema="hr",
    )
    op.create_index(
        "ix_employee_transfer_erpnext_id",
        "employee_transfer",
        ["erpnext_id"],
        schema="hr",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_employee_transfer_erpnext_id",
        table_name="employee_transfer",
        schema="hr",
    )
    op.drop_column("employee_transfer", "last_synced_at", schema="hr")
    op.drop_column("employee_transfer", "erpnext_id", schema="hr")

    op.drop_index(
        "ix_employee_promotion_erpnext_id",
        table_name="employee_promotion",
        schema="hr",
    )
    op.drop_column("employee_promotion", "last_synced_at", schema="hr")
    op.drop_column("employee_promotion", "erpnext_id", schema="hr")

    op.drop_index(
        "ix_asset_assignment_erpnext_id",
        table_name="asset_assignment",
        schema="hr",
    )
    op.drop_column("asset_assignment", "last_synced_at", schema="hr")
    op.drop_column("asset_assignment", "erpnext_id", schema="hr")
