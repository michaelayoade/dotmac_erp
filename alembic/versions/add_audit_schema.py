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

from sqlalchemy import text

from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision = "add_audit_schema"
down_revision = "add_auth_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    ensure_enum(
        bind,
        "approval_decision_action",
        "APPROVE",
        "REJECT",
        "DELEGATE",
        "ESCALATE",
        "REQUEST_INFO",
    )
    ensure_enum(
        bind,
        "approval_request_status",
        "PENDING",
        "APPROVED",
        "REJECTED",
        "CANCELLED",
        "ESCALATED",
    )
    ensure_enum(bind, "audit_action", "INSERT", "UPDATE", "DELETE")

    statements = [
        """CREATE SCHEMA IF NOT EXISTS audit;""",
        """CREATE TABLE IF NOT EXISTS audit.approval_workflow (
	workflow_id UUID DEFAULT gen_random_uuid() NOT NULL,
	organization_id UUID NOT NULL,
	workflow_code VARCHAR(50) NOT NULL,
	workflow_name VARCHAR(100) NOT NULL,
	description TEXT,
	document_type VARCHAR(50) NOT NULL,
	threshold_amount NUMERIC(20, 6),
	threshold_currency_code VARCHAR(3),
	approval_levels JSONB NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (workflow_id),
	CONSTRAINT uq_workflow_code UNIQUE (organization_id, workflow_code),
	FOREIGN KEY(organization_id) REFERENCES core_org.organization (organization_id)
);""",
        """COMMENT ON COLUMN audit.approval_workflow.document_type IS 'INVOICE, JOURNAL, PAYMENT, PO, ADJUSTMENT, PERIOD_REOPEN, AUDIT_LOCK';""",
        """CREATE TABLE IF NOT EXISTS audit.audit_log (
	audit_id UUID DEFAULT gen_random_uuid() NOT NULL,
	organization_id UUID NOT NULL,
	table_schema VARCHAR(50) NOT NULL,
	table_name VARCHAR(100) NOT NULL,
	record_id VARCHAR(100) NOT NULL,
	action audit_action NOT NULL,
	old_values JSONB,
	new_values JSONB,
	changed_fields TEXT[],
	user_id UUID,
	ip_address VARCHAR(45),
	user_agent TEXT,
	session_id UUID,
	correlation_id VARCHAR(100),
	reason TEXT,
	occurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	hash_chain VARCHAR(64),
	PRIMARY KEY (audit_id)
);""",
        """COMMENT ON COLUMN audit.audit_log.hash_chain IS 'SHA256(prev_hash + record_payload)';""",
        """CREATE INDEX IF NOT EXISTS idx_audit_correlation ON audit.audit_log (correlation_id);""",
        """CREATE INDEX IF NOT EXISTS idx_audit_org_table ON audit.audit_log (organization_id, table_schema, table_name);""",
        """CREATE INDEX IF NOT EXISTS idx_audit_record ON audit.audit_log (table_schema, table_name, record_id);""",
        """CREATE INDEX IF NOT EXISTS idx_audit_user ON audit.audit_log (user_id);""",
        """CREATE TABLE IF NOT EXISTS audit.approval_request (
	request_id UUID DEFAULT gen_random_uuid() NOT NULL,
	organization_id UUID NOT NULL,
	workflow_id UUID NOT NULL,
	document_type VARCHAR(50) NOT NULL,
	document_id UUID NOT NULL,
	document_reference VARCHAR(100),
	document_amount NUMERIC(20, 6),
	document_currency_code VARCHAR(3),
	requested_by_user_id UUID NOT NULL,
	requested_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	current_level INTEGER NOT NULL,
	status approval_request_status NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	final_approver_user_id UUID,
	notes TEXT,
	correlation_id VARCHAR(100),
	PRIMARY KEY (request_id),
	FOREIGN KEY(workflow_id) REFERENCES audit.approval_workflow (workflow_id)
);""",
        """CREATE INDEX IF NOT EXISTS idx_approval_document ON audit.approval_request (document_type, document_id);""",
        """CREATE INDEX IF NOT EXISTS idx_approval_status ON audit.approval_request (organization_id, status);""",
        """CREATE TABLE IF NOT EXISTS audit.approval_decision (
	decision_id UUID DEFAULT gen_random_uuid() NOT NULL,
	request_id UUID NOT NULL,
	level INTEGER NOT NULL,
	approver_user_id UUID NOT NULL,
	delegated_from_user_id UUID,
	action approval_decision_action NOT NULL,
	comments TEXT,
	decided_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	ip_address VARCHAR(45),
	mfa_verified BOOLEAN NOT NULL,
	PRIMARY KEY (decision_id),
	FOREIGN KEY(request_id) REFERENCES audit.approval_request (request_id)
);""",
        """CREATE INDEX IF NOT EXISTS idx_decision_request ON audit.approval_decision (request_id);""",
    ]
    for statement in statements:
        op.execute(statement)

    # Add RLS policies to audit tables with organization_id
    result = bind.execute(
        text("""
        SELECT t.table_name
        FROM information_schema.tables t
        JOIN information_schema.columns c
            ON c.table_schema = t.table_schema AND c.table_name = t.table_name
        WHERE t.table_schema = 'audit'
        AND c.column_name = 'organization_id'
        AND t.table_type = 'BASE TABLE'
    """)
    )

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
    # Drop RLS policies from audit tables
    bind = op.get_bind()
    result = bind.execute(
        text("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'audit' AND table_type = 'BASE TABLE'
    """)
    )

    for row in result:
        table = row[0]
        full_table = f"audit.{table}"
        policy_name = f"{table}_tenant_isolation"

        op.execute(f"DROP POLICY IF EXISTS {policy_name}_select ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_insert ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_update ON {full_table};")
        op.execute(f"DROP POLICY IF EXISTS {policy_name}_delete ON {full_table};")
        op.execute(f"ALTER TABLE {full_table} DISABLE ROW LEVEL SECURITY;")

    statements = [
        """DROP TABLE IF EXISTS audit.approval_decision CASCADE;""",
        """DROP TABLE IF EXISTS audit.approval_request CASCADE;""",
        """DROP TABLE IF EXISTS audit.audit_log CASCADE;""",
        """DROP TABLE IF EXISTS audit.approval_workflow CASCADE;""",
        """DROP TYPE IF EXISTS audit_action CASCADE;""",
        """DROP TYPE IF EXISTS approval_request_status CASCADE;""",
        """DROP TYPE IF EXISTS approval_decision_action CASCADE;""",
        """DROP SCHEMA IF EXISTS audit CASCADE;""",
    ]
    for statement in statements:
        op.execute(statement)
