"""Add missing values to settingdomain enum.

Revision ID: add_settingdomain_values
Revises: fix_org_branding_created_by_fk
Create Date: 2026-01-21
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_settingdomain_values"
down_revision = "fix_org_branding_created_by_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_enum
                WHERE enumlabel = 'automation'
                  AND enumtypid = 'settingdomain'::regtype
            ) THEN
                ALTER TYPE settingdomain ADD VALUE 'automation';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_enum
                WHERE enumlabel = 'email'
                  AND enumtypid = 'settingdomain'::regtype
            ) THEN
                ALTER TYPE settingdomain ADD VALUE 'email';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_enum
                WHERE enumlabel = 'features'
                  AND enumtypid = 'settingdomain'::regtype
            ) THEN
                ALTER TYPE settingdomain ADD VALUE 'features';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_enum
                WHERE enumlabel = 'reporting'
                  AND enumtypid = 'settingdomain'::regtype
            ) THEN
                ALTER TYPE settingdomain ADD VALUE 'reporting';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Postgres does not support removing enum values safely.
    pass
