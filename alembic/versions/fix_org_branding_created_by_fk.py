"""Fix organization_branding created_by_id FK to people.

Revision ID: fix_org_branding_created_by_fk
Revises: 5c7e3587f12a
Create Date: 2026-01-17
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "fix_org_branding_created_by_fk"
down_revision = "add_organization_branding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            SELECT tc.constraint_name INTO constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'core_org'
              AND tc.table_name = 'organization_branding'
              AND kcu.column_name = 'created_by_id'
            LIMIT 1;

            IF constraint_name IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE core_org.organization_branding DROP CONSTRAINT %I',
                    constraint_name
                );
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_type = 'FOREIGN KEY'
                  AND table_schema = 'core_org'
                  AND table_name = 'organization_branding'
                  AND constraint_name = 'fk_org_branding_created_by'
            ) THEN
                ALTER TABLE core_org.organization_branding
                ADD CONSTRAINT fk_org_branding_created_by
                FOREIGN KEY (created_by_id)
                REFERENCES people(id)
                ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE core_org.organization_branding
        DROP CONSTRAINT IF EXISTS fk_org_branding_created_by;
        """
    )
