"""Add payroll_number_sequence table for idempotent numbering.

Revision ID: 20260130_add_payroll_number_sequence
Revises:
Create Date: 2026-01-30

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260130_add_payroll_number_sequence"
down_revision = "create_people_schemas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the payroll_number_sequence table for idempotent number generation
    op.create_table(
        "payroll_number_sequence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("prefix", sa.String(20), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("formatted_number", sa.String(50), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "prefix",
            "year",
            "sequence_number",
            name="uq_payroll_number_org_prefix_year_seq",
        ),
        schema="people",
    )

    # Index for faster lookups when finding max sequence
    op.create_index(
        "ix_payroll_number_seq_org_prefix_year",
        "payroll_number_sequence",
        ["organization_id", "prefix", "year"],
        schema="people",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payroll_number_seq_org_prefix_year",
        table_name="payroll_number_sequence",
        schema="people",
    )
    op.drop_table("payroll_number_sequence", schema="people")
