"""Add learning and assessment tables.

Revision ID: 20260606_learning_assessment
Revises: 20260605_match_decision_audit
Create Date: 2026-06-06
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision = "20260606_learning_assessment"
down_revision = "20260603_add_match_state"
branch_labels = None
depends_on = None

SCHEMA = "training"

TABLES = [
    "training_course",
    "training_course_prerequisite",
    "training_course_module",
    "training_lesson",
    "training_assessment",
    "training_question_bank",
    "training_question",
    "training_question_option",
    "training_assessment_question",
    "training_question_tag",
    "training_question_tag_map",
    "training_course_assignment",
    "training_course_progress",
    "training_lesson_progress",
    "training_exam_attempt",
    "training_exam_answer",
]


def uuid_pk(name: str = "id") -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def org_id_col() -> sa.Column:
    return sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False)


def timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def org_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id"],
        ["core_org.organization.organization_id"],
    )


def enable_rls() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {SCHEMA}.{table}
            USING (should_bypass_rls() OR organization_id = get_current_organization_id())
            WITH CHECK (should_bypass_rls() OR organization_id = get_current_organization_id())
            """
        )


def disable_rls() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS training")
    bind = op.get_bind()

    lesson_type = ensure_enum(
        bind,
        "training_lesson_type",
        "video",
        "pdf",
        "text",
        "link",
        schema=SCHEMA,
    )
    course_status = ensure_enum(
        bind,
        "training_course_status",
        "draft",
        "published",
        "archived",
        schema=SCHEMA,
    )
    assessment_status = ensure_enum(
        bind,
        "training_assessment_status",
        "draft",
        "published",
        "archived",
        schema=SCHEMA,
    )
    question_type = ensure_enum(
        bind,
        "training_question_type",
        "multiple_choice",
        "multiple_select",
        "true_false",
        "fill_gap",
        "short_answer",
        "essay",
        schema=SCHEMA,
    )
    question_difficulty = ensure_enum(
        bind,
        "training_question_difficulty",
        "easy",
        "medium",
        "hard",
        schema=SCHEMA,
    )
    progress_status = ensure_enum(
        bind,
        "training_progress_status",
        "not_started",
        "in_progress",
        "completed",
        schema=SCHEMA,
    )

    op.create_table(
        "training_course",
        uuid_pk(),
        org_id_col(),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("thumbnail_file_id", sa.String(length=255), nullable=True),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "pass_mark",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="70.00",
        ),
        sa.Column(
            "retake_limit",
            sa.Integer(),
            nullable=False,
            server_default="3",
        ),
        sa.Column(
            "version_number",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "is_mandatory",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "status",
            course_status,
            nullable=False,
            server_default="draft",
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(["department_id"], ["hr.department.department_id"]),
        sa.ForeignKeyConstraint(["created_by"], ["people.id"]),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_course_org_active",
        "training_course",
        ["organization_id", "is_active"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_course_department",
        "training_course",
        ["organization_id", "department_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_course_status",
        "training_course",
        ["organization_id", "status"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_course_prerequisite",
        uuid_pk(),
        org_id_col(),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prerequisite_course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["training.training_course.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prerequisite_course_id"],
            ["training.training_course.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "course_id",
            "prerequisite_course_id",
            name="uq_training_course_prerequisite",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_prerequisite_course",
        "training_course_prerequisite",
        ["organization_id", "course_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_prerequisite_required",
        "training_course_prerequisite",
        ["organization_id", "prerequisite_course_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_course_module",
        uuid_pk(),
        org_id_col(),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["training.training_course.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("course_id", "sequence", name="uq_training_module_sequence"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_module_course",
        "training_course_module",
        ["organization_id", "course_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_lesson",
        uuid_pk(),
        org_id_col(),
        sa.Column("module_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lesson_type", lesson_type, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("file_id", sa.String(length=255), nullable=True),
        sa.Column("youtube_url", sa.String(length=500), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["module_id"],
            ["training.training_course_module.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("module_id", "sequence", name="uq_training_lesson_sequence"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_lesson_module",
        "training_lesson",
        ["organization_id", "module_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_assessment",
        uuid_pk(),
        org_id_col(),
        sa.Column("module_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "pass_mark",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="70.00",
        ),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "randomize_questions",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "randomize_options",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "status",
            assessment_status,
            nullable=False,
            server_default="draft",
        ),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["module_id"],
            ["training.training_course_module.id"],
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_assessment_module",
        "training_assessment",
        ["organization_id", "module_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_assessment_status",
        "training_assessment",
        ["organization_id", "status"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_question_bank",
        uuid_pk(),
        org_id_col(),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(["department_id"], ["hr.department.department_id"]),
        sa.ForeignKeyConstraint(["created_by"], ["people.id"]),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_question_bank_org_active",
        "training_question_bank",
        ["organization_id", "is_active"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_question_bank_department",
        "training_question_bank",
        ["organization_id", "department_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_question",
        uuid_pk(),
        org_id_col(),
        sa.Column("question_bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_type", question_type, nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column(
            "difficulty_level",
            question_difficulty,
            nullable=False,
            server_default="medium",
        ),
        sa.Column(
            "points",
            sa.Numeric(8, 2),
            nullable=False,
            server_default="1.00",
        ),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["question_bank_id"],
            ["training.training_question_bank.id"],
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_question_bank",
        "training_question",
        ["organization_id", "question_bank_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_question_type",
        "training_question",
        ["organization_id", "question_type"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_question_difficulty",
        "training_question",
        ["organization_id", "difficulty_level"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_question_option",
        uuid_pk(),
        org_id_col(),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("option_text", sa.Text(), nullable=False),
        sa.Column(
            "is_correct",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["training.training_question.id"],
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_option_question",
        "training_question_option",
        ["organization_id", "question_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_assessment_question",
        uuid_pk(),
        org_id_col(),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("points_override", sa.Numeric(8, 2), nullable=True),
        sa.Column(
            "is_required",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["training.training_assessment.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["question_id"], ["training.training_question.id"]),
        sa.UniqueConstraint(
            "assessment_id",
            "question_id",
            name="uq_training_assessment_question",
        ),
        sa.UniqueConstraint(
            "assessment_id",
            "sequence",
            name="uq_training_assessment_question_sequence",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_assessment_question_assessment",
        "training_assessment_question",
        ["organization_id", "assessment_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_assessment_question_question",
        "training_assessment_question",
        ["organization_id", "question_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_question_tag",
        uuid_pk(),
        org_id_col(),
        sa.Column("name", sa.String(length=80), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.UniqueConstraint(
            "organization_id",
            "name",
            name="uq_training_question_tag_name",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_question_tag_org",
        "training_question_tag",
        ["organization_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_question_tag_map",
        uuid_pk(),
        org_id_col(),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["training.training_question.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["training.training_question_tag.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("question_id", "tag_id", name="uq_training_question_tag_map"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_question_tag_map_question",
        "training_question_tag_map",
        ["organization_id", "question_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_question_tag_map_tag",
        "training_question_tag_map",
        ["organization_id", "tag_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_course_assignment",
        uuid_pk(),
        org_id_col(),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "assigned_date",
            sa.Date(),
            nullable=False,
            server_default=sa.text("CURRENT_DATE"),
        ),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "assignment_source",
            sa.String(length=40),
            nullable=False,
            server_default="employee",
        ),
        sa.Column("assignment_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "is_mandatory",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "course_version_number",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["training.training_course.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["assigned_by"], ["people.id"]),
        sa.UniqueConstraint(
            "course_id",
            "employee_id",
            name="uq_training_course_assignment_employee",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_assignment_course",
        "training_course_assignment",
        ["organization_id", "course_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_assignment_employee",
        "training_course_assignment",
        ["organization_id", "employee_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_assignment_due",
        "training_course_assignment",
        ["organization_id", "due_date"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_assignment_source",
        "training_course_assignment",
        ["organization_id", "assignment_source"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_assignment_date",
        "training_course_assignment",
        ["organization_id", "assigned_date"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_course_progress",
        uuid_pk(),
        org_id_col(),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "completion_percentage",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="0.00",
        ),
        sa.Column(
            "course_version_number",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "status",
            progress_status,
            nullable=False,
            server_default="not_started",
        ),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["training.training_course.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.UniqueConstraint(
            "course_id",
            "employee_id",
            name="uq_training_course_progress_employee",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_progress_course",
        "training_course_progress",
        ["organization_id", "course_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_progress_employee",
        "training_course_progress",
        ["organization_id", "employee_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_progress_status",
        "training_course_progress",
        ["organization_id", "status"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_lesson_progress",
        uuid_pk(),
        org_id_col(),
        sa.Column("lesson_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "completed",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["lesson_id"],
            ["training.training_lesson.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.UniqueConstraint(
            "lesson_id",
            "employee_id",
            name="uq_training_lesson_progress_employee",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_lesson_progress_lesson",
        "training_lesson_progress",
        ["organization_id", "lesson_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_lesson_progress_employee",
        "training_lesson_progress",
        ["organization_id", "employee_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_exam_attempt",
        uuid_pk(),
        org_id_col(),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "course_version_number",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("assessment_snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("score", sa.Numeric(8, 2), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["training.training_assessment.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_attempt_assessment",
        "training_exam_attempt",
        ["organization_id", "assessment_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_attempt_employee",
        "training_exam_attempt",
        ["organization_id", "employee_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_attempt_submitted",
        "training_exam_attempt",
        ["organization_id", "submitted_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "training_exam_answer",
        uuid_pk(),
        org_id_col(),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("question_text_snapshot", sa.Text(), nullable=False),
        sa.Column("question_type_snapshot", sa.String(length=40), nullable=False),
        sa.Column("options_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("correct_answer_snapshot", sa.Text(), nullable=True),
        sa.Column("score_awarded", sa.Numeric(8, 2), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("graded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["training.training_exam_attempt.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["question_id"], ["training.training_question.id"]),
        sa.ForeignKeyConstraint(["graded_by"], ["people.id"]),
        sa.UniqueConstraint(
            "attempt_id",
            "question_id",
            name="uq_training_exam_answer_question",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_answer_attempt",
        "training_exam_answer",
        ["organization_id", "attempt_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_answer_question",
        "training_exam_answer",
        ["organization_id", "question_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_training_answer_manual_review",
        "training_exam_answer",
        ["organization_id", "question_type_snapshot", "score_awarded"],
        schema=SCHEMA,
    )

    enable_rls()


def downgrade() -> None:
    disable_rls()

    for table in reversed(TABLES):
        op.drop_table(table, schema=SCHEMA)

    op.execute("DROP TYPE IF EXISTS training.training_progress_status")
    op.execute("DROP TYPE IF EXISTS training.training_question_difficulty")
    op.execute("DROP TYPE IF EXISTS training.training_question_type")
    op.execute("DROP TYPE IF EXISTS training.training_assessment_status")
    op.execute("DROP TYPE IF EXISTS training.training_course_status")
    op.execute("DROP TYPE IF EXISTS training.training_lesson_type")
