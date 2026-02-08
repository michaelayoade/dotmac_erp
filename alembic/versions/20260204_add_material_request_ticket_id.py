"""Add ticket_id to material request header.

Revision ID: 20260204_add_material_request_ticket_id
Revises: 20260204_add_material_request_project_id
Create Date: 2026-02-04
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260204_add_material_request_ticket_id"
down_revision = "20260204_add_material_request_project_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("material_request", schema="inv"):
        return

    columns = {
        col["name"] for col in inspector.get_columns("material_request", schema="inv")
    }
    if "ticket_id" not in columns:
        op.add_column(
            "material_request",
            sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=True),
            schema="inv",
        )

    fks = {
        fk["name"]
        for fk in inspector.get_foreign_keys("material_request", schema="inv")
        if fk.get("name")
    }
    if "fk_material_request_ticket" not in fks:
        op.create_foreign_key(
            "fk_material_request_ticket",
            "material_request",
            "ticket",
            ["ticket_id"],
            ["ticket_id"],
            source_schema="inv",
            referent_schema="support",
        )

    indexes = {
        idx["name"]
        for idx in inspector.get_indexes("material_request", schema="inv")
        if idx.get("name")
    }
    if "idx_material_request_ticket" not in indexes:
        op.create_index(
            "idx_material_request_ticket",
            "material_request",
            ["ticket_id"],
            schema="inv",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("material_request", schema="inv"):
        return

    indexes = {
        idx["name"]
        for idx in inspector.get_indexes("material_request", schema="inv")
        if idx.get("name")
    }
    if "idx_material_request_ticket" in indexes:
        op.drop_index(
            "idx_material_request_ticket",
            table_name="material_request",
            schema="inv",
        )

    fks = {
        fk["name"]
        for fk in inspector.get_foreign_keys("material_request", schema="inv")
        if fk.get("name")
    }
    if "fk_material_request_ticket" in fks:
        op.drop_constraint(
            "fk_material_request_ticket",
            "material_request",
            type_="foreignkey",
            schema="inv",
        )

    columns = {
        col["name"] for col in inspector.get_columns("material_request", schema="inv")
    }
    if "ticket_id" in columns:
        op.drop_column("material_request", "ticket_id", schema="inv")
