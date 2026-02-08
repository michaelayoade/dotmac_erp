"""Add discipline module tables.

Revision ID: 20260128_discipline
Revises: 20260128_payroll_gl
Create Date: 2026-01-28

Creates tables for the discipline module:
- hr.disciplinary_case
- hr.case_witness
- hr.case_action
- hr.case_document
- hr.case_response
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260128_discipline"
down_revision = "20260128_payroll_gl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Create enum types using raw SQL with exception handling
    conn.execute(
        sa.text("""
        DO $$ BEGIN
            CREATE TYPE hr.violation_type AS ENUM (
                'MISCONDUCT', 'GROSS_MISCONDUCT', 'ATTENDANCE', 'PERFORMANCE',
                'INSUBORDINATION', 'HARASSMENT', 'THEFT', 'SAFETY_VIOLATION',
                'POLICY_BREACH', 'CONFLICT_OF_INTEREST', 'OTHER'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)
    )

    conn.execute(
        sa.text("""
        DO $$ BEGIN
            CREATE TYPE hr.severity_level AS ENUM ('MINOR', 'MODERATE', 'MAJOR', 'CRITICAL');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)
    )

    conn.execute(
        sa.text("""
        DO $$ BEGIN
            CREATE TYPE hr.case_status AS ENUM (
                'DRAFT', 'QUERY_ISSUED', 'RESPONSE_RECEIVED', 'UNDER_INVESTIGATION',
                'HEARING_SCHEDULED', 'HEARING_COMPLETED', 'DECISION_MADE',
                'APPEAL_FILED', 'APPEAL_DECIDED', 'CLOSED', 'WITHDRAWN'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)
    )

    conn.execute(
        sa.text("""
        DO $$ BEGIN
            CREATE TYPE hr.action_type AS ENUM (
                'VERBAL_WARNING', 'WRITTEN_WARNING', 'FINAL_WARNING',
                'SUSPENSION_PAID', 'SUSPENSION_UNPAID', 'DEMOTION', 'SALARY_REDUCTION',
                'TRANSFER', 'MANDATORY_TRAINING', 'PROBATION', 'TERMINATION',
                'NO_ACTION', 'EXONERATED'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)
    )

    conn.execute(
        sa.text("""
        DO $$ BEGIN
            CREATE TYPE hr.document_type AS ENUM (
                'EVIDENCE', 'QUERY_LETTER', 'EMPLOYEE_RESPONSE', 'WITNESS_STATEMENT',
                'HEARING_MINUTES', 'DECISION_LETTER', 'APPEAL_LETTER', 'APPEAL_DECISION',
                'WARNING_LETTER', 'TERMINATION_LETTER', 'OTHER'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)
    )

    # Create tables using raw SQL to avoid SQLAlchemy enum issues
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS hr.disciplinary_case (
            case_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            case_number VARCHAR(50) NOT NULL UNIQUE,
            employee_id UUID NOT NULL REFERENCES hr.employee(employee_id),
            violation_type hr.violation_type NOT NULL,
            severity hr.severity_level NOT NULL,
            subject VARCHAR(255) NOT NULL,
            description TEXT,
            incident_date DATE,
            reported_date DATE NOT NULL,
            query_issued_date DATE,
            response_due_date DATE,
            hearing_date TIMESTAMPTZ,
            decision_date DATE,
            appeal_deadline DATE,
            closed_date DATE,
            status hr.case_status NOT NULL DEFAULT 'DRAFT',
            query_text TEXT,
            hearing_location VARCHAR(255),
            hearing_notes TEXT,
            decision_summary TEXT,
            appeal_reason TEXT,
            appeal_decision TEXT,
            reported_by_id UUID REFERENCES hr.employee(employee_id),
            investigating_officer_id UUID REFERENCES hr.employee(employee_id),
            panel_chair_id UUID REFERENCES hr.employee(employee_id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            created_by_id UUID REFERENCES people(id),
            updated_by_id UUID REFERENCES people(id),
            is_deleted BOOLEAN NOT NULL DEFAULT false,
            deleted_at TIMESTAMPTZ,
            deleted_by_id UUID REFERENCES people(id),
            status_changed_at TIMESTAMPTZ,
            status_changed_by_id UUID REFERENCES people(id)
        );
    """)
    )

    conn.execute(
        sa.text("""
        CREATE INDEX IF NOT EXISTS ix_discipline_case_org_status
        ON hr.disciplinary_case(organization_id, status);
    """)
    )

    conn.execute(
        sa.text("""
        CREATE INDEX IF NOT EXISTS ix_discipline_case_employee
        ON hr.disciplinary_case(employee_id);
    """)
    )

    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS hr.case_witness (
            witness_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id UUID NOT NULL REFERENCES hr.disciplinary_case(case_id) ON DELETE CASCADE,
            employee_id UUID REFERENCES hr.employee(employee_id),
            external_name VARCHAR(200),
            external_contact VARCHAR(255),
            statement TEXT,
            statement_date TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    )

    conn.execute(
        sa.text("""
        CREATE INDEX IF NOT EXISTS ix_case_witness_case_id ON hr.case_witness(case_id);
    """)
    )

    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS hr.case_action (
            action_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id UUID NOT NULL REFERENCES hr.disciplinary_case(case_id) ON DELETE CASCADE,
            action_type hr.action_type NOT NULL,
            description TEXT,
            effective_date DATE NOT NULL,
            end_date DATE,
            warning_expiry_date DATE,
            is_active BOOLEAN NOT NULL DEFAULT true,
            payroll_processed BOOLEAN NOT NULL DEFAULT false,
            lifecycle_triggered BOOLEAN NOT NULL DEFAULT false,
            issued_by_id UUID REFERENCES hr.employee(employee_id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    )

    conn.execute(
        sa.text("""
        CREATE INDEX IF NOT EXISTS ix_case_action_case_id ON hr.case_action(case_id);
    """)
    )

    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS hr.case_document (
            document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id UUID NOT NULL REFERENCES hr.disciplinary_case(case_id) ON DELETE CASCADE,
            document_type hr.document_type NOT NULL,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            file_path VARCHAR(500) NOT NULL,
            file_name VARCHAR(255) NOT NULL,
            file_size INTEGER,
            mime_type VARCHAR(100),
            uploaded_by_id UUID REFERENCES hr.employee(employee_id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    )

    conn.execute(
        sa.text("""
        CREATE INDEX IF NOT EXISTS ix_case_document_case_id ON hr.case_document(case_id);
    """)
    )

    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS hr.case_response (
            response_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id UUID NOT NULL REFERENCES hr.disciplinary_case(case_id) ON DELETE CASCADE,
            response_text TEXT NOT NULL,
            is_initial_response BOOLEAN NOT NULL DEFAULT true,
            is_appeal_response BOOLEAN NOT NULL DEFAULT false,
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            acknowledged_at TIMESTAMPTZ
        );
    """)
    )

    conn.execute(
        sa.text("""
        CREATE INDEX IF NOT EXISTS ix_case_response_case_id ON hr.case_response(case_id);
    """)
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Drop tables
    conn.execute(sa.text("DROP TABLE IF EXISTS hr.case_response CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS hr.case_document CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS hr.case_action CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS hr.case_witness CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS hr.disciplinary_case CASCADE"))

    # Drop enum types
    conn.execute(sa.text("DROP TYPE IF EXISTS hr.document_type CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS hr.action_type CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS hr.case_status CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS hr.severity_level CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS hr.violation_type CASCADE"))
