"""Add document_type column to employee_document.

Revision ID: 20260130_emp_doc_type
Revises: 20260130_competency_jd
Create Date: 2026-01-30

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260130_emp_doc_type"
down_revision = "20260130_competency_jd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the employee document type enum (different from disciplinary document_type)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE hr.employee_document_type AS ENUM (
                'CONTRACT', 'OFFER_LETTER', 'ID_PROOF', 'PASSPORT', 'VISA',
                'WORK_PERMIT', 'EDUCATIONAL', 'PROFESSIONAL', 'MEDICAL',
                'BACKGROUND_CHECK', 'TAX_FORM', 'BANK_DETAILS', 'OTHER'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # Add document_type column if it doesn't exist
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE hr.employee_document
            ADD COLUMN document_type hr.employee_document_type NOT NULL DEFAULT 'OTHER';
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$;
    """)

    # Create index on document_type
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_emp_doc_type
        ON hr.employee_document(organization_id, document_type);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS hr.idx_emp_doc_type")
    op.execute("ALTER TABLE hr.employee_document DROP COLUMN IF EXISTS document_type")
    op.execute("DROP TYPE IF EXISTS hr.employee_document_type")
