"""Create HR Core tables.

Revision ID: create_hr_core_tables
Revises: create_migration_mapping_tables
Create Date: 2025-01-20

This migration creates the HR Core tables:
- hr.department
- hr.designation
- hr.employment_type
- hr.employee_grade
- hr.employee
"""
from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision = "create_hr_core_tables"
down_revision = "create_migration_mapping_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enums first
    bind = op.get_bind()
    ensure_enum(
        bind,
        "employee_status",
        "DRAFT",
        "ACTIVE",
        "ON_LEAVE",
        "SUSPENDED",
        "RESIGNED",
        "TERMINATED",
        "RETIRED",
        schema="hr",
    )
    ensure_enum(
        bind,
        "hr_gender",
        "MALE",
        "FEMALE",
        "OTHER",
        "PREFER_NOT_TO_SAY",
        schema="hr",
    )

    # Create department table
    op.execute("""
        CREATE TABLE hr.department (
            department_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            department_code VARCHAR(20) NOT NULL,
            department_name VARCHAR(100) NOT NULL,
            description TEXT,
            parent_department_id UUID REFERENCES hr.department(department_id),
            cost_center_id UUID REFERENCES core_org.cost_center(cost_center_id),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            deleted_by_id UUID REFERENCES public.people(id),
            erpnext_id VARCHAR(255),
            last_synced_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );
        CREATE INDEX idx_department_org ON hr.department(organization_id);
        CREATE INDEX idx_department_code ON hr.department(department_code);
        CREATE INDEX idx_department_deleted ON hr.department(is_deleted);
        CREATE INDEX idx_department_erpnext ON hr.department(erpnext_id);
    """)

    # Create designation table
    op.execute("""
        CREATE TABLE hr.designation (
            designation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            designation_code VARCHAR(20) NOT NULL,
            designation_name VARCHAR(100) NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            deleted_by_id UUID REFERENCES public.people(id),
            erpnext_id VARCHAR(255),
            last_synced_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );
        CREATE INDEX idx_designation_org ON hr.designation(organization_id);
        CREATE INDEX idx_designation_code ON hr.designation(designation_code);
        CREATE INDEX idx_designation_deleted ON hr.designation(is_deleted);
        CREATE INDEX idx_designation_erpnext ON hr.designation(erpnext_id);
    """)

    # Create employment_type table
    op.execute("""
        CREATE TABLE hr.employment_type (
            employment_type_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            type_code VARCHAR(20) NOT NULL,
            type_name VARCHAR(100) NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            erpnext_id VARCHAR(255),
            last_synced_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );
        CREATE INDEX idx_employment_type_org ON hr.employment_type(organization_id);
        CREATE INDEX idx_employment_type_code ON hr.employment_type(type_code);
        CREATE INDEX idx_employment_type_erpnext ON hr.employment_type(erpnext_id);
    """)

    # Create employee_grade table
    op.execute("""
        CREATE TABLE hr.employee_grade (
            grade_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            grade_code VARCHAR(20) NOT NULL,
            grade_name VARCHAR(100) NOT NULL,
            description TEXT,
            rank INTEGER NOT NULL DEFAULT 0,
            min_salary NUMERIC(15,2),
            max_salary NUMERIC(15,2),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            erpnext_id VARCHAR(255),
            last_synced_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );
        CREATE INDEX idx_grade_org ON hr.employee_grade(organization_id);
        CREATE INDEX idx_grade_code ON hr.employee_grade(grade_code);
        CREATE INDEX idx_grade_erpnext ON hr.employee_grade(erpnext_id);
    """)

    # Create employee table
    op.execute("""
        CREATE TABLE hr.employee (
            employee_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            person_id UUID NOT NULL REFERENCES public.people(id) ON DELETE RESTRICT,
            employee_code VARCHAR(30) NOT NULL,
            gender hr.hr_gender,
            date_of_birth DATE,
            personal_email VARCHAR(255),
            personal_phone VARCHAR(50),
            emergency_contact_name VARCHAR(100),
            emergency_contact_phone VARCHAR(50),
            department_id UUID REFERENCES hr.department(department_id),
            designation_id UUID REFERENCES hr.designation(designation_id),
            employment_type_id UUID REFERENCES hr.employment_type(employment_type_id),
            grade_id UUID REFERENCES hr.employee_grade(grade_id),
            reports_to_id UUID REFERENCES hr.employee(employee_id),
            date_of_joining DATE NOT NULL,
            date_of_leaving DATE,
            probation_end_date DATE,
            confirmation_date DATE,
            status hr.employee_status NOT NULL DEFAULT 'DRAFT',
            cost_center_id UUID REFERENCES core_org.cost_center(cost_center_id),
            default_payroll_payable_account_id UUID REFERENCES gl.account(account_id),
            bank_name VARCHAR(100),
            bank_account_number VARCHAR(30),
            bank_account_name VARCHAR(100),
            bank_branch_code VARCHAR(20),
            notes TEXT,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            deleted_by_id UUID REFERENCES public.people(id),
            erpnext_id VARCHAR(255),
            last_synced_at TIMESTAMPTZ,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_employee_org_code UNIQUE (organization_id, employee_code),
            CONSTRAINT uq_employee_person UNIQUE (person_id)
        );
        CREATE INDEX idx_employee_org ON hr.employee(organization_id);
        CREATE INDEX idx_employee_person ON hr.employee(person_id);
        CREATE INDEX idx_employee_org_dept ON hr.employee(organization_id, department_id);
        CREATE INDEX idx_employee_org_status ON hr.employee(organization_id, status);
        CREATE INDEX idx_employee_deleted ON hr.employee(is_deleted);
        CREATE INDEX idx_employee_erpnext ON hr.employee(erpnext_id);
    """)


def downgrade() -> None:
    # Drop tables in reverse order
    op.execute("DROP TABLE IF EXISTS hr.employee CASCADE")
    op.execute("DROP TABLE IF EXISTS hr.employee_grade CASCADE")
    op.execute("DROP TABLE IF EXISTS hr.employment_type CASCADE")
    op.execute("DROP TABLE IF EXISTS hr.designation CASCADE")
    op.execute("DROP TABLE IF EXISTS hr.department CASCADE")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS hr.employee_status CASCADE")
    op.execute("DROP TYPE IF EXISTS hr.hr_gender CASCADE")
