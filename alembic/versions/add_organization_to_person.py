"""Add organization_id to Person model for multi-tenancy.

Revision ID: add_organization_to_person
Revises: 799a0ecebdd4
Create Date: 2025-01-09

This migration adds the organization_id foreign key to the Person (people) table,
linking users to their organization for multi-tenant support.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "add_organization_to_person"
down_revision = "799a0ecebdd4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add organization_id column to people table
    # Note: FK constraint will be added after IFRS schemas are created
    op.add_column(
        "people",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    # Create index for faster lookups
    op.create_index(
        "ix_people_organization_id",
        "people",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    # Drop index first
    op.drop_index("ix_people_organization_id", table_name="people")

    # Drop the column
    op.drop_column("people", "organization_id")
