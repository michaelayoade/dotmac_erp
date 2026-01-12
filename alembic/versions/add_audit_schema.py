"""Add audit schema and tables.

Revision ID: add_audit_schema
Revises: add_auth_constraints
Create Date: 2025-01-09

This migration adds the audit schema with 4 tables:
- audit_log: Immutable audit trail
- approval_workflow: Workflow definitions
- approval_request: Pending approval requests
- approval_decision: Approval decisions
"""
from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = "add_audit_schema"
down_revision = "add_auth_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create audit schema
    op.execute("CREATE SCHEMA IF NOT EXISTS audit")

    # Import models to get metadata
    from app.db import Base
    import app.models.ifrs.audit  # noqa: F401 - registers models

    # Get connection and create audit tables
    bind = op.get_bind()

    # Create all tables in the audit schema
    Base.metadata.create_all(
        bind=bind,
        tables=[
            t for t in Base.metadata.sorted_tables
            if t.schema == "audit"
        ],
    )

    # Add RLS policies to audit tables with organization_id
    result = bind.execute(text("""
        SELECT t.table_name
        FROM information_schema.tables t
        JOIN information_schema.columns c
            ON c.table_schema = t.table_schema AND c.table_name = t.table_name
        WHERE t.table_schema = 'audit'
        AND c.column_name = 'organization_id'
        AND t.table_type = 'BASE TABLE'
    """))

    for row in result:
        table = row[0]
        full_table = f"audit.{table}"
        policy_name = f"{table}_tenant_isolation"

        op.execute(f"ALTER TABLE {full_table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {full_table} FORCE ROW LEVEL SECURITY;")

        # Drop existing policies if they exist (idempotent)
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_select ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_insert ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_update ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_delete ON {full_table};")

        op.execute(f"""
            CREATE POLICY {policy_name}_select ON {full_table}
            FOR SELECT
            USING (
                should_bypass_rls()
                OR organization_id = get_current_organization_id()
            );
        """)

        op.execute(f"""
            CREATE POLICY {policy_name}_insert ON {full_table}
            FOR INSERT
            WITH CHECK (
                should_bypass_rls()
                OR organization_id = get_current_organization_id()
            );
        """)

        # Note: audit_log should be append-only, but we still need update/delete
        # policies for completeness. A trigger should block actual updates/deletes.
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

        op.execute(f"""
            CREATE POLICY {policy_name}_delete ON {full_table}
            FOR DELETE
            USING (
                should_bypass_rls()
                OR organization_id = get_current_organization_id()
            );
        """)


def downgrade() -> None:
    # Import models to get table names
    from app.db import Base
    import app.models.ifrs.audit  # noqa: F401

    bind = op.get_bind()

    # Drop RLS policies from audit tables
    result = bind.execute(text("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'audit' AND table_type = 'BASE TABLE'
    """))

    for row in result:
        table = row[0]
        full_table = f"audit.{table}"
        policy_name = f"{table}_tenant_isolation"

        op.execute(f"DROP POLICY IF EXISTS {policy_name}_select ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_insert ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_update ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_delete ON {full_table};")
        op.execute(f"ALTER TABLE {full_table} DISABLE ROW LEVEL SECURITY;")

    # Drop all audit tables
    tables_to_drop = [
        t for t in reversed(Base.metadata.sorted_tables)
        if t.schema == "audit"
    ]

    for table in tables_to_drop:
        op.execute(f"DROP TABLE IF EXISTS audit.{table.name} CASCADE")

    # Drop the schema
    op.execute("DROP SCHEMA IF EXISTS audit CASCADE")
