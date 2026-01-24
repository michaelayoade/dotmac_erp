"""Add saga execution tables to platform schema.

Revision ID: add_saga_execution_tables
Revises: 5c7e3587f12a
Create Date: 2026-01-19

This migration adds saga orchestration tables for distributed transactions:
- saga_execution: Tracks saga state and progress
- saga_step: Individual step execution records
"""
from alembic import op
from app.alembic_utils import ensure_enum


revision = "add_saga_execution_tables"
down_revision = "5c7e3587f12a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    ensure_enum(
        bind,
        "saga_status",
        "PENDING",
        "EXECUTING",
        "COMPLETED",
        "COMPENSATING",
        "COMPENSATED",
        "FAILED",
        schema="platform",
    )
    ensure_enum(
        bind,
        "saga_step_status",
        "PENDING",
        "EXECUTING",
        "COMPLETED",
        "FAILED",
        "COMPENSATING",
        "COMPENSATED",
        schema="platform",
    )

    statements = [
        """CREATE SCHEMA IF NOT EXISTS platform;""",
        """CREATE TABLE IF NOT EXISTS platform.saga_execution (
	saga_id UUID DEFAULT gen_random_uuid() NOT NULL, 
	organization_id UUID NOT NULL, 
	saga_type VARCHAR(50) NOT NULL, 
	idempotency_key VARCHAR(200) NOT NULL, 
	correlation_id VARCHAR(100), 
	status platform.saga_status NOT NULL, 
	current_step INTEGER NOT NULL, 
	payload JSONB NOT NULL, 
	context JSONB DEFAULT '{}'::jsonb NOT NULL, 
	result JSONB, 
	error_message TEXT, 
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	created_by_user_id UUID NOT NULL, 
	PRIMARY KEY (saga_id), 
	CONSTRAINT uq_saga_idempotency_key UNIQUE (idempotency_key)
);""",
        """COMMENT ON COLUMN platform.saga_execution.saga_type IS 'Type identifier: AP_INVOICE_POST, AR_INVOICE_POST, etc.';""",
        """COMMENT ON COLUMN platform.saga_execution.idempotency_key IS 'Unique key for saga deduplication';""",
        """COMMENT ON COLUMN platform.saga_execution.correlation_id IS 'Correlation ID for distributed tracing';""",
        """COMMENT ON COLUMN platform.saga_execution.current_step IS 'Index of current/last executed step';""",
        """COMMENT ON COLUMN platform.saga_execution.payload IS 'Input parameters for the saga';""",
        """COMMENT ON COLUMN platform.saga_execution.context IS 'Accumulated context from step outputs';""",
        """COMMENT ON COLUMN platform.saga_execution.result IS 'Final result on completion';""",
        """COMMENT ON COLUMN platform.saga_execution.error_message IS 'Error message if failed';""",
        """CREATE INDEX IF NOT EXISTS idx_saga_execution_correlation ON platform.saga_execution (correlation_id);""",
        """CREATE INDEX IF NOT EXISTS idx_saga_execution_org_status ON platform.saga_execution (organization_id, status);""",
        """CREATE INDEX IF NOT EXISTS idx_saga_execution_type_status ON platform.saga_execution (saga_type, status);""",
        """CREATE TABLE IF NOT EXISTS platform.saga_step (
	step_id UUID DEFAULT gen_random_uuid() NOT NULL, 
	saga_id UUID NOT NULL, 
	step_number INTEGER NOT NULL, 
	step_name VARCHAR(50) NOT NULL, 
	status platform.saga_step_status NOT NULL, 
	input_data JSONB, 
	output_data JSONB, 
	compensation_data JSONB, 
	started_at TIMESTAMP WITH TIME ZONE, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	error_message TEXT, 
	retry_count INTEGER NOT NULL, 
	PRIMARY KEY (step_id), 
	FOREIGN KEY(saga_id) REFERENCES platform.saga_execution (saga_id) ON DELETE CASCADE
);""",
        """COMMENT ON COLUMN platform.saga_step.step_number IS 'Order of step in saga (0-indexed)';""",
        """COMMENT ON COLUMN platform.saga_step.step_name IS 'Human-readable step name';""",
        """COMMENT ON COLUMN platform.saga_step.input_data IS 'Input parameters for this step';""",
        """COMMENT ON COLUMN platform.saga_step.output_data IS 'Output data from successful execution';""",
        """COMMENT ON COLUMN platform.saga_step.compensation_data IS 'Data needed for compensation (rollback)';""",
        """CREATE INDEX IF NOT EXISTS idx_saga_step_saga_number ON platform.saga_step (saga_id, step_number);""",
    ]
    for statement in statements:
        op.execute(statement)

    # Add RLS policies
    for table in ["saga_execution", "saga_step"]:
        full_table = f"platform.{table}"
        policy_name = f"{table}_tenant_isolation"

        op.execute(f"ALTER TABLE {full_table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {full_table} FORCE ROW LEVEL SECURITY;")

        op.execute(f"DROP POLICY IF EXISTS {policy_name}_select ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_insert ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_update ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_delete ON {full_table};")

        if table == "saga_execution":
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
        else:
            op.execute(f"""
        CREATE POLICY {policy_name}_select ON {full_table}
        FOR SELECT
        USING (
            should_bypass_rls()
            OR EXISTS (
                SELECT 1 FROM platform.saga_execution se
                WHERE se.saga_id = saga_id
                AND se.organization_id = get_current_organization_id()
            )
        );
    """)

            op.execute(f"""
        CREATE POLICY {policy_name}_insert ON {full_table}
        FOR INSERT
        WITH CHECK (
            should_bypass_rls()
            OR EXISTS (
                SELECT 1 FROM platform.saga_execution se
                WHERE se.saga_id = saga_id
                AND se.organization_id = get_current_organization_id()
            )
        );
    """)

            op.execute(f"""
        CREATE POLICY {policy_name}_update ON {full_table}
        FOR UPDATE
        USING (
            should_bypass_rls()
            OR EXISTS (
                SELECT 1 FROM platform.saga_execution se
                WHERE se.saga_id = saga_id
                AND se.organization_id = get_current_organization_id()
            )
        )
        WITH CHECK (
            should_bypass_rls()
            OR EXISTS (
                SELECT 1 FROM platform.saga_execution se
                WHERE se.saga_id = saga_id
                AND se.organization_id = get_current_organization_id()
            )
        );
    """)

            op.execute(f"""
        CREATE POLICY {policy_name}_delete ON {full_table}
        FOR DELETE
        USING (
            should_bypass_rls()
            OR EXISTS (
                SELECT 1 FROM platform.saga_execution se
                WHERE se.saga_id = saga_id
                AND se.organization_id = get_current_organization_id()
            )
        );
    """)


def downgrade() -> None:
    # Drop RLS policies
    for table in ["saga_step", "saga_execution"]:
        full_table = f"platform.{table}"
        policy_name = f"{table}_tenant_isolation"

        op.execute(f"DROP POLICY IF EXISTS {policy_name}_select ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_insert ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_update ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_delete ON {full_table};")
        op.execute(f"ALTER TABLE {full_table} DISABLE ROW LEVEL SECURITY;")


    statements = [
        """DROP TABLE IF EXISTS platform.saga_step CASCADE;""",
        """DROP TABLE IF EXISTS platform.saga_execution CASCADE;""",
        """DROP TYPE IF EXISTS platform.saga_step_status CASCADE;""",
        """DROP TYPE IF EXISTS platform.saga_status CASCADE;""",
        """DROP SCHEMA IF EXISTS platform CASCADE;""",
    ]
    for statement in statements:
        op.execute(statement)
