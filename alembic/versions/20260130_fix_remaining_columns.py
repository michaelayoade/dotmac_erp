"""Fix remaining column mismatches.

Revision ID: 20260130_fix_remaining
Revises: 20260130_soft_delete
Create Date: 2026-01-30

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260130_fix_remaining'
down_revision = '20260130_soft_delete'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # hr_document model uses created_by/updated_by (not _id suffix)
    # The fix_columns migration incorrectly renamed them
    op.execute("""
        ALTER TABLE hr.hr_document
        RENAME COLUMN created_by_id TO created_by;
    """)
    op.execute("""
        ALTER TABLE hr.hr_document
        RENAME COLUMN updated_by_id TO updated_by;
    """)

    # job_description uses ERPNextSyncMixin which needs last_synced_at
    # The migration only added erpnext_id, erpnext_name, erpnext_modified, erpnext_synced_at
    op.execute("""
        ALTER TABLE hr.job_description
        ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ;
    """)

    # Also fix erpnext_synced_at -> last_synced_at if needed (check ERPNextSyncMixin)
    # The mixin uses last_synced_at, but migration used erpnext_synced_at
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE hr.job_description
            DROP COLUMN IF EXISTS erpnext_synced_at;
        EXCEPTION WHEN undefined_column THEN NULL;
        END $$;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE hr.job_description DROP COLUMN IF EXISTS last_synced_at")
    op.execute("ALTER TABLE hr.hr_document RENAME COLUMN updated_by TO updated_by_id")
    op.execute("ALTER TABLE hr.hr_document RENAME COLUMN created_by TO created_by_id")
