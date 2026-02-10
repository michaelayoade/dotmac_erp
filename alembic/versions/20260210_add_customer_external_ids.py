"""Add erpnext_id and splynx_id columns to ar.customer.

Supports customer deduplication and canonical renumbering by
storing external system references as first-class columns.

Revision ID: 20260210_add_customer_external_ids
Revises: 20260210_add_approver_monthly_budget
Create Date: 2026-02-10
"""

import sqlalchemy as sa

from alembic import op

revision = "20260210_add_customer_external_ids"
down_revision = "20260210_add_approver_monthly_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("customer", schema="ar")]

    if "erpnext_id" not in columns:
        op.add_column(
            "customer",
            sa.Column("erpnext_id", sa.String(100), nullable=True),
            schema="ar",
        )

    if "splynx_id" not in columns:
        op.add_column(
            "customer",
            sa.Column("splynx_id", sa.String(100), nullable=True),
            schema="ar",
        )

    # Partial indexes — only index non-null values
    indexes = {idx["name"] for idx in inspector.get_indexes("customer", schema="ar")}

    if "idx_customer_erpnext_id" not in indexes:
        op.create_index(
            "idx_customer_erpnext_id",
            "customer",
            ["erpnext_id"],
            schema="ar",
            postgresql_where=sa.text("erpnext_id IS NOT NULL"),
        )

    if "idx_customer_splynx_id" not in indexes:
        op.create_index(
            "idx_customer_splynx_id",
            "customer",
            ["splynx_id"],
            schema="ar",
            postgresql_where=sa.text("splynx_id IS NOT NULL"),
        )


def downgrade() -> None:
    op.drop_index(
        "idx_customer_splynx_id",
        table_name="customer",
        schema="ar",
    )
    op.drop_index(
        "idx_customer_erpnext_id",
        table_name="customer",
        schema="ar",
    )
    op.drop_column("customer", "splynx_id", schema="ar")
    op.drop_column("customer", "erpnext_id", schema="ar")
