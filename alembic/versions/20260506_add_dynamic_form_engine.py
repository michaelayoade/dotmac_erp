"""add_dynamic_form_engine

Revision ID: 20260506_dynamic_forms
Revises: 20260429_fix_vat_75_inclusive_accounts
Create Date: 2026-05-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260506_dynamic_forms"
down_revision = "20260429_fix_vat_75_inclusive_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS forms")

    form_status = postgresql.ENUM(
        "DRAFT",
        "PUBLISHED",
        "ARCHIVED",
        name="form_status",
        create_type=False,
    )
    form_field_type = postgresql.ENUM(
        "TEXT",
        "LONG_TEXT",
        "NUMBER",
        "DATE",
        "EMAIL",
        "PHONE",
        "URL",
        "SINGLE_CHOICE",
        "MULTI_CHOICE",
        "DROPDOWN",
        "CHECKBOX",
        "YES_NO",
        "FILE",
        "IMAGE",
        "PDF",
        "CONSENT",
        "RATING",
        name="form_field_type",
        create_type=False,
    )
    form_submission_status = postgresql.ENUM(
        "SUBMITTED",
        "VOIDED",
        name="form_submission_status",
        create_type=False,
    )
    bind = op.get_bind()
    form_status.create(bind, checkfirst=True)
    form_field_type.create(bind, checkfirst=True)
    form_submission_status.create(bind, checkfirst=True)

    op.create_table(
        "form",
        sa.Column(
            "form_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("form_type", sa.String(length=60), nullable=False),
        sa.Column("owner_entity_type", sa.String(length=80), nullable=True),
        sa.Column("owner_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.PrimaryKeyConstraint("form_id"),
        schema="forms",
    )
    op.create_index(
        "idx_forms_form_org_type",
        "form",
        ["organization_id", "form_type"],
        schema="forms",
    )

    op.create_table(
        "form_version",
        sa.Column(
            "form_version_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("form_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", form_status, nullable=False),
        sa.Column(
            "settings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["form_id"], ["forms.form.form_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.PrimaryKeyConstraint("form_version_id"),
        sa.UniqueConstraint("form_id", "version_number", name="uq_form_version_number"),
        schema="forms",
    )
    op.create_index(
        "idx_forms_version_org_status",
        "form_version",
        ["organization_id", "status"],
        schema="forms",
    )

    op.create_table(
        "form_section",
        sa.Column(
            "section_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("form_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["form_version_id"],
            ["forms.form_version.form_version_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("section_id"),
        schema="forms",
    )
    op.create_index(
        "idx_forms_section_version",
        "form_section",
        ["form_version_id", "sort_order"],
        schema="forms",
    )

    op.create_table(
        "form_field",
        sa.Column(
            "field_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("form_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_key", sa.String(length=80), nullable=False),
        sa.Column("label", sa.String(length=240), nullable=False),
        sa.Column("field_type", form_field_type, nullable=False),
        sa.Column("help_text", sa.Text(), nullable=True),
        sa.Column("placeholder", sa.String(length=240), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("show_in_list", sa.Boolean(), nullable=False),
        sa.Column("is_filterable", sa.Boolean(), nullable=False),
        sa.Column("system_mapping", sa.String(length=60), nullable=True),
        sa.Column(
            "settings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "validation_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "visibility_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["form_version_id"],
            ["forms.form_version.form_version_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["section_id"], ["forms.form_section.section_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("field_id"),
        sa.UniqueConstraint(
            "form_version_id", "field_key", name="uq_form_field_version_key"
        ),
        schema="forms",
    )
    op.create_index(
        "idx_forms_field_version",
        "form_field",
        ["form_version_id", "sort_order"],
        schema="forms",
    )

    op.create_table(
        "form_field_option",
        sa.Column(
            "option_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=240), nullable=False),
        sa.Column("value", sa.String(length=160), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["field_id"], ["forms.form_field.field_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("option_id"),
        schema="forms",
    )
    op.create_index(
        "idx_forms_option_field",
        "form_field_option",
        ["field_id", "sort_order"],
        schema="forms",
    )

    op.create_table(
        "form_submission",
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("form_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_type", sa.String(length=80), nullable=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", form_submission_status, nullable=False),
        sa.Column("submitted_by_email", sa.String(length=255), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["form_version_id"], ["forms.form_version.form_version_id"]
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.PrimaryKeyConstraint("submission_id"),
        schema="forms",
    )
    op.create_index(
        "idx_forms_submission_org_form",
        "form_submission",
        ["organization_id", "form_version_id"],
        schema="forms",
    )
    op.create_index(
        "idx_forms_submission_subject",
        "form_submission",
        ["subject_type", "subject_id"],
        schema="forms",
    )

    op.create_table(
        "form_answer",
        sa.Column(
            "answer_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_key_snapshot", sa.String(length=80), nullable=False),
        sa.Column("field_label_snapshot", sa.String(length=240), nullable=False),
        sa.Column("field_type_snapshot", sa.String(length=40), nullable=False),
        sa.Column("value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("display_value", sa.Text(), nullable=True),
        sa.Column("file_url", sa.String(length=500), nullable=True),
        sa.Column("file_name", sa.String(length=240), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["forms.form_submission.submission_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["field_id"], ["forms.form_field.field_id"]),
        sa.PrimaryKeyConstraint("answer_id"),
        sa.UniqueConstraint("submission_id", "field_id", name="uq_form_answer_field"),
        schema="forms",
    )
    op.create_index(
        "idx_forms_answer_field",
        "form_answer",
        ["field_id"],
        schema="forms",
    )

    op.add_column(
        "job_opening",
        sa.Column(
            "application_form_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        schema="recruit",
    )
    op.create_foreign_key(
        "fk_job_opening_application_form_version",
        "job_opening",
        "form_version",
        ["application_form_version_id"],
        ["form_version_id"],
        source_schema="recruit",
        referent_schema="forms",
    )
    op.add_column(
        "job_applicant",
        sa.Column("form_submission_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="recruit",
    )
    op.create_foreign_key(
        "fk_job_applicant_form_submission",
        "job_applicant",
        "form_submission",
        ["form_submission_id"],
        ["submission_id"],
        source_schema="recruit",
        referent_schema="forms",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_job_applicant_form_submission",
        "job_applicant",
        schema="recruit",
        type_="foreignkey",
    )
    op.drop_column("job_applicant", "form_submission_id", schema="recruit")
    op.drop_constraint(
        "fk_job_opening_application_form_version",
        "job_opening",
        schema="recruit",
        type_="foreignkey",
    )
    op.drop_column("job_opening", "application_form_version_id", schema="recruit")
    op.drop_table("form_answer", schema="forms")
    op.drop_table("form_submission", schema="forms")
    op.drop_table("form_field_option", schema="forms")
    op.drop_table("form_field", schema="forms")
    op.drop_table("form_section", schema="forms")
    op.drop_table("form_version", schema="forms")
    op.drop_table("form", schema="forms")
    bind = op.get_bind()
    postgresql.ENUM(name="form_submission_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="form_field_type").drop(bind, checkfirst=True)
    postgresql.ENUM(name="form_status").drop(bind, checkfirst=True)
    op.execute("DROP SCHEMA IF EXISTS forms")
