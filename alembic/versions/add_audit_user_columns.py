"""Add audit user columns to customer, supplier, and account tables.

Revision ID: add_audit_user_columns
Revises: add_remaining_indexes_and_fks
Create Date: 2026-01-16

This migration adds created_by_user_id and updated_by_user_id columns to:
- ar.customer
- ap.supplier
- gl.account

These columns track who created/imported records and who last updated them.
The columns reference public.people.id but we don't create FK constraints
to avoid cross-schema FK complexity with RLS.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = "add_audit_user_columns"
down_revision = "add_remaining_indexes_and_fks"
branch_labels = None
depends_on = None


# Tables to add audit columns to
TABLES = [
    ("ar", "customer"),
    ("ap", "supplier"),
    ("gl", "account"),
]


def _has_column(inspector, table_name: str, column_name: str, schema: str) -> bool:
    """Check if a column exists in a table."""
    return column_name in {
        column["name"] for column in inspector.get_columns(table_name, schema=schema)
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for schema, table in TABLES:
        if not inspector.has_table(table, schema=schema):
            continue

        # Add created_by_user_id if it doesn't exist
        # No FK constraint - references public.people.id but cross-schema FKs
        # with RLS can be problematic. Application logic handles the relationship.
        if not _has_column(inspector, table, "created_by_user_id", schema):
            op.add_column(
                table,
                sa.Column(
                    "created_by_user_id",
                    UUID(as_uuid=True),
                    nullable=True,
                    comment="User ID who created/imported this record (references people.id)",
                ),
                schema=schema,
            )

        # Add updated_by_user_id if it doesn't exist
        if not _has_column(inspector, table, "updated_by_user_id", schema):
            op.add_column(
                table,
                sa.Column(
                    "updated_by_user_id",
                    UUID(as_uuid=True),
                    nullable=True,
                    comment="User ID who last updated this record (references people.id)",
                ),
                schema=schema,
            )

        # Create indexes for the new columns for efficient lookups
        created_by_idx = f"idx_{table}_created_by"
        updated_by_idx = f"idx_{table}_updated_by"

        # Check if indexes exist before creating
        existing_indexes = {
            idx["name"] for idx in inspector.get_indexes(table, schema=schema)
        }

        if created_by_idx not in existing_indexes:
            op.create_index(
                created_by_idx,
                table,
                ["created_by_user_id"],
                schema=schema,
            )

        if updated_by_idx not in existing_indexes:
            op.create_index(
                updated_by_idx,
                table,
                ["updated_by_user_id"],
                schema=schema,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for schema, table in TABLES:
        if not inspector.has_table(table, schema=schema):
            continue

        # Drop indexes first
        existing_indexes = {
            idx["name"] for idx in inspector.get_indexes(table, schema=schema)
        }

        created_by_idx = f"idx_{table}_created_by"
        updated_by_idx = f"idx_{table}_updated_by"

        if updated_by_idx in existing_indexes:
            op.drop_index(updated_by_idx, table_name=table, schema=schema)

        if created_by_idx in existing_indexes:
            op.drop_index(created_by_idx, table_name=table, schema=schema)

        # Drop columns
        if _has_column(inspector, table, "updated_by_user_id", schema):
            op.drop_column(table, "updated_by_user_id", schema=schema)

        if _has_column(inspector, table, "created_by_user_id", schema):
            op.drop_column(table, "created_by_user_id", schema=schema)
