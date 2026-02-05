"""Make Person.organization_id NOT NULL for proper tenant isolation.

Revision ID: make_person_org_required
Revises: add_rls_policies
Create Date: 2025-01-10

This migration makes organization_id required on the people table to ensure
all users are properly scoped to a tenant/organization.

Before running this migration, ensure all existing people have an organization_id set.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "make_person_org_required"
down_revision = "add_audit_schema"
branch_labels = None
depends_on = None


# Default organization UUID for migrating orphaned users
DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"



def upgrade() -> None:
    # Determine currency from existing organizations, fallback to USD
    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            "SELECT functional_currency_code, presentation_currency_code "
            "FROM core_org.organization LIMIT 1"
        )
    ).first()
    functional_currency = row[0] if row else "USD"
    presentation_currency = row[1] if row else "USD"

    # First, ensure the default organization exists
    # This is idempotent - it won't fail if org already exists
    op.execute(
        f"""
        INSERT INTO core_org.organization (
            organization_id,
            organization_code,
            legal_name,
            functional_currency_code,
            presentation_currency_code,
            fiscal_year_end_month,
            fiscal_year_end_day,
            is_active,
            created_at,
            updated_at
        ) VALUES (
            '{DEFAULT_ORG_ID}'::uuid,
            'DEFAULT',
            'Default Organization',
            '{functional_currency}',
            '{presentation_currency}',
            12,
            31,
            true,
            NOW(),
            NOW()
        )
        ON CONFLICT (organization_id) DO NOTHING;
        """
    )

    # Update any existing people without an organization
    op.execute(
        f"""
        UPDATE people
        SET organization_id = '{DEFAULT_ORG_ID}'::uuid
        WHERE organization_id IS NULL;
        """
    )

    # Now make the column NOT NULL
    op.alter_column(
        "people",
        "organization_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    # Add the foreign key constraint if not exists
    # First check if constraint exists
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'fk_people_organization_id'
                AND table_name = 'people'
            ) THEN
                ALTER TABLE people
                ADD CONSTRAINT fk_people_organization_id
                FOREIGN KEY (organization_id)
                REFERENCES core_org.organization(organization_id)
                ON DELETE RESTRICT;
            END IF;
        END $$;
        """
    )

    # Add RLS policy to people table if not exists
    op.execute(
        """
        DO $$
        BEGIN
            -- Enable RLS on people table
            ALTER TABLE people ENABLE ROW LEVEL SECURITY;
            ALTER TABLE people FORCE ROW LEVEL SECURITY;

            -- Create policy for SELECT
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'people' AND policyname = 'people_tenant_isolation_select'
            ) THEN
                CREATE POLICY people_tenant_isolation_select ON people
                FOR SELECT
                USING (
                    should_bypass_rls()
                    OR organization_id = get_current_organization_id()
                );
            END IF;

            -- Create policy for INSERT
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'people' AND policyname = 'people_tenant_isolation_insert'
            ) THEN
                CREATE POLICY people_tenant_isolation_insert ON people
                FOR INSERT
                WITH CHECK (
                    should_bypass_rls()
                    OR organization_id = get_current_organization_id()
                );
            END IF;

            -- Create policy for UPDATE
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'people' AND policyname = 'people_tenant_isolation_update'
            ) THEN
                CREATE POLICY people_tenant_isolation_update ON people
                FOR UPDATE
                USING (
                    should_bypass_rls()
                    OR organization_id = get_current_organization_id()
                );
            END IF;

            -- Create policy for DELETE
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'people' AND policyname = 'people_tenant_isolation_delete'
            ) THEN
                CREATE POLICY people_tenant_isolation_delete ON people
                FOR DELETE
                USING (
                    should_bypass_rls()
                    OR organization_id = get_current_organization_id()
                );
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Remove RLS policies from people table
    op.execute(
        """
        DROP POLICY IF EXISTS people_tenant_isolation_select ON people;
        DROP POLICY IF EXISTS people_tenant_isolation_insert ON people;
        DROP POLICY IF EXISTS people_tenant_isolation_update ON people;
        DROP POLICY IF EXISTS people_tenant_isolation_delete ON people;
        ALTER TABLE people DISABLE ROW LEVEL SECURITY;
        """
    )

    # Remove foreign key constraint
    op.execute(
        """
        ALTER TABLE people
        DROP CONSTRAINT IF EXISTS fk_people_organization_id;
        """
    )

    # Make column nullable again
    op.alter_column(
        "people",
        "organization_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
