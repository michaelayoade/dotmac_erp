"""Add Row Level Security policies for tenant isolation.

Revision ID: add_rls_policies
Revises: create_ifrs_schemas
Create Date: 2025-01-09

This migration adds PostgreSQL Row Level Security (RLS) policies to enforce
tenant isolation at the database level. Each table with organization_id
will have policies that restrict access based on the current session's
organization context.

Usage:
    Before executing queries, set the current organization:
    SET app.current_organization_id = 'uuid-of-organization';

    To bypass RLS (for superusers/admin operations):
    SET app.bypass_rls = 'true';
"""

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_rls_policies"
down_revision = "create_ifrs_schemas"
branch_labels = None
depends_on = None


# All IFRS schemas to scan for tables with organization_id
IFRS_SCHEMAS = [
    "ap",
    "ar",
    "audit",
    "cons",
    "core_config",
    "core_fx",
    "core_org",
    "fa",
    "fin_inst",
    "gl",
    "inv",
    "lease",
    "platform",
    "rpt",
    "tax",
]


def upgrade() -> None:
    # Create the function to get current organization from session
    op.execute("""
        CREATE OR REPLACE FUNCTION get_current_organization_id()
        RETURNS UUID AS $$
        BEGIN
            -- Check if bypass is enabled (for admin/system operations)
            IF current_setting('app.bypass_rls', true) = 'true' THEN
                RETURN NULL;
            END IF;

            -- Return the current organization ID from session
            RETURN NULLIF(current_setting('app.current_organization_id', true), '')::UUID;
        EXCEPTION
            WHEN OTHERS THEN
                RETURN NULL;
        END;
        $$ LANGUAGE plpgsql STABLE;
    """)

    # Create a function to check if RLS should be bypassed
    op.execute("""
        CREATE OR REPLACE FUNCTION should_bypass_rls()
        RETURNS BOOLEAN AS $$
        BEGIN
            RETURN COALESCE(current_setting('app.bypass_rls', true), 'false') = 'true';
        END;
        $$ LANGUAGE plpgsql STABLE;
    """)

    # Find all tables with organization_id column dynamically
    conn = op.get_bind()
    result = conn.execute(
        text("""
        SELECT t.table_schema, t.table_name
        FROM information_schema.tables t
        JOIN information_schema.columns c
            ON c.table_schema = t.table_schema AND c.table_name = t.table_name
        WHERE t.table_schema = ANY(:schemas)
        AND c.column_name = 'organization_id'
        AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_schema, t.table_name
    """),
        {"schemas": IFRS_SCHEMAS},
    )

    tenant_tables = [(row[0], row[1]) for row in result]

    # Enable RLS and create policies for each table with organization_id
    for schema, table in tenant_tables:
        full_table = f"{schema}.{table}"
        policy_name = f"{table}_tenant_isolation"

        # Enable RLS on the table
        op.execute(f"ALTER TABLE {full_table} ENABLE ROW LEVEL SECURITY;")

        # Force RLS for table owners too (important for security)
        op.execute(f"ALTER TABLE {full_table} FORCE ROW LEVEL SECURITY;")

        # Create policy for SELECT
        op.execute(f"""
            CREATE POLICY {policy_name}_select ON {full_table}
            FOR SELECT
            USING (
                should_bypass_rls()
                OR organization_id = get_current_organization_id()
            );
        """)

        # Create policy for INSERT
        op.execute(f"""
            CREATE POLICY {policy_name}_insert ON {full_table}
            FOR INSERT
            WITH CHECK (
                should_bypass_rls()
                OR organization_id = get_current_organization_id()
            );
        """)

        # Create policy for UPDATE
        op.execute(f"""
            CREATE POLICY {policy_name}_update ON {full_table}
            FOR UPDATE
            USING (
                should_bypass_rls()
                OR organization_id = get_current_organization_id()
            )
            WITH CHECK (
                should_bypass_rls()
                OR organization_id = get_current_organization_id()
            );
        """)

        # Create policy for DELETE
        op.execute(f"""
            CREATE POLICY {policy_name}_delete ON {full_table}
            FOR DELETE
            USING (
                should_bypass_rls()
                OR organization_id = get_current_organization_id()
            );
        """)


def downgrade() -> None:
    # Find all tables with organization_id column dynamically
    conn = op.get_bind()
    result = conn.execute(
        text("""
        SELECT t.table_schema, t.table_name
        FROM information_schema.tables t
        JOIN information_schema.columns c
            ON c.table_schema = t.table_schema AND c.table_name = t.table_name
        WHERE t.table_schema = ANY(:schemas)
        AND c.column_name = 'organization_id'
        AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_schema, t.table_name
    """),
        {"schemas": IFRS_SCHEMAS},
    )

    tenant_tables = [(row[0], row[1]) for row in result]

    # Drop policies and disable RLS for each table
    for schema, table in tenant_tables:
        full_table = f"{schema}.{table}"
        policy_name = f"{table}_tenant_isolation"

        op.execute(f"DROP POLICY IF EXISTS {policy_name}_select ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_insert ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_update ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_delete ON {full_table};")
        op.execute(f"ALTER TABLE {full_table} DISABLE ROW LEVEL SECURITY;")

    # Drop the helper functions
    op.execute("DROP FUNCTION IF EXISTS should_bypass_rls();")
    op.execute("DROP FUNCTION IF EXISTS get_current_organization_id();")
