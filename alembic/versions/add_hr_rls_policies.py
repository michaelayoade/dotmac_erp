"""Add RLS policies for HR schema tables.

Revision ID: add_hr_rls_policies
Revises: add_rls_policies
Create Date: 2025-01-20

This migration adds PostgreSQL Row Level Security (RLS) policies for the
HR schema tables. Uses the same pattern as the finance schemas - tenant
isolation based on organization_id with bypass capability.
"""
from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = "add_hr_rls_policies"
down_revision = "create_hr_core_tables"
branch_labels = None
depends_on = None


# HR schemas to add RLS policies for
HR_SCHEMAS = ["hr"]


def upgrade() -> None:
    """Enable RLS and create policies for HR schema tables."""
    conn = op.get_bind()

    # Find all tables with organization_id column in HR schemas
    result = conn.execute(text("""
        SELECT t.table_schema, t.table_name
        FROM information_schema.tables t
        JOIN information_schema.columns c
            ON c.table_schema = t.table_schema AND c.table_name = t.table_name
        WHERE t.table_schema = ANY(:schemas)
        AND c.column_name = 'organization_id'
        AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_schema, t.table_name
    """), {"schemas": HR_SCHEMAS})

    tenant_tables = [(row[0], row[1]) for row in result]

    # Enable RLS and create policies for each table with organization_id
    for schema, table in tenant_tables:
        full_table = f"{schema}.{table}"
        policy_name = f"{table}_tenant_isolation"

        # Enable RLS on the table
        op.execute(f"ALTER TABLE {full_table} ENABLE ROW LEVEL SECURITY;")

        # Force RLS for table owners too
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
    """Drop RLS policies and disable RLS for HR schema tables."""
    conn = op.get_bind()

    # Find all tables with organization_id column in HR schemas
    result = conn.execute(text("""
        SELECT t.table_schema, t.table_name
        FROM information_schema.tables t
        JOIN information_schema.columns c
            ON c.table_schema = t.table_schema AND c.table_name = t.table_name
        WHERE t.table_schema = ANY(:schemas)
        AND c.column_name = 'organization_id'
        AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_schema, t.table_name
    """), {"schemas": HR_SCHEMAS})

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
