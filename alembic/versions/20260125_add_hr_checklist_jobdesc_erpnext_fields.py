"""Add ERPNext sync fields to HR checklists and job descriptions.

Revision ID: 20260125_add_hr_checklist_jobdesc_erpnext_fields
Revises: 20260125_add_hr_asset_promotion_transfer_erpnext_fields
Create Date: 2026-01-25
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260125_add_hr_checklist_jobdesc_erpnext_fields"
down_revision = "20260125_add_hr_asset_promotion_transfer_erpnext_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("checklist_template", schema="hr"):
        op.add_column(
            "checklist_template",
            sa.Column("erpnext_id", sa.String(length=255), nullable=True),
            schema="hr",
        )
        op.add_column(
            "checklist_template",
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            schema="hr",
        )
        op.create_index(
            "ix_checklist_template_erpnext_id",
            "checklist_template",
            ["erpnext_id"],
            schema="hr",
        )

    if inspector.has_table("job_description", schema="hr"):
        op.add_column(
            "job_description",
            sa.Column("erpnext_id", sa.String(length=255), nullable=True),
            schema="hr",
        )
        op.add_column(
            "job_description",
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            schema="hr",
        )
        op.create_index(
            "ix_job_description_erpnext_id",
            "job_description",
            ["erpnext_id"],
            schema="hr",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("job_description", schema="hr"):
        op.drop_index(
            "ix_job_description_erpnext_id",
            table_name="job_description",
            schema="hr",
        )
        op.drop_column("job_description", "last_synced_at", schema="hr")
        op.drop_column("job_description", "erpnext_id", schema="hr")

    if inspector.has_table("checklist_template", schema="hr"):
        op.drop_index(
            "ix_checklist_template_erpnext_id",
            table_name="checklist_template",
            schema="hr",
        )
        op.drop_column("checklist_template", "last_synced_at", schema="hr")
        op.drop_column("checklist_template", "erpnext_id", schema="hr")
