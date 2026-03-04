"""Add parent-child customer hierarchy for ISP reseller accounts.

Adds:
- parent_customer_id: self-referential FK for reseller → sub-account linking
- splynx_partner_id: Splynx partner ID for resolving parent during sync
- Indexes on (organization_id, parent_customer_id) and (organization_id, splynx_partner_id)

Revision ID: 20260304_add_customer_parent_hierarchy
Revises: 20260301_delete_orphan_void_journals
Create Date: 2026-03-04
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260304_add_customer_parent_hierarchy"
down_revision = "20260301_delete_orphan_void_journals"
branch_labels = None
depends_on = None


def _column_exists(schema: str, table: str, column: str) -> bool:
    """Check if a column exists (idempotent guard)."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table "
            "AND column_name = :column"
        ),
        {"schema": schema, "table": table, "column": column},
    )
    return result.fetchone() is not None


def _index_exists(index_name: str) -> bool:
    """Check if an index exists (idempotent guard)."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :name"),
        {"name": index_name},
    )
    return result.fetchone() is not None


def _fk_exists(constraint_name: str) -> bool:
    """Check if a foreign key constraint exists."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE constraint_name = :name AND constraint_type = 'FOREIGN KEY'"
        ),
        {"name": constraint_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # 1. Add parent_customer_id column
    if not _column_exists("ar", "customer", "parent_customer_id"):
        op.add_column(
            "customer",
            sa.Column("parent_customer_id", UUID(as_uuid=True), nullable=True),
            schema="ar",
        )

    # 2. Add FK constraint
    if not _fk_exists("fk_customer_parent_customer_id"):
        op.create_foreign_key(
            "fk_customer_parent_customer_id",
            "customer",
            "customer",
            ["parent_customer_id"],
            ["customer_id"],
            source_schema="ar",
            referent_schema="ar",
            ondelete="SET NULL",
        )

    # 3. Add splynx_partner_id column
    if not _column_exists("ar", "customer", "splynx_partner_id"):
        op.add_column(
            "customer",
            sa.Column(
                "splynx_partner_id",
                sa.String(20),
                nullable=True,
                comment="Splynx partner ID — reseller customers have this set",
            ),
            schema="ar",
        )

    # 4. Add indexes
    if not _index_exists("idx_customer_parent"):
        op.create_index(
            "idx_customer_parent",
            "customer",
            ["organization_id", "parent_customer_id"],
            schema="ar",
        )

    if not _index_exists("idx_customer_splynx_partner"):
        op.create_index(
            "idx_customer_splynx_partner",
            "customer",
            ["organization_id", "splynx_partner_id"],
            schema="ar",
        )


def downgrade() -> None:
    # Drop indexes
    if _index_exists("idx_customer_splynx_partner"):
        op.drop_index("idx_customer_splynx_partner", table_name="customer", schema="ar")

    if _index_exists("idx_customer_parent"):
        op.drop_index("idx_customer_parent", table_name="customer", schema="ar")

    # Drop FK constraint
    if _fk_exists("fk_customer_parent_customer_id"):
        op.drop_constraint(
            "fk_customer_parent_customer_id",
            "customer",
            schema="ar",
            type_="foreignkey",
        )

    # Drop columns
    if _column_exists("ar", "customer", "splynx_partner_id"):
        op.drop_column("customer", "splynx_partner_id", schema="ar")

    if _column_exists("ar", "customer", "parent_customer_id"):
        op.drop_column("customer", "parent_customer_id", schema="ar")
