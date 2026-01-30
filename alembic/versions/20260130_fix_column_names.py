"""Fix column names to match model definitions.

Revision ID: 20260130_fix_columns
Revises: 20260130_emp_doc_type
Create Date: 2026-01-30

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '20260130_fix_columns'
down_revision = '20260130_emp_doc_type'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fix competency table - AuditMixin uses created_by_id, updated_by_id
    op.execute("""
        ALTER TABLE hr.competency
        RENAME COLUMN created_by TO created_by_id;
    """)
    op.execute("""
        ALTER TABLE hr.competency
        RENAME COLUMN updated_by TO updated_by_id;
    """)

    # Fix job_description table - AuditMixin uses created_by_id, updated_by_id
    op.execute("""
        ALTER TABLE hr.job_description
        RENAME COLUMN created_by TO created_by_id;
    """)
    op.execute("""
        ALTER TABLE hr.job_description
        RENAME COLUMN updated_by TO updated_by_id;
    """)

    # Fix job_description_competency table
    op.execute("""
        ALTER TABLE hr.job_description_competency
        RENAME COLUMN created_by TO created_by_id;
    """)
    op.execute("""
        ALTER TABLE hr.job_description_competency
        RENAME COLUMN updated_by TO updated_by_id;
    """)

    # Fix hr_document table - model uses 'metadata' not 'extra_data'
    op.execute("""
        ALTER TABLE hr.hr_document
        RENAME COLUMN extra_data TO metadata;
    """)
    op.execute("""
        ALTER TABLE hr.hr_document
        RENAME COLUMN created_by TO created_by_id;
    """)
    op.execute("""
        ALTER TABLE hr.hr_document
        RENAME COLUMN updated_by TO updated_by_id;
    """)


def downgrade() -> None:
    # Revert hr_document
    op.execute("ALTER TABLE hr.hr_document RENAME COLUMN updated_by_id TO updated_by")
    op.execute("ALTER TABLE hr.hr_document RENAME COLUMN created_by_id TO created_by")
    op.execute("ALTER TABLE hr.hr_document RENAME COLUMN metadata TO extra_data")

    # Revert job_description_competency
    op.execute("ALTER TABLE hr.job_description_competency RENAME COLUMN updated_by_id TO updated_by")
    op.execute("ALTER TABLE hr.job_description_competency RENAME COLUMN created_by_id TO created_by")

    # Revert job_description
    op.execute("ALTER TABLE hr.job_description RENAME COLUMN updated_by_id TO updated_by")
    op.execute("ALTER TABLE hr.job_description RENAME COLUMN created_by_id TO created_by")

    # Revert competency
    op.execute("ALTER TABLE hr.competency RENAME COLUMN updated_by_id TO updated_by")
    op.execute("ALTER TABLE hr.competency RENAME COLUMN created_by_id TO created_by")
