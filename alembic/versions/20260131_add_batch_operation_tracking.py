"""Add batch operation tracking table.

Revision ID: 20260131_batch_ops
Revises:
Create Date: 2026-01-31

Tracks script runs, bulk imports, and other batch operations for audit purposes.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260131_batch_ops"
down_revision = "20260131_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Create enum types
    existing_enums = [e["name"] for e in inspector.get_enums()]

    if "batch_operation_type" not in existing_enums:
        op.execute("""
            DO $$ BEGIN
                CREATE TYPE batch_operation_type AS ENUM (
                    'script', 'import', 'sync', 'migration', 'bulk_update', 'cleanup'
                );
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
        """)

    if "batch_operation_status" not in existing_enums:
        op.execute("""
            DO $$ BEGIN
                CREATE TYPE batch_operation_status AS ENUM (
                    'running', 'completed', 'failed', 'rolled_back'
                );
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
        """)

    # Create batch_operations table
    if not inspector.has_table("batch_operations"):
        op.create_table(
            "batch_operations",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "organization_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "core_org.organization.organization_id", ondelete="CASCADE"
                ),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "operation_type",
                postgresql.ENUM(
                    "script",
                    "import",
                    "sync",
                    "migration",
                    "bulk_update",
                    "cleanup",
                    name="batch_operation_type",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("operation_name", sa.String(120), nullable=False),
            sa.Column("description", sa.Text),
            sa.Column("source_file", sa.String(512)),
            sa.Column("source_checksum", sa.String(64)),
            sa.Column(
                "started_by_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("people.id", ondelete="SET NULL"),
            ),
            sa.Column(
                "started_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "running",
                    "completed",
                    "failed",
                    "rolled_back",
                    name="batch_operation_status",
                    create_type=False,
                ),
                nullable=False,
                server_default="running",
            ),
            sa.Column("records_created", sa.Integer, server_default="0"),
            sa.Column("records_updated", sa.Integer, server_default="0"),
            sa.Column("records_skipped", sa.Integer, server_default="0"),
            sa.Column("records_failed", sa.Integer, server_default="0"),
            sa.Column("error_message", sa.Text),
            sa.Column("created_entity_ids", postgresql.JSONB),
            sa.Column("metadata", postgresql.JSONB),
        )

    # Add indexes for batch_operations table
    if inspector.has_table("batch_operations"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("batch_operations")
            if idx.get("name")
        }
        if "ix_batch_operations_type_status" not in indexes:
            op.create_index(
                "ix_batch_operations_type_status",
                "batch_operations",
                ["operation_type", "status"],
            )
        if "ix_batch_operations_started_at" not in indexes:
            op.create_index(
                "ix_batch_operations_started_at",
                "batch_operations",
                ["started_at"],
            )

    # Add batch_operation_id column to key tables for tracking
    if inspector.has_table("people"):
        columns = {col["name"] for col in inspector.get_columns("people")}
        if "batch_operation_id" not in columns:
            op.add_column(
                "people",
                sa.Column(
                    "batch_operation_id",
                    postgresql.UUID(as_uuid=True),
                    sa.ForeignKey("batch_operations.id", ondelete="SET NULL"),
                    nullable=True,
                ),
            )

    if inspector.has_table("employee", schema="hr"):
        columns = {
            col["name"] for col in inspector.get_columns("employee", schema="hr")
        }
        if "batch_operation_id" not in columns:
            op.add_column(
                "employee",
                sa.Column(
                    "batch_operation_id",
                    postgresql.UUID(as_uuid=True),
                    sa.ForeignKey("batch_operations.id", ondelete="SET NULL"),
                    nullable=True,
                ),
                schema="hr",
            )

    if inspector.has_table("salary_structure_assignment", schema="payroll"):
        columns = {
            col["name"]
            for col in inspector.get_columns(
                "salary_structure_assignment", schema="payroll"
            )
        }
        if "batch_operation_id" not in columns:
            op.add_column(
                "salary_structure_assignment",
                sa.Column(
                    "batch_operation_id",
                    postgresql.UUID(as_uuid=True),
                    sa.ForeignKey("public.batch_operations.id", ondelete="SET NULL"),
                    nullable=True,
                ),
                schema="payroll",
            )

    # Create indexes for efficient batch rollback queries
    if inspector.has_table("people"):
        indexes = {
            idx["name"] for idx in inspector.get_indexes("people") if idx.get("name")
        }
        if "ix_people_batch_operation_id" not in indexes:
            op.create_index(
                "ix_people_batch_operation_id",
                "people",
                ["batch_operation_id"],
                postgresql_where=sa.text("batch_operation_id IS NOT NULL"),
            )

    if inspector.has_table("employee", schema="hr"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("employee", schema="hr")
            if idx.get("name")
        }
        if "ix_employee_batch_operation_id" not in indexes:
            op.create_index(
                "ix_employee_batch_operation_id",
                "employee",
                ["batch_operation_id"],
                schema="hr",
                postgresql_where=sa.text("batch_operation_id IS NOT NULL"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("employee", schema="hr"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("employee", schema="hr")
            if idx.get("name")
        }
        if "ix_employee_batch_operation_id" in indexes:
            op.drop_index(
                "ix_employee_batch_operation_id",
                table_name="employee",
                schema="hr",
            )

    if inspector.has_table("people"):
        indexes = {
            idx["name"] for idx in inspector.get_indexes("people") if idx.get("name")
        }
        if "ix_people_batch_operation_id" in indexes:
            op.drop_index("ix_people_batch_operation_id", table_name="people")

    if inspector.has_table("salary_structure_assignment", schema="payroll"):
        columns = {
            col["name"]
            for col in inspector.get_columns(
                "salary_structure_assignment", schema="payroll"
            )
        }
        if "batch_operation_id" in columns:
            op.drop_column(
                "salary_structure_assignment",
                "batch_operation_id",
                schema="payroll",
            )

    if inspector.has_table("employee", schema="hr"):
        columns = {
            col["name"] for col in inspector.get_columns("employee", schema="hr")
        }
        if "batch_operation_id" in columns:
            op.drop_column("employee", "batch_operation_id", schema="hr")

    if inspector.has_table("people"):
        columns = {col["name"] for col in inspector.get_columns("people")}
        if "batch_operation_id" in columns:
            op.drop_column("people", "batch_operation_id")

    if inspector.has_table("batch_operations"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("batch_operations")
            if idx.get("name")
        }
        if "ix_batch_operations_started_at" in indexes:
            op.drop_index(
                "ix_batch_operations_started_at", table_name="batch_operations"
            )
        if "ix_batch_operations_type_status" in indexes:
            op.drop_index(
                "ix_batch_operations_type_status", table_name="batch_operations"
            )
        op.drop_table("batch_operations")

    op.execute("DROP TYPE IF EXISTS batch_operation_status")
    op.execute("DROP TYPE IF EXISTS batch_operation_type")
