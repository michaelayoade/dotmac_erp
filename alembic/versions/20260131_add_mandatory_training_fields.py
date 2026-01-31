"""Add mandatory training tracking fields to training_attendee.

Supports disciplinary corrective training and other mandatory training assignments.

Revision ID: 20260131_mandatory_training
Revises: 20260131_batch_ops
Create Date: 2026-01-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260131_mandatory_training"
down_revision = "20260131_batch_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum type for mandatory source
    mandatory_source_type = postgresql.ENUM(
        "DISCIPLINE",
        "PERFORMANCE",
        "COMPLIANCE",
        "ONBOARDING",
        "POLICY",
        name="mandatory_source_type",
        create_type=False,
    )

    # Create the enum in the database
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

    # Add mandatory training fields to training_attendee
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
    op.create_index(
        "idx_training_attendee_mandatory",
        "training_attendee",
        ["is_mandatory", "mandatory_due_date"],
        schema="training",
        postgresql_where=sa.text("is_mandatory = true"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_training_attendee_mandatory",
        table_name="training_attendee",
        schema="training",
    )

    op.drop_column("training_attendee", "mandatory_due_date", schema="training")
    op.drop_column("training_attendee", "mandatory_source_id", schema="training")
    op.drop_column("training_attendee", "mandatory_source_type", schema="training")
    op.drop_column("training_attendee", "is_mandatory", schema="training")

    op.execute("DROP TYPE IF EXISTS mandatory_source_type")
