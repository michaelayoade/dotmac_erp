"""Add competency and job description tables.

Revision ID: 20260130_competency_jd
Revises: 20260128_merge_handbook
Create Date: 2026-01-30

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260130_competency_jd"
down_revision = "20260128_merge_handbook"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enums using DO/EXCEPTION for idempotency
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE hr.competency_category AS ENUM (
                'core', 'functional', 'leadership', 'behavioral'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE hr.job_description_status AS ENUM (
                'draft', 'active', 'under_review', 'archived'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # Create competency table
    op.create_table(
        "competency",
        sa.Column(
            "competency_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("competency_code", sa.String(20), nullable=False),
        sa.Column("competency_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "category",
            postgresql.ENUM(
                "core",
                "functional",
                "leadership",
                "behavioral",
                name="competency_category",
                schema="hr",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("level_1_description", sa.Text(), nullable=True),
        sa.Column("level_2_description", sa.Text(), nullable=True),
        sa.Column("level_3_description", sa.Text(), nullable=True),
        sa.Column("level_4_description", sa.Text(), nullable=True),
        sa.Column("level_5_description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, default=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.PrimaryKeyConstraint("competency_id"),
        schema="hr",
    )
    op.create_index(
        "idx_competency_org", "competency", ["organization_id"], schema="hr"
    )
    op.create_index(
        "idx_competency_code", "competency", ["competency_code"], schema="hr"
    )

    # Create job_description table
    op.create_table(
        "job_description",
        sa.Column(
            "job_description_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("designation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("jd_code", sa.String(20), nullable=False),
        sa.Column("job_title", sa.String(150), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, default=1),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "draft",
                "active",
                "under_review",
                "archived",
                name="job_description_status",
                schema="hr",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("key_responsibilities", sa.Text(), nullable=True),
        sa.Column("education_requirements", sa.Text(), nullable=True),
        sa.Column("experience_requirements", sa.Text(), nullable=True),
        sa.Column("min_years_experience", sa.Integer(), nullable=True),
        sa.Column("max_years_experience", sa.Integer(), nullable=True),
        sa.Column("technical_skills", sa.Text(), nullable=True),
        sa.Column("certifications_required", sa.Text(), nullable=True),
        sa.Column("certifications_preferred", sa.Text(), nullable=True),
        sa.Column("work_location", sa.String(100), nullable=True),
        sa.Column("travel_requirements", sa.Text(), nullable=True),
        sa.Column("physical_requirements", sa.Text(), nullable=True),
        sa.Column("salary_min", sa.Numeric(15, 2), nullable=True),
        sa.Column("salary_max", sa.Numeric(15, 2), nullable=True),
        sa.Column("salary_currency", sa.String(3), nullable=True),
        sa.Column("reports_to", sa.String(150), nullable=True),
        sa.Column("direct_reports", sa.Text(), nullable=True),
        sa.Column("additional_notes", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, default=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        # ERPNext sync fields
        sa.Column("erpnext_id", sa.String(140), nullable=True),
        sa.Column("erpnext_name", sa.String(140), nullable=True),
        sa.Column("erpnext_modified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erpnext_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["designation_id"], ["hr.designation.designation_id"]),
        sa.ForeignKeyConstraint(["department_id"], ["hr.department.department_id"]),
        sa.PrimaryKeyConstraint("job_description_id"),
        schema="hr",
    )
    op.create_index(
        "idx_job_description_org", "job_description", ["organization_id"], schema="hr"
    )
    op.create_index(
        "idx_job_description_code", "job_description", ["jd_code"], schema="hr"
    )
    op.create_index(
        "idx_job_description_designation",
        "job_description",
        ["designation_id"],
        schema="hr",
    )
    op.create_index(
        "idx_job_description_department",
        "job_description",
        ["department_id"],
        schema="hr",
    )

    # Create job_description_competency junction table
    op.create_table(
        "job_description_competency",
        sa.Column(
            "jd_competency_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_description_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("competency_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("required_level", sa.Integer(), nullable=False, default=3),
        sa.Column("weight", sa.Numeric(5, 2), nullable=True),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, default=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_description_id"], ["hr.job_description.job_description_id"]
        ),
        sa.ForeignKeyConstraint(["competency_id"], ["hr.competency.competency_id"]),
        sa.PrimaryKeyConstraint("jd_competency_id"),
        schema="hr",
    )
    op.create_index(
        "idx_jd_competency_jd",
        "job_description_competency",
        ["job_description_id"],
        schema="hr",
    )
    op.create_index(
        "idx_jd_competency_comp",
        "job_description_competency",
        ["competency_id"],
        schema="hr",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_jd_competency_comp", table_name="job_description_competency", schema="hr"
    )
    op.drop_index(
        "idx_jd_competency_jd", table_name="job_description_competency", schema="hr"
    )
    op.drop_table("job_description_competency", schema="hr")

    op.drop_index(
        "idx_job_description_department", table_name="job_description", schema="hr"
    )
    op.drop_index(
        "idx_job_description_designation", table_name="job_description", schema="hr"
    )
    op.drop_index("idx_job_description_code", table_name="job_description", schema="hr")
    op.drop_index("idx_job_description_org", table_name="job_description", schema="hr")
    op.drop_table("job_description", schema="hr")

    op.drop_index("idx_competency_code", table_name="competency", schema="hr")
    op.drop_index("idx_competency_org", table_name="competency", schema="hr")
    op.drop_table("competency", schema="hr")

    op.execute("DROP TYPE IF EXISTS hr.job_description_status")
    op.execute("DROP TYPE IF EXISTS hr.competency_category")
