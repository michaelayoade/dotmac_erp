"""Add employee extended tables.

Revision ID: 20260125_add_employee_extended_tables
Revises: 20260125_extend_project_type
Create Date: 2026-01-25
"""
from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision = "20260125_add_employee_extended_tables"
down_revision = "20260125_extend_project_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    ensure_enum(
        bind,
        "document_type",
        "CONTRACT",
        "OFFER_LETTER",
        "ID_PROOF",
        "PASSPORT",
        "VISA",
        "WORK_PERMIT",
        "EDUCATIONAL",
        "PROFESSIONAL",
        "MEDICAL",
        "BACKGROUND_CHECK",
        "TAX_FORM",
        "BANK_DETAILS",
        "OTHER",
        schema="hr",
    )
    ensure_enum(
        bind,
        "qualification_type",
        "HIGH_SCHOOL",
        "DIPLOMA",
        "ASSOCIATE",
        "BACHELORS",
        "MASTERS",
        "DOCTORATE",
        "PROFESSIONAL",
        "CERTIFICATION",
        "OTHER",
        schema="hr",
    )
    ensure_enum(
        bind,
        "relationship_type",
        "SPOUSE",
        "CHILD",
        "PARENT",
        "SIBLING",
        "DOMESTIC_PARTNER",
        "GUARDIAN",
        "OTHER",
        schema="hr",
    )
    ensure_enum(
        bind,
        "dependent_gender",
        "MALE",
        "FEMALE",
        "OTHER",
        "PREFER_NOT_TO_SAY",
        schema="hr",
    )
    ensure_enum(
        bind,
        "skill_category",
        "TECHNICAL",
        "SOFT_SKILL",
        "LANGUAGE",
        "MANAGEMENT",
        "DOMAIN",
        "TOOL",
        "OTHER",
        schema="hr",
    )

    op.execute(
        """
        CREATE TABLE hr.employee_document (
            document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            employee_id UUID NOT NULL REFERENCES hr.employee(employee_id),
            document_type hr.document_type NOT NULL,
            document_name VARCHAR(255) NOT NULL,
            description TEXT,
            file_path VARCHAR(500) NOT NULL,
            file_name VARCHAR(255) NOT NULL,
            file_size INTEGER,
            mime_type VARCHAR(100),
            issue_date DATE,
            expiry_date DATE,
            uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_verified BOOLEAN DEFAULT FALSE,
            verified_by_id UUID REFERENCES hr.employee(employee_id),
            verified_at TIMESTAMPTZ,
            verification_notes TEXT,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            deleted_by_id UUID REFERENCES public.people(id)
        );
        CREATE INDEX idx_emp_doc_org ON hr.employee_document(organization_id);
        CREATE INDEX idx_emp_doc_employee ON hr.employee_document(employee_id);
        CREATE INDEX idx_emp_doc_type ON hr.employee_document(organization_id, document_type);
        CREATE INDEX idx_emp_doc_expiry ON hr.employee_document(organization_id, expiry_date);
        """
    )

    op.execute(
        """
        CREATE TABLE hr.employee_qualification (
            qualification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            employee_id UUID NOT NULL REFERENCES hr.employee(employee_id),
            qualification_type hr.qualification_type NOT NULL,
            qualification_name VARCHAR(200) NOT NULL,
            field_of_study VARCHAR(200),
            institution_name VARCHAR(255) NOT NULL,
            institution_location VARCHAR(200),
            start_date DATE,
            end_date DATE,
            is_ongoing BOOLEAN DEFAULT FALSE,
            grade VARCHAR(50),
            score NUMERIC(5,2),
            max_score NUMERIC(5,2),
            is_verified BOOLEAN DEFAULT FALSE,
            document_id UUID REFERENCES hr.employee_document(document_id),
            notes TEXT,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            deleted_by_id UUID REFERENCES public.people(id)
        );
        CREATE INDEX idx_emp_qual_org ON hr.employee_qualification(organization_id);
        CREATE INDEX idx_emp_qual_employee ON hr.employee_qualification(employee_id);
        CREATE INDEX idx_emp_qual_type ON hr.employee_qualification(organization_id, qualification_type);
        """
    )

    op.execute(
        """
        CREATE TABLE hr.employee_certification (
            certification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            employee_id UUID NOT NULL REFERENCES hr.employee(employee_id),
            certification_name VARCHAR(255) NOT NULL,
            issuing_authority VARCHAR(255) NOT NULL,
            credential_id VARCHAR(100),
            credential_url VARCHAR(500),
            issue_date DATE NOT NULL,
            expiry_date DATE,
            does_not_expire BOOLEAN DEFAULT FALSE,
            renewal_reminder_days INTEGER DEFAULT 30,
            last_reminder_sent TIMESTAMPTZ,
            is_verified BOOLEAN DEFAULT FALSE,
            document_id UUID REFERENCES hr.employee_document(document_id),
            notes TEXT,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            deleted_by_id UUID REFERENCES public.people(id)
        );
        CREATE INDEX idx_emp_cert_org ON hr.employee_certification(organization_id);
        CREATE INDEX idx_emp_cert_employee ON hr.employee_certification(employee_id);
        CREATE INDEX idx_emp_cert_expiry ON hr.employee_certification(organization_id, expiry_date);
        """
    )

    op.execute(
        """
        CREATE TABLE hr.employee_dependent (
            dependent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            employee_id UUID NOT NULL REFERENCES hr.employee(employee_id),
            full_name VARCHAR(200) NOT NULL,
            relationship hr.relationship_type NOT NULL,
            date_of_birth DATE,
            gender hr.dependent_gender,
            phone VARCHAR(20),
            email VARCHAR(255),
            address TEXT,
            is_emergency_contact BOOLEAN DEFAULT FALSE,
            emergency_contact_priority INTEGER,
            is_beneficiary BOOLEAN DEFAULT FALSE,
            beneficiary_percentage NUMERIC(5,2),
            is_covered_under_insurance BOOLEAN DEFAULT FALSE,
            insurance_id VARCHAR(100),
            notes TEXT,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            deleted_by_id UUID REFERENCES public.people(id)
        );
        CREATE INDEX idx_emp_dep_org ON hr.employee_dependent(organization_id);
        CREATE INDEX idx_emp_dep_employee ON hr.employee_dependent(employee_id);
        CREATE INDEX idx_emp_dep_emergency ON hr.employee_dependent(employee_id, is_emergency_contact);
        """
    )

    op.execute(
        """
        CREATE TABLE hr.skill (
            skill_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            skill_name VARCHAR(100) NOT NULL,
            category hr.skill_category NOT NULL,
            description TEXT,
            is_language BOOLEAN DEFAULT FALSE,
            is_active BOOLEAN DEFAULT TRUE,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            deleted_by_id UUID REFERENCES public.people(id)
        );
        CREATE INDEX idx_skill_org ON hr.skill(organization_id);
        CREATE INDEX idx_skill_org_category ON hr.skill(organization_id, category);
        CREATE INDEX idx_skill_name ON hr.skill(organization_id, skill_name);
        """
    )

    op.execute(
        """
        CREATE TABLE hr.employee_skill (
            employee_skill_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            employee_id UUID NOT NULL REFERENCES hr.employee(employee_id),
            skill_id UUID NOT NULL REFERENCES hr.skill(skill_id),
            proficiency_level INTEGER NOT NULL,
            years_experience NUMERIC(4,1),
            last_used_date DATE,
            is_self_assessed BOOLEAN DEFAULT TRUE,
            assessed_by_id UUID REFERENCES hr.employee(employee_id),
            assessed_at TIMESTAMPTZ,
            is_primary BOOLEAN DEFAULT FALSE,
            is_certified BOOLEAN DEFAULT FALSE,
            notes TEXT,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id)
        );
        CREATE INDEX idx_emp_skill_org ON hr.employee_skill(organization_id);
        CREATE INDEX idx_emp_skill_employee ON hr.employee_skill(employee_id);
        CREATE INDEX idx_emp_skill_skill ON hr.employee_skill(skill_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS hr.employee_skill CASCADE")
    op.execute("DROP TABLE IF EXISTS hr.skill CASCADE")
    op.execute("DROP TABLE IF EXISTS hr.employee_dependent CASCADE")
    op.execute("DROP TABLE IF EXISTS hr.employee_certification CASCADE")
    op.execute("DROP TABLE IF EXISTS hr.employee_qualification CASCADE")
    op.execute("DROP TABLE IF EXISTS hr.employee_document CASCADE")

    op.execute("DROP TYPE IF EXISTS hr.skill_category CASCADE")
    op.execute("DROP TYPE IF EXISTS hr.dependent_gender CASCADE")
    op.execute("DROP TYPE IF EXISTS hr.relationship_type CASCADE")
    op.execute("DROP TYPE IF EXISTS hr.qualification_type CASCADE")
    op.execute("DROP TYPE IF EXISTS hr.document_type CASCADE")
