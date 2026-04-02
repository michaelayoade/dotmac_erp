"""Add unique constraint for CRM material request idempotency.

Revision ID: 20260402_add_unique_material_request_crm_id
Revises: 20260326_sod_flag
Create Date: 2026-04-02
"""

import sqlalchemy as sa

from alembic import op

revision = "20260402_add_unique_material_request_crm_id"
down_revision = "20260326_sod_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("material_request", schema="inv"):
        return

    constraints = {
        c.get("name")
        for c in inspector.get_unique_constraints("material_request", schema="inv")
    }
    if "uq_material_request_org_crm_id" not in constraints:
        op.create_unique_constraint(
            "uq_material_request_org_crm_id",
            "material_request",
            ["organization_id", "crm_id"],
            schema="inv",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("material_request", schema="inv"):
        return

    constraints = {
        c.get("name")
        for c in inspector.get_unique_constraints("material_request", schema="inv")
    }
    if "uq_material_request_org_crm_id" in constraints:
        op.drop_constraint(
            "uq_material_request_org_crm_id",
            "material_request",
            schema="inv",
            type_="unique",
        )

