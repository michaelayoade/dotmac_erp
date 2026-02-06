"""Add sync schema and tables for ERPNext migration.

Revision ID: add_sync_tables
Revises: add_flexible_tax_support
Create Date: 2024-01-16

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision: str = "add_sync_tables"
down_revision: Union[str, None] = "add_remaining_indexes_and_fks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create sync schema
    op.execute("CREATE SCHEMA IF NOT EXISTS sync")

    # Create enum types
    bind = op.get_bind()
    ensure_enum(
        bind,
        "sync_status",
        "PENDING",
        "SYNCED",
        "FAILED",
        "SKIPPED",
        schema="sync",
    )
    ensure_enum(bind, "sync_type", "FULL", "INCREMENTAL", schema="sync")
    ensure_enum(
        bind,
        "sync_job_status",
        "PENDING",
        "RUNNING",
        "COMPLETED",
        "COMPLETED_WITH_ERRORS",
        "FAILED",
        "CANCELLED",
        schema="sync",
    )

    # Create sync_entity table
    op.create_table(
        "sync_entity",
        sa.Column(
            "sync_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(50), nullable=False),
        sa.Column("source_doctype", sa.String(100), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("target_table", sa.String(100), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "sync_status",
            postgresql.ENUM(
                "PENDING",
                "SYNCED",
                "FAILED",
                "SKIPPED",
                name="sync_status",
                schema="sync",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("source_modified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
            name="fk_sync_entity_org",
        ),
        sa.PrimaryKeyConstraint("sync_id"),
        sa.UniqueConstraint(
            "organization_id",
            "source_system",
            "source_doctype",
            "source_name",
            name="uq_sync_entity_source",
        ),
        schema="sync",
    )
    op.create_index(
        "idx_sync_entity_org", "sync_entity", ["organization_id"], schema="sync"
    )
    op.create_index(
        "idx_sync_entity_status", "sync_entity", ["sync_status"], schema="sync"
    )
    op.create_index(
        "idx_sync_entity_target",
        "sync_entity",
        ["target_table", "target_id"],
        schema="sync",
    )

    # Create sync_history table
    op.create_table(
        "sync_history",
        sa.Column(
            "history_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(50), nullable=False),
        sa.Column(
            "sync_type",
            postgresql.ENUM(
                "FULL",
                "INCREMENTAL",
                name="sync_type",
                schema="sync",
                create_type=False,
            ),
            nullable=False,
            server_default="FULL",
        ),
        sa.Column("entity_types", postgresql.JSONB(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "RUNNING",
                "COMPLETED",
                "COMPLETED_WITH_ERRORS",
                "FAILED",
                "CANCELLED",
                name="sync_job_status",
                schema="sync",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("synced_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", postgresql.JSONB(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
            name="fk_sync_history_org",
        ),
        sa.PrimaryKeyConstraint("history_id"),
        schema="sync",
    )
    op.create_index(
        "idx_sync_history_org", "sync_history", ["organization_id"], schema="sync"
    )
    op.create_index(
        "idx_sync_history_status", "sync_history", ["status"], schema="sync"
    )
    op.create_index(
        "idx_sync_history_started", "sync_history", ["started_at"], schema="sync"
    )


def downgrade() -> None:
    op.drop_table("sync_history", schema="sync")
    op.drop_table("sync_entity", schema="sync")
    op.execute("DROP TYPE IF EXISTS sync.sync_job_status")
    op.execute("DROP TYPE IF EXISTS sync.sync_type")
    op.execute("DROP TYPE IF EXISTS sync.sync_status")
    op.execute("DROP SCHEMA IF EXISTS sync")
