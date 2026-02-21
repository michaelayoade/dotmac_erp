"""Add append-only trigger on audit.audit_log.

Prevents UPDATE and DELETE on existing audit records at the database level.
This is the tamper-proof enforcement layer — even direct SQL or DBA access
cannot modify audit records without explicitly disabling the trigger (which
requires superuser and is logged by PostgreSQL).

Revision ID: 20260221_add_audit_log_append_only_trigger
Revises: 20260221_add_expense_approve_corrections
Create Date: 2026-02-21
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260221_add_audit_log_append_only_trigger"
down_revision = "20260221_add_expense_approve_corrections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the trigger function (idempotent via OR REPLACE)
    op.execute("""
        CREATE OR REPLACE FUNCTION audit.prevent_audit_log_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'audit.audit_log is append-only: % operations are forbidden',
                TG_OP
            USING ERRCODE = 'restrict_violation';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql IMMUTABLE;
    """)

    # Drop trigger if it already exists (idempotent)
    op.execute("""
        DROP TRIGGER IF EXISTS trg_audit_log_append_only
        ON audit.audit_log;
    """)

    # Create the trigger — fires BEFORE UPDATE or DELETE
    op.execute("""
        CREATE TRIGGER trg_audit_log_append_only
        BEFORE UPDATE OR DELETE ON audit.audit_log
        FOR EACH ROW
        EXECUTE FUNCTION audit.prevent_audit_log_mutation();
    """)


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER IF EXISTS trg_audit_log_append_only
        ON audit.audit_log;
    """)
    op.execute("""
        DROP FUNCTION IF EXISTS audit.prevent_audit_log_mutation();
    """)
