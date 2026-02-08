"""Add ERPNext sync fields to HR lifecycle tables.

Revision ID: 20260124_add_hr_lifecycle_erpnext_fields
Revises: 20260124_add_settingdomain_payments
Create Date: 2026-01-24
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260124_add_hr_lifecycle_erpnext_fields"
down_revision = "20260124_add_settingdomain_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employee_onboarding",
        sa.Column("erpnext_id", sa.String(length=255), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        schema="hr",
    )
    op.create_index(
        "ix_employee_onboarding_erpnext_id",
        "employee_onboarding",
        ["erpnext_id"],
        schema="hr",
    )

    op.add_column(
        "employee_separation",
        sa.Column("erpnext_id", sa.String(length=255), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_separation",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        schema="hr",
    )
    op.create_index(
        "ix_employee_separation_erpnext_id",
        "employee_separation",
        ["erpnext_id"],
        schema="hr",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_employee_separation_erpnext_id",
        table_name="employee_separation",
        schema="hr",
    )
    op.drop_column("employee_separation", "last_synced_at", schema="hr")
    op.drop_column("employee_separation", "erpnext_id", schema="hr")

    op.drop_index(
        "ix_employee_onboarding_erpnext_id",
        table_name="employee_onboarding",
        schema="hr",
    )
    op.drop_column("employee_onboarding", "last_synced_at", schema="hr")
    op.drop_column("employee_onboarding", "erpnext_id", schema="hr")
