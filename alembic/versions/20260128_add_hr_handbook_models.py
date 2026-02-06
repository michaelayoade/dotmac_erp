"""Add HR handbook and document acknowledgment models.

Revision ID: 20260128_hr_handbook
Revises: 20260128_payroll_gl
Create Date: 2026-01-28

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260128_hr_handbook"
down_revision = "20260128_payroll_gl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enums first using raw SQL to avoid conflicts with create_table
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE hr.hr_document_category AS ENUM (
                'HANDBOOK', 'POLICY', 'CODE_OF_CONDUCT', 'SAFETY', 'BENEFITS',
                'IT_SECURITY', 'COMPLIANCE', 'TRAINING', 'OTHER'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE hr.hr_document_status AS ENUM (
                'DRAFT', 'ACTIVE', 'SUPERSEDED', 'ARCHIVED'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # Create hr_document table - use postgresql.ENUM with create_type=False since we created them above
    hr_document_category = postgresql.ENUM(
        "HANDBOOK",
        "POLICY",
        "CODE_OF_CONDUCT",
        "SAFETY",
        "BENEFITS",
        "IT_SECURITY",
        "COMPLIANCE",
        "TRAINING",
        "OTHER",
        name="hr_document_category",
        schema="hr",
        create_type=False,
    )
    hr_document_status = postgresql.ENUM(
        "DRAFT",
        "ACTIVE",
        "SUPERSEDED",
        "ARCHIVED",
        name="hr_document_status",
        schema="hr",
        create_type=False,
    )

    op.create_table(
        "hr_document",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "document_code",
            sa.String(50),
            nullable=False,
            comment="Unique code for this document, e.g., HB-001, POL-IT-001",
        ),
        sa.Column("title", sa.String(200), nullable=False, comment="Document title"),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
            comment="Brief description of the document",
        ),
        sa.Column("category", hr_document_category, nullable=False),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            default=1,
            comment="Document version number",
        ),
        sa.Column(
            "previous_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Reference to previous version",
        ),
        sa.Column(
            "file_path",
            sa.String(500),
            nullable=False,
            comment="Storage path for the document file",
        ),
        sa.Column(
            "file_name", sa.String(255), nullable=False, comment="Original filename"
        ),
        sa.Column(
            "content_type",
            sa.String(100),
            nullable=False,
            default="application/pdf",
            comment="MIME type",
        ),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False, default=0),
        sa.Column(
            "content_hash",
            sa.String(64),
            nullable=True,
            comment="SHA256 hash for integrity verification",
        ),
        sa.Column(
            "effective_date",
            sa.Date(),
            nullable=False,
            comment="Date this version becomes effective",
        ),
        sa.Column(
            "expiry_date", sa.Date(), nullable=True, comment="Optional expiry date"
        ),
        sa.Column(
            "requires_acknowledgment",
            sa.Boolean(),
            nullable=False,
            default=True,
            comment="Whether employees must acknowledge this document",
        ),
        sa.Column(
            "acknowledgment_deadline_days",
            sa.Integer(),
            nullable=True,
            comment="Days from onboarding/effective date to acknowledge",
        ),
        sa.Column(
            "applies_to_all_employees",
            sa.Boolean(),
            nullable=False,
            default=True,
            comment="If false, specific departments/roles may be defined",
        ),
        sa.Column(
            "applies_to_departments",
            postgresql.JSONB(),
            nullable=True,
            comment="List of department IDs this applies to (if not all)",
        ),
        sa.Column("status", hr_document_status, nullable=False),
        sa.Column(
            "tags",
            postgresql.JSONB(),
            nullable=True,
            comment="Tags for searching/filtering",
        ),
        sa.Column(
            "extra_data",
            postgresql.JSONB(),
            nullable=True,
            comment="Additional metadata",
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["previous_version_id"],
            ["hr.hr_document.document_id"],
        ),
        sa.PrimaryKeyConstraint("document_id"),
        sa.UniqueConstraint(
            "organization_id",
            "document_code",
            "version",
            name="uq_hr_document_code_version",
        ),
        schema="hr",
    )
    op.create_index(
        "idx_hr_document_org", "hr_document", ["organization_id"], schema="hr"
    )
    op.create_index(
        "idx_hr_document_category", "hr_document", ["category"], schema="hr"
    )
    op.create_index("idx_hr_document_status", "hr_document", ["status"], schema="hr")

    # Create hr_document_acknowledgment table
    op.create_table(
        "hr_document_acknowledgment",
        sa.Column(
            "acknowledgment_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "ip_address",
            sa.String(45),
            nullable=True,
            comment="IP address at time of acknowledgment",
        ),
        sa.Column(
            "user_agent",
            sa.String(500),
            nullable=True,
            comment="Browser user agent for audit",
        ),
        sa.Column(
            "signature_data",
            sa.Text(),
            nullable=True,
            comment="Base64 encoded signature image if captured",
        ),
        sa.Column(
            "confirmation_text",
            sa.Text(),
            nullable=True,
            comment="Text the employee confirmed",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["hr.hr_document.document_id"],
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["hr.employee.employee_id"],
        ),
        sa.PrimaryKeyConstraint("acknowledgment_id"),
        sa.UniqueConstraint(
            "document_id", "employee_id", name="uq_hr_doc_ack_document_employee"
        ),
        schema="hr",
    )
    op.create_index(
        "idx_hr_doc_ack_document",
        "hr_document_acknowledgment",
        ["document_id"],
        schema="hr",
    )
    op.create_index(
        "idx_hr_doc_ack_employee",
        "hr_document_acknowledgment",
        ["employee_id"],
        schema="hr",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_hr_doc_ack_employee", table_name="hr_document_acknowledgment", schema="hr"
    )
    op.drop_index(
        "idx_hr_doc_ack_document", table_name="hr_document_acknowledgment", schema="hr"
    )
    op.drop_table("hr_document_acknowledgment", schema="hr")

    op.drop_index("idx_hr_document_status", table_name="hr_document", schema="hr")
    op.drop_index("idx_hr_document_category", table_name="hr_document", schema="hr")
    op.drop_index("idx_hr_document_org", table_name="hr_document", schema="hr")
    op.drop_table("hr_document", schema="hr")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS hr.hr_document_status")
    op.execute("DROP TYPE IF EXISTS hr.hr_document_category")
