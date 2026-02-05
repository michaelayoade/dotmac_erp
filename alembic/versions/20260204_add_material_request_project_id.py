"""Add project_id to material request header.

Revision ID: 20260204_add_material_request_project_id
Revises: 20260203_add_material_request_sequence_type
Create Date: 2026-02-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260204_add_material_request_project_id"
down_revision = "20260203_add_material_request_sequence_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "material_request",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="inv",
    )
    op.create_foreign_key(
        "fk_material_request_project",
        "material_request",
        "project",
        ["project_id"],
        ["project_id"],
        source_schema="inv",
        referent_schema="core_org",
    )
    op.create_index(
        "idx_material_request_project",
        "material_request",
        ["project_id"],
        schema="inv",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_material_request_project",
        table_name="material_request",
        schema="inv",
    )
    op.drop_constraint(
        "fk_material_request_project",
        "material_request",
        type_="foreignkey",
        schema="inv",
    )
    op.drop_column("material_request", "project_id", schema="inv")
