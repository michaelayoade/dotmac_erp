"""Add mandatory training tracking fields to training_attendee.

Supports disciplinary corrective training and other mandatory training assignments.

Revision ID: 20260131_mandatory_training
Revises: 20260131_batch_ops
Create Date: 2026-01-31
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260131_mandatory_training"
down_revision = "20260131_batch_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Create enum type for mandatory source
    existing_enums = [e["name"] for e in inspector.get_enums()]
    if "mandatory_source_type" not in existing_enums:
        op.execute(
            """
            DO $$ BEGIN
                CREATE TYPE mandatory_source_type AS ENUM (
                    'DISCIPLINE', 'PERFORMANCE', 'COMPLIANCE', 'ONBOARDING', 'POLICY'
                );
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END $$;
            """
        )

    mandatory_source_type = postgresql.ENUM(
        "DISCIPLINE",
        "PERFORMANCE",
        "COMPLIANCE",
        "ONBOARDING",
        "POLICY",
        name="mandatory_source_type",
        create_type=False,
    )

    if not inspector.has_table("training_attendee", schema="training"):
        return

    columns = {
        col["name"]
        for col in inspector.get_columns("training_attendee", schema="training")
    }

    # Add mandatory training fields to training_attendee
    if "is_mandatory" not in columns:
        op.add_column(
            "training_attendee",
            sa.Column(
                "is_mandatory",
                sa.Boolean(),
                nullable=False,
                server_default="false",
                comment="Whether this training is mandatory for the employee",
            ),
            schema="training",
        )

    if "mandatory_source_type" not in columns:
        op.add_column(
            "training_attendee",
            sa.Column(
                "mandatory_source_type",
                mandatory_source_type,
                nullable=True,
                comment="What triggered the mandatory training (e.g., DISCIPLINE, PERFORMANCE)",
            ),
            schema="training",
        )

    if "mandatory_source_id" not in columns:
        op.add_column(
            "training_attendee",
            sa.Column(
                "mandatory_source_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
                comment="ID of the source record (e.g., discipline case ID)",
            ),
            schema="training",
        )

    if "mandatory_due_date" not in columns:
        op.add_column(
            "training_attendee",
            sa.Column(
                "mandatory_due_date",
                sa.Date(),
                nullable=True,
                comment="Deadline for completing mandatory training",
            ),
            schema="training",
        )

    # Add index for finding pending mandatory trainings
    indexes = {
        idx["name"]
        for idx in inspector.get_indexes("training_attendee", schema="training")
        if idx.get("name")
    }
    if "idx_training_attendee_mandatory" not in indexes:
        op.create_index(
            "idx_training_attendee_mandatory",
            "training_attendee",
            ["is_mandatory", "mandatory_due_date"],
            schema="training",
            postgresql_where=sa.text("is_mandatory = true"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("training_attendee", schema="training"):
        return

    indexes = {
        idx["name"]
        for idx in inspector.get_indexes("training_attendee", schema="training")
        if idx.get("name")
    }
    if "idx_training_attendee_mandatory" in indexes:
        op.drop_index(
            "idx_training_attendee_mandatory",
            table_name="training_attendee",
            schema="training",
        )

    columns = {
        col["name"]
        for col in inspector.get_columns("training_attendee", schema="training")
    }
    if "mandatory_due_date" in columns:
        op.drop_column("training_attendee", "mandatory_due_date", schema="training")
    if "mandatory_source_id" in columns:
        op.drop_column("training_attendee", "mandatory_source_id", schema="training")
    if "mandatory_source_type" in columns:
        op.drop_column("training_attendee", "mandatory_source_type", schema="training")
    if "is_mandatory" in columns:
        op.drop_column("training_attendee", "is_mandatory", schema="training")

    op.execute("DROP TYPE IF EXISTS mandatory_source_type")
