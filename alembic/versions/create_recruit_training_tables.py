"""Create recruitment and training tables.

Revision ID: create_recruit_training
Revises: create_leave_attendance_tables
Create Date: 2025-01-20

Phase 4: Recruitment & Training tables for People module.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision: str = "create_recruit_training"
down_revision: Union[str, None] = "create_leave_attendance_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create schemas
    op.execute("CREATE SCHEMA IF NOT EXISTS recruit")
    op.execute("CREATE SCHEMA IF NOT EXISTS training")

    # ========== RECRUITMENT SCHEMA ==========

    # Create enum types for recruit schema
    bind = op.get_bind()
    ensure_enum(
        bind,
        "job_opening_status",
        "DRAFT",
        "OPEN",
        "ON_HOLD",
        "CLOSED",
        "FILLED",
        "CANCELLED",
        schema="recruit",
    )
    ensure_enum(
        bind,
        "applicant_status",
        "NEW",
        "SCREENING",
        "SHORTLISTED",
        "INTERVIEW_SCHEDULED",
        "INTERVIEW_COMPLETED",
        "SELECTED",
        "OFFER_EXTENDED",
        "OFFER_ACCEPTED",
        "OFFER_DECLINED",
        "HIRED",
        "REJECTED",
        "WITHDRAWN",
        schema="recruit",
    )
    ensure_enum(
        bind,
        "interview_round",
        "PHONE_SCREENING",
        "TECHNICAL_ROUND_1",
        "TECHNICAL_ROUND_2",
        "MANAGER_ROUND",
        "HR_ROUND",
        "FINAL_ROUND",
        "CULTURE_FIT",
        schema="recruit",
    )
    ensure_enum(
        bind,
        "interview_status",
        "SCHEDULED",
        "RESCHEDULED",
        "IN_PROGRESS",
        "COMPLETED",
        "CANCELLED",
        "NO_SHOW",
        schema="recruit",
    )
    ensure_enum(
        bind,
        "offer_status",
        "DRAFT",
        "PENDING_APPROVAL",
        "APPROVED",
        "EXTENDED",
        "ACCEPTED",
        "DECLINED",
        "WITHDRAWN",
        "EXPIRED",
        schema="recruit",
    )

    # Job Opening table
    op.create_table(
        "job_opening",
        sa.Column(
            "job_opening_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_code", sa.String(30), nullable=False),
        sa.Column("job_title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("designation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reports_to_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "number_of_positions", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("positions_filled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("posted_on", sa.Date(), nullable=True),
        sa.Column("closes_on", sa.Date(), nullable=True),
        sa.Column(
            "employment_type", sa.String(30), nullable=False, server_default="FULL_TIME"
        ),
        sa.Column("location", sa.String(100), nullable=True),
        sa.Column("is_remote", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("min_salary", sa.Numeric(12, 2), nullable=True),
        sa.Column("max_salary", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="NGN"),
        sa.Column("min_experience_years", sa.Integer(), nullable=True),
        sa.Column("required_skills", sa.Text(), nullable=True),
        sa.Column("preferred_skills", sa.Text(), nullable=True),
        sa.Column("education_requirements", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "OPEN",
                "ON_HOLD",
                "CLOSED",
                "FILLED",
                "CANCELLED",
                name="job_opening_status",
                schema="recruit",
                create_type=False,
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("job_opening_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["department_id"], ["hr.department.department_id"]),
        sa.ForeignKeyConstraint(["designation_id"], ["hr.designation.designation_id"]),
        sa.ForeignKeyConstraint(["reports_to_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint(
            "organization_id", "job_code", name="uq_job_opening_org_code"
        ),
        schema="recruit",
    )
    op.create_index(
        "idx_job_opening_org", "job_opening", ["organization_id"], schema="recruit"
    )
    op.create_index(
        "idx_job_opening_status",
        "job_opening",
        ["organization_id", "status"],
        schema="recruit",
    )
    op.create_index(
        "idx_job_opening_dept",
        "job_opening",
        ["organization_id", "department_id"],
        schema="recruit",
    )
    op.create_index(
        "idx_job_opening_erpnext", "job_opening", ["erpnext_id"], schema="recruit"
    )

    # Job Applicant table
    op.create_table(
        "job_applicant",
        sa.Column(
            "applicant_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_number", sa.String(30), nullable=False, unique=True),
        sa.Column("job_opening_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("first_name", sa.String(80), nullable=False),
        sa.Column("last_name", sa.String(80), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("gender", sa.String(20), nullable=True),
        sa.Column("city", sa.String(80), nullable=True),
        sa.Column("country_code", sa.String(2), nullable=True),
        sa.Column("current_employer", sa.String(200), nullable=True),
        sa.Column("current_job_title", sa.String(200), nullable=True),
        sa.Column("years_of_experience", sa.Integer(), nullable=True),
        sa.Column("highest_qualification", sa.String(100), nullable=True),
        sa.Column("skills", sa.Text(), nullable=True),
        sa.Column(
            "applied_on",
            sa.Date(),
            server_default=sa.text("CURRENT_DATE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("referral_employee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cover_letter", sa.Text(), nullable=True),
        sa.Column("resume_url", sa.String(500), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "NEW",
                "SCREENING",
                "SHORTLISTED",
                "INTERVIEW_SCHEDULED",
                "INTERVIEW_COMPLETED",
                "SELECTED",
                "OFFER_EXTENDED",
                "OFFER_ACCEPTED",
                "OFFER_DECLINED",
                "HIRED",
                "REJECTED",
                "WITHDRAWN",
                name="applicant_status",
                schema="recruit",
                create_type=False,
            ),
            nullable=False,
            server_default="NEW",
        ),
        sa.Column("overall_rating", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("applicant_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["job_opening_id"], ["recruit.job_opening.job_opening_id"]
        ),
        sa.ForeignKeyConstraint(["referral_employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["status_changed_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        schema="recruit",
    )
    op.create_index(
        "idx_job_applicant_org", "job_applicant", ["organization_id"], schema="recruit"
    )
    op.create_index(
        "idx_job_applicant_status",
        "job_applicant",
        ["organization_id", "status"],
        schema="recruit",
    )
    op.create_index(
        "idx_job_applicant_job",
        "job_applicant",
        ["job_opening_id", "status"],
        schema="recruit",
    )
    op.create_index(
        "idx_job_applicant_email",
        "job_applicant",
        ["organization_id", "email"],
        schema="recruit",
    )
    op.create_index(
        "idx_job_applicant_erpnext", "job_applicant", ["erpnext_id"], schema="recruit"
    )

    # Interview table
    op.create_table(
        "interview",
        sa.Column(
            "interview_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("applicant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "round",
            postgresql.ENUM(
                "PHONE_SCREENING",
                "TECHNICAL_ROUND_1",
                "TECHNICAL_ROUND_2",
                "MANAGER_ROUND",
                "HR_ROUND",
                "FINAL_ROUND",
                "CULTURE_FIT",
                name="interview_round",
                schema="recruit",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "interview_type", sa.String(30), nullable=False, server_default="IN_PERSON"
        ),
        sa.Column("scheduled_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("meeting_link", sa.String(500), nullable=True),
        sa.Column("interviewer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "SCHEDULED",
                "RESCHEDULED",
                "IN_PROGRESS",
                "COMPLETED",
                "CANCELLED",
                "NO_SHOW",
                name="interview_status",
                schema="recruit",
                create_type=False,
            ),
            nullable=False,
            server_default="SCHEDULED",
        ),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("recommendation", sa.String(20), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("strengths", sa.Text(), nullable=True),
        sa.Column("weaknesses", sa.Text(), nullable=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("interview_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["applicant_id"], ["recruit.job_applicant.applicant_id"]
        ),
        sa.ForeignKeyConstraint(["interviewer_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        schema="recruit",
    )
    op.create_index(
        "idx_interview_org", "interview", ["organization_id"], schema="recruit"
    )
    op.create_index(
        "idx_interview_applicant", "interview", ["applicant_id"], schema="recruit"
    )
    op.create_index(
        "idx_interview_status",
        "interview",
        ["organization_id", "status"],
        schema="recruit",
    )
    op.create_index(
        "idx_interview_date",
        "interview",
        ["organization_id", "scheduled_from"],
        schema="recruit",
    )
    op.create_index(
        "idx_interview_erpnext", "interview", ["erpnext_id"], schema="recruit"
    )

    # Job Offer table
    op.create_table(
        "job_offer",
        sa.Column(
            "offer_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_number", sa.String(30), nullable=False, unique=True),
        sa.Column("applicant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_opening_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("designation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("offer_date", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("expected_joining_date", sa.Date(), nullable=False),
        sa.Column("base_salary", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="NGN"),
        sa.Column(
            "pay_frequency", sa.String(20), nullable=False, server_default="MONTHLY"
        ),
        sa.Column("signing_bonus", sa.Numeric(12, 2), nullable=True),
        sa.Column("relocation_allowance", sa.Numeric(12, 2), nullable=True),
        sa.Column("other_benefits", sa.Text(), nullable=True),
        sa.Column(
            "employment_type", sa.String(30), nullable=False, server_default="FULL_TIME"
        ),
        sa.Column("probation_months", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "notice_period_days", sa.Integer(), nullable=False, server_default="30"
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "PENDING_APPROVAL",
                "APPROVED",
                "EXTENDED",
                "ACCEPTED",
                "DECLINED",
                "WITHDRAWN",
                "EXPIRED",
                name="offer_status",
                schema="recruit",
                create_type=False,
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("extended_on", sa.Date(), nullable=True),
        sa.Column("responded_on", sa.Date(), nullable=True),
        sa.Column("decline_reason", sa.Text(), nullable=True),
        sa.Column(
            "converted_to_employee_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("terms_and_conditions", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("offer_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["applicant_id"], ["recruit.job_applicant.applicant_id"]
        ),
        sa.ForeignKeyConstraint(
            ["job_opening_id"], ["recruit.job_opening.job_opening_id"]
        ),
        sa.ForeignKeyConstraint(["designation_id"], ["hr.designation.designation_id"]),
        sa.ForeignKeyConstraint(["department_id"], ["hr.department.department_id"]),
        sa.ForeignKeyConstraint(
            ["converted_to_employee_id"], ["hr.employee.employee_id"]
        ),
        sa.ForeignKeyConstraint(["status_changed_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        schema="recruit",
    )
    op.create_index(
        "idx_job_offer_org", "job_offer", ["organization_id"], schema="recruit"
    )
    op.create_index(
        "idx_job_offer_applicant", "job_offer", ["applicant_id"], schema="recruit"
    )
    op.create_index(
        "idx_job_offer_status",
        "job_offer",
        ["organization_id", "status"],
        schema="recruit",
    )
    op.create_index(
        "idx_job_offer_erpnext", "job_offer", ["erpnext_id"], schema="recruit"
    )

    # ========== TRAINING SCHEMA ==========

    # Create enum types for training schema
    ensure_enum(
        bind,
        "training_program_status",
        "DRAFT",
        "ACTIVE",
        "ARCHIVED",
        schema="training",
    )
    ensure_enum(
        bind,
        "training_event_status",
        "DRAFT",
        "SCHEDULED",
        "IN_PROGRESS",
        "COMPLETED",
        "CANCELLED",
        schema="training",
    )
    ensure_enum(
        bind,
        "attendee_status",
        "INVITED",
        "CONFIRMED",
        "ATTENDED",
        "ABSENT",
        "CANCELLED",
        schema="training",
    )

    # Training Program table
    op.create_table(
        "training_program",
        sa.Column(
            "program_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("program_code", sa.String(30), nullable=False),
        sa.Column("program_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "training_type", sa.String(30), nullable=False, server_default="INTERNAL"
        ),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column("duration_hours", sa.Integer(), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cost_per_attendee", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="NGN"),
        sa.Column("objectives", sa.Text(), nullable=True),
        sa.Column("prerequisites", sa.Text(), nullable=True),
        sa.Column("syllabus", sa.Text(), nullable=True),
        sa.Column("provider_name", sa.String(200), nullable=True),
        sa.Column("provider_contact", sa.String(200), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "ACTIVE",
                "ARCHIVED",
                name="training_program_status",
                schema="training",
                create_type=False,
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("program_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["department_id"], ["hr.department.department_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint(
            "organization_id", "program_code", name="uq_training_program_code"
        ),
        schema="training",
    )
    op.create_index(
        "idx_training_program_org",
        "training_program",
        ["organization_id"],
        schema="training",
    )
    op.create_index(
        "idx_training_program_status",
        "training_program",
        ["organization_id", "status"],
        schema="training",
    )
    op.create_index(
        "idx_training_program_erpnext",
        "training_program",
        ["erpnext_id"],
        schema="training",
    )

    # Training Event table
    op.create_table(
        "training_event",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column(
            "event_type", sa.String(20), nullable=False, server_default="IN_PERSON"
        ),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("meeting_link", sa.String(500), nullable=True),
        sa.Column("trainer_name", sa.String(200), nullable=True),
        sa.Column("trainer_email", sa.String(255), nullable=True),
        sa.Column("trainer_employee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("max_attendees", sa.Integer(), nullable=True),
        sa.Column("total_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="NGN"),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "SCHEDULED",
                "IN_PROGRESS",
                "COMPLETED",
                "CANCELLED",
                name="training_event_status",
                schema="training",
                create_type=False,
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("average_rating", sa.Numeric(3, 2), nullable=True),
        sa.Column("feedback_notes", sa.Text(), nullable=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("event_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["program_id"], ["training.training_program.program_id"]
        ),
        sa.ForeignKeyConstraint(["trainer_employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        schema="training",
    )
    op.create_index(
        "idx_training_event_org",
        "training_event",
        ["organization_id"],
        schema="training",
    )
    op.create_index(
        "idx_training_event_program",
        "training_event",
        ["program_id"],
        schema="training",
    )
    op.create_index(
        "idx_training_event_dates",
        "training_event",
        ["organization_id", "start_date", "end_date"],
        schema="training",
    )
    op.create_index(
        "idx_training_event_status",
        "training_event",
        ["organization_id", "status"],
        schema="training",
    )
    op.create_index(
        "idx_training_event_erpnext",
        "training_event",
        ["erpnext_id"],
        schema="training",
    )

    # Training Attendee table
    op.create_table(
        "training_attendee",
        sa.Column(
            "attendee_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "INVITED",
                "CONFIRMED",
                "ATTENDED",
                "ABSENT",
                "CANCELLED",
                name="attendee_status",
                schema="training",
                create_type=False,
            ),
            nullable=False,
            server_default="INVITED",
        ),
        sa.Column("invited_on", sa.Date(), nullable=True),
        sa.Column("confirmed_on", sa.Date(), nullable=True),
        sa.Column("attended_on", sa.Date(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column(
            "certificate_issued", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("certificate_number", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("attendee_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["event_id"], ["training.training_event.event_id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        schema="training",
    )
    op.create_index(
        "idx_training_attendee_org",
        "training_attendee",
        ["organization_id"],
        schema="training",
    )
    op.create_index(
        "idx_training_attendee_event",
        "training_attendee",
        ["event_id"],
        schema="training",
    )
    op.create_index(
        "idx_training_attendee_employee",
        "training_attendee",
        ["employee_id"],
        schema="training",
    )

    # ========== RLS POLICIES ==========

    # Enable RLS on all recruit tables
    for table in ["job_opening", "job_applicant", "interview", "job_offer"]:
        op.execute(f"ALTER TABLE recruit.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON recruit.{table}
            USING (organization_id = current_setting('app.current_organization_id')::uuid)
        """)

    # Enable RLS on all training tables
    for table in ["training_program", "training_event", "training_attendee"]:
        op.execute(f"ALTER TABLE training.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON training.{table}
            USING (organization_id = current_setting('app.current_organization_id')::uuid)
        """)


def downgrade() -> None:
    # Drop RLS policies
    for table in ["job_opening", "job_applicant", "interview", "job_offer"]:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON recruit.{table}")
        op.execute(f"ALTER TABLE recruit.{table} DISABLE ROW LEVEL SECURITY")

    for table in ["training_program", "training_event", "training_attendee"]:
        op.execute(
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON training.{table}"
        )
        op.execute(f"ALTER TABLE training.{table} DISABLE ROW LEVEL SECURITY")

    # Drop training tables
    op.drop_table("training_attendee", schema="training")
    op.drop_table("training_event", schema="training")
    op.drop_table("training_program", schema="training")

    # Drop recruit tables
    op.drop_table("job_offer", schema="recruit")
    op.drop_table("interview", schema="recruit")
    op.drop_table("job_applicant", schema="recruit")
    op.drop_table("job_opening", schema="recruit")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS training.attendee_status")
    op.execute("DROP TYPE IF EXISTS training.training_event_status")
    op.execute("DROP TYPE IF EXISTS training.training_program_status")

    op.execute("DROP TYPE IF EXISTS recruit.offer_status")
    op.execute("DROP TYPE IF EXISTS recruit.interview_status")
    op.execute("DROP TYPE IF EXISTS recruit.interview_round")
    op.execute("DROP TYPE IF EXISTS recruit.applicant_status")
    op.execute("DROP TYPE IF EXISTS recruit.job_opening_status")

    # Drop schemas
    op.execute("DROP SCHEMA IF EXISTS training CASCADE")
    op.execute("DROP SCHEMA IF EXISTS recruit CASCADE")
