"""Add batch operation tracking table.

Revision ID: 20260131_batch_ops
Revises:
Create Date: 2026-01-31

Tracks script runs, bulk imports, and other batch operations for audit purposes.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260131_batch_ops"
down_revision = "20260131_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE batch_operation_type AS ENUM (
                'script', 'import', 'sync', 'migration', 'bulk_update', 'cleanup'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)

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
    op.create_table(
        "batch_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "operation_type",
            postgresql.ENUM(
                "script", "import", "sync", "migration", "bulk_update", "cleanup",
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
                "running", "completed", "failed", "rolled_back",
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

    # Add index for querying by operation type and status
    op.create_index(
        "ix_batch_operations_type_status",
        "batch_operations",
        ["operation_type", "status"],
    )

    # Add index for querying by date
    op.create_index(
        "ix_batch_operations_started_at",
        "batch_operations",
        ["started_at"],
    )

    # Add batch_operation_id column to key tables for tracking
    # These columns are nullable - only populated for batch-created records

    op.add_column(
        "people",
        sa.Column(
            "batch_operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batch_operations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

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
    op.create_index(
        "ix_people_batch_operation_id",
        "people",
        ["batch_operation_id"],
        postgresql_where=sa.text("batch_operation_id IS NOT NULL"),
    )

    op.create_index(
        "ix_employee_batch_operation_id",
        "employee",
        ["batch_operation_id"],
        schema="hr",
        postgresql_where=sa.text("batch_operation_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_employee_batch_operation_id", table_name="employee", schema="hr")
    op.drop_index("ix_people_batch_operation_id", table_name="people")

    op.drop_column("salary_structure_assignment", "batch_operation_id", schema="payroll")
    op.drop_column("employee", "batch_operation_id", schema="hr")
    op.drop_column("people", "batch_operation_id")

    op.drop_index("ix_batch_operations_started_at", table_name="batch_operations")
    op.drop_index("ix_batch_operations_type_status", table_name="batch_operations")
    op.drop_table("batch_operations")

    op.execute("DROP TYPE IF EXISTS batch_operation_status")
    op.execute("DROP TYPE IF EXISTS batch_operation_type")
