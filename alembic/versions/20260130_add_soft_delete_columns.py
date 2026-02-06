"""Add soft delete columns to competency and job_description tables.

Revision ID: 20260130_soft_delete
Revises: 20260130_fix_columns
Create Date: 2026-01-30

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260130_soft_delete"
down_revision = "20260130_fix_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add missing SoftDeleteMixin columns to competency
    op.execute("""
        ALTER TABLE hr.competency
        ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS deleted_by_id UUID REFERENCES people(id);
    """)

    # Add missing SoftDeleteMixin columns to job_description
    op.execute("""
        ALTER TABLE hr.job_description
        ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS deleted_by_id UUID REFERENCES people(id);
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE hr.job_description DROP COLUMN IF EXISTS deleted_by_id")
    op.execute("ALTER TABLE hr.job_description DROP COLUMN IF EXISTS deleted_at")
    op.execute("ALTER TABLE hr.competency DROP COLUMN IF EXISTS deleted_by_id")
    op.execute("ALTER TABLE hr.competency DROP COLUMN IF EXISTS deleted_at")
