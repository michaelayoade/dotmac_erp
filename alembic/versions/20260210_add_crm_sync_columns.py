"""Add crm_id columns to ar.customer and inv.material_request.

Supports CRM sync by storing the DotMac CRM entity ID directly on the
model for fast idempotency checks and cross-system lookup.

Revision ID: 20260210_add_crm_sync_columns
Revises: 20260210_add_customer_external_ids
Create Date: 2026-02-10
"""

import sqlalchemy as sa

from alembic import op

revision = "20260210_add_crm_sync_columns"
down_revision = "20260210_add_customer_external_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # --- ar.customer.crm_id ---
    customer_cols = [c["name"] for c in inspector.get_columns("customer", schema="ar")]
    if "crm_id" not in customer_cols:
        op.add_column(
            "customer",
            sa.Column(
                "crm_id",
                sa.String(36),
                nullable=True,
                comment="DotMac CRM customer/company ID (for sync lookup)",
            ),
            schema="ar",
        )

    customer_indexes = {
        idx["name"] for idx in inspector.get_indexes("customer", schema="ar")
    }
    if "idx_customer_crm_id" not in customer_indexes:
        op.create_index(
            "idx_customer_crm_id",
            "customer",
            ["crm_id"],
            schema="ar",
            postgresql_where=sa.text("crm_id IS NOT NULL"),
        )

    # --- inv.material_request.crm_id ---
    mr_cols = [
        c["name"] for c in inspector.get_columns("material_request", schema="inv")
    ]
    if "crm_id" not in mr_cols:
        op.add_column(
            "material_request",
            sa.Column(
                "crm_id",
                sa.String(36),
                nullable=True,
                comment="DotMac CRM material request ID (omni_id for idempotency)",
            ),
            schema="inv",
        )

    mr_indexes = {
        idx["name"] for idx in inspector.get_indexes("material_request", schema="inv")
    }
    if "idx_material_request_crm_id" not in mr_indexes:
        op.create_index(
            "idx_material_request_crm_id",
            "material_request",
            ["crm_id"],
            schema="inv",
            postgresql_where=sa.text("crm_id IS NOT NULL"),
        )


def downgrade() -> None:
    op.drop_index(
        "idx_material_request_crm_id",
        table_name="material_request",
        schema="inv",
    )
    op.drop_column("material_request", "crm_id", schema="inv")
    op.drop_index(
        "idx_customer_crm_id",
        table_name="customer",
        schema="ar",
    )
    op.drop_column("customer", "crm_id", schema="ar")
