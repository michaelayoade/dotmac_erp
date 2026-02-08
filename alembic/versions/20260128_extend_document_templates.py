"""Extend document template system with HR types and generated documents.

Revision ID: 20260128_extend_doc_templates
Revises:
Create Date: 2026-01-28
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers
revision = "20260128_extend_doc_templates"
down_revision = "add_automation_schema"  # Fixed: connect to initial schema
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new HR document types to the document_template_type enum
    # (enum is created by add_automation_schema, which runs before this migration)
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'OFFER_LETTER'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'EMPLOYMENT_CONTRACT'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'APPOINTMENT_LETTER'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'CONFIRMATION_LETTER'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'PROMOTION_LETTER'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'TRANSFER_LETTER'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'TERMINATION_LETTER'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'RESIGNATION_ACCEPTANCE'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'EXPERIENCE_LETTER'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'RELIEVING_LETTER'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'WARNING_LETTER'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'SHOW_CAUSE_NOTICE'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'SALARY_REVISION_LETTER'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'BONUS_LETTER'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'EMAIL_OFFER'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'EMAIL_ONBOARDING'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'EMAIL_INTERVIEW_INVITE'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'EMAIL_APPLICATION_RECEIVED'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'EMAIL_APPLICATION_STATUS'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'EMAIL_REJECTION'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'PAYSLIP'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'TAX_CERTIFICATE'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'BANK_LETTER'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'PROJECT_PROPOSAL'"
    )
    op.execute(
        "ALTER TYPE public.document_template_type ADD VALUE IF NOT EXISTS 'PROJECT_REPORT'"
    )

    # Create generated_document table
    # Note: Enums are created automatically by SQLAlchemy when the table is created
    op.create_table(
        "generated_document",
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", UUID(as_uuid=True), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("document_number", sa.String(50), nullable=True),
        sa.Column("document_date", sa.Date(), nullable=False),
        sa.Column("document_title", sa.String(200), nullable=True),
        sa.Column(
            "output_format",
            sa.Enum(
                "PDF",
                "HTML",
                "EMAIL",
                name="generated_doc_output_format",
                schema="automation",
            ),
            nullable=False,
        ),
        sa.Column("file_path", sa.String(500), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("sent_to", sa.String(255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("context_snapshot", JSONB(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "FINAL",
                "SENT",
                "SUPERSEDED",
                "VOIDED",
                name="generated_doc_status",
                schema="automation",
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("superseded_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
            name="fk_generated_doc_org",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["automation.document_template.template_id"],
            name="fk_generated_doc_template",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["automation.generated_document.document_id"],
            name="fk_generated_doc_superseded",
        ),
        schema="automation",
    )

    # Create indexes
    op.create_index(
        "idx_generated_doc_org",
        "generated_document",
        ["organization_id"],
        schema="automation",
    )
    op.create_index(
        "idx_generated_doc_entity",
        "generated_document",
        ["entity_type", "entity_id"],
        schema="automation",
    )
    op.create_index(
        "idx_generated_doc_template",
        "generated_document",
        ["template_id"],
        schema="automation",
    )
    op.create_index(
        "idx_generated_doc_number",
        "generated_document",
        ["organization_id", "document_number"],
        schema="automation",
    )


def downgrade() -> None:
    # Drop generated_document table and indexes
    op.drop_index(
        "idx_generated_doc_number", table_name="generated_document", schema="automation"
    )
    op.drop_index(
        "idx_generated_doc_template",
        table_name="generated_document",
        schema="automation",
    )
    op.drop_index(
        "idx_generated_doc_entity", table_name="generated_document", schema="automation"
    )
    op.drop_index(
        "idx_generated_doc_org", table_name="generated_document", schema="automation"
    )
    op.drop_table("generated_document", schema="automation")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS automation.generated_doc_status;")
    op.execute("DROP TYPE IF EXISTS automation.generated_doc_output_format;")

    # Note: Cannot remove enum values from document_template_type in PostgreSQL
    # Would need to recreate the entire enum (not recommended for production)
