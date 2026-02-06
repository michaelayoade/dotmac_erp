"""Update email_module enum to match main application modules.

Changes enum values from:
  PAYROLL, HR, EXPENSE, FINANCE, SUPPORT, SYSTEM, MARKETING
To:
  PEOPLE, FINANCE, OPERATIONS, ADMIN

Migration mapping:
  - PAYROLL → PEOPLE
  - HR → PEOPLE
  - EXPENSE → FINANCE (merged - EXPENSE deleted if FINANCE exists)
  - FINANCE → FINANCE (unchanged)
  - SUPPORT → OPERATIONS
  - SYSTEM → ADMIN
  - MARKETING → ADMIN (merged - MARKETING deleted if SYSTEM exists)

Revision ID: 20260131_update_email_module_enum
Revises: 20260131_add_mandatory_training_fields
Create Date: 2026-01-31
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260131_update_email_module_enum"
down_revision = "20260131_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Update email_module enum to new values aligned with main modules.

    PostgreSQL limitation: New enum values added with ALTER TYPE ADD VALUE
    cannot be used in the same transaction. Solution: Convert column to TEXT,
    transform data, create fresh enum, convert back.

    Also handles merging: when multiple old modules map to same new module,
    delete duplicates before conversion to avoid unique constraint violation.
    """

    # Step 1: Convert column to TEXT to allow data transformation
    op.execute("""
        ALTER TABLE module_email_routing
        ALTER COLUMN module TYPE TEXT
    """)

    # Step 2: Delete duplicates that would conflict after merge
    # Keep FINANCE, delete EXPENSE (if both exist for same org)
    op.execute("""
        DELETE FROM module_email_routing r1
        WHERE r1.module = 'EXPENSE'
        AND EXISTS (
            SELECT 1 FROM module_email_routing r2
            WHERE r2.organization_id = r1.organization_id
            AND r2.module = 'FINANCE'
        )
    """)

    # Keep HR, delete PAYROLL (if both exist for same org)
    op.execute("""
        DELETE FROM module_email_routing r1
        WHERE r1.module = 'PAYROLL'
        AND EXISTS (
            SELECT 1 FROM module_email_routing r2
            WHERE r2.organization_id = r1.organization_id
            AND r2.module = 'HR'
        )
    """)

    # Keep SYSTEM, delete MARKETING (if both exist for same org)
    op.execute("""
        DELETE FROM module_email_routing r1
        WHERE r1.module = 'MARKETING'
        AND EXISTS (
            SELECT 1 FROM module_email_routing r2
            WHERE r2.organization_id = r1.organization_id
            AND r2.module = 'SYSTEM'
        )
    """)

    # Step 3: Update existing routing records to new values
    op.execute("""
        UPDATE module_email_routing
        SET module = CASE module
            WHEN 'PAYROLL' THEN 'PEOPLE'
            WHEN 'HR' THEN 'PEOPLE'
            WHEN 'EXPENSE' THEN 'FINANCE'
            WHEN 'SUPPORT' THEN 'OPERATIONS'
            WHEN 'SYSTEM' THEN 'ADMIN'
            WHEN 'MARKETING' THEN 'ADMIN'
            ELSE module
        END
    """)

    # Step 4: Drop old enum type
    op.execute("DROP TYPE IF EXISTS email_module")

    # Step 5: Create new enum with only the desired values
    op.execute("""
        CREATE TYPE email_module AS ENUM (
            'PEOPLE', 'FINANCE', 'OPERATIONS', 'ADMIN'
        )
    """)

    # Step 6: Convert column back to enum type
    op.execute("""
        ALTER TABLE module_email_routing
        ALTER COLUMN module TYPE email_module
        USING module::email_module
    """)


def downgrade() -> None:
    """Revert to original email_module enum values."""

    # Step 1: Convert column to TEXT
    op.execute("""
        ALTER TABLE module_email_routing
        ALTER COLUMN module TYPE TEXT
    """)

    # Step 2: Map new values back to old values (best effort)
    op.execute("""
        UPDATE module_email_routing
        SET module = CASE module
            WHEN 'PEOPLE' THEN 'HR'
            WHEN 'OPERATIONS' THEN 'SUPPORT'
            WHEN 'ADMIN' THEN 'SYSTEM'
            ELSE module
        END
    """)

    # Step 3: Drop new enum type
    op.execute("DROP TYPE IF EXISTS email_module")

    # Step 4: Create old enum type
    op.execute("""
        CREATE TYPE email_module AS ENUM (
            'PAYROLL', 'HR', 'EXPENSE', 'FINANCE', 'SUPPORT', 'SYSTEM', 'MARKETING'
        )
    """)

    # Step 5: Convert column back to enum type
    op.execute("""
        ALTER TABLE module_email_routing
        ALTER COLUMN module TYPE email_module
        USING module::email_module
    """)
