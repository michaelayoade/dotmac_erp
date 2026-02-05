"""add hr lifecycle tables

Revision ID: 20250212_add_hr_lifecycle_tables
Revises: 4f4e6f737d70
Create Date: 2025-02-12 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from app.alembic_utils import ensure_enum


# revision identifiers, used by Alembic.
revision = "20250212_add_hr_lifecycle_tables"
down_revision = "4f4e6f737d70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    boarding_status = ensure_enum(
        bind,
        "boarding_status",
        "PENDING",
        "IN_PROGRESS",
        "COMPLETED",
    )
    separation_type = ensure_enum(
        bind,
        "separation_type",
        "RESIGNATION",
        "TERMINATION",
        "RETIREMENT",
        "REDUNDANCY",
        "CONTRACT_END",
        "DEATH",
        "OTHER",
    )
    separation_status = ensure_enum(
        bind,
        "separation_status",
        "PENDING",
        "IN_PROGRESS",
        "COMPLETED",
    )

    op.create_table(
        "employee_onboarding",
        sa.Column(
            "onboarding_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee.employee_id"),
            nullable=False,
        ),
        sa.Column(
            "job_applicant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recruit.job_applicant.applicant_id"),
            nullable=True,
        ),
        sa.Column(
            "job_offer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recruit.job_offer.offer_id"),
            nullable=True,
        ),
        sa.Column("date_of_joining", sa.Date(), nullable=True),
        sa.Column(
            "department_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.department.department_id"),
            nullable=True,
        ),
        sa.Column(
            "designation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.designation.designation_id"),
            nullable=True,
        ),
        sa.Column("template_name", sa.String(length=200), nullable=True),
        sa.Column("status", boarding_status, nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        schema="hr",
    )
    op.create_index(
        "idx_onboarding_status",
        "employee_onboarding",
        ["organization_id", "status"],
        schema="hr",
    )
    op.create_index(
        "idx_onboarding_employee",
        "employee_onboarding",
        ["organization_id", "employee_id"],
        schema="hr",
    )

    op.create_table(
        "employee_onboarding_activity",
        sa.Column(
            "activity_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "onboarding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee_onboarding.onboarding_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("activity_name", sa.String(length=500), nullable=False),
        sa.Column("assignee_role", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("completed_on", sa.Date(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=True),
        schema="hr",
    )
    op.create_index(
        "idx_onboarding_activity_onboarding",
        "employee_onboarding_activity",
        ["onboarding_id"],
        schema="hr",
    )

    op.create_table(
        "employee_separation",
        sa.Column(
            "separation_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee.employee_id"),
            nullable=False,
        ),
        sa.Column("separation_type", separation_type, nullable=True),
        sa.Column("resignation_letter_date", sa.Date(), nullable=True),
        sa.Column("separation_date", sa.Date(), nullable=True),
        sa.Column(
            "department_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.department.department_id"),
            nullable=True,
        ),
        sa.Column(
            "designation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.designation.designation_id"),
            nullable=True,
        ),
        sa.Column("reason_for_leaving", sa.Text(), nullable=True),
        sa.Column("exit_interview", sa.Text(), nullable=True),
        sa.Column("template_name", sa.String(length=200), nullable=True),
        sa.Column("status", separation_status, nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        schema="hr",
    )
    op.create_index(
        "idx_separation_status",
        "employee_separation",
        ["organization_id", "status"],
        schema="hr",
    )
    op.create_index(
        "idx_separation_employee",
        "employee_separation",
        ["organization_id", "employee_id"],
        schema="hr",
    )

    op.create_table(
        "employee_separation_activity",
        sa.Column(
            "activity_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "separation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee_separation.separation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("activity_name", sa.String(length=500), nullable=False),
        sa.Column("assignee_role", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("completed_on", sa.Date(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=True),
        schema="hr",
    )
    op.create_index(
        "idx_separation_activity_separation",
        "employee_separation_activity",
        ["separation_id"],
        schema="hr",
    )

    op.create_table(
        "employee_promotion",
        sa.Column(
            "promotion_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee.employee_id"),
            nullable=False,
        ),
        sa.Column("promotion_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        schema="hr",
    )
    op.create_index(
        "idx_promotion_employee",
        "employee_promotion",
        ["organization_id", "employee_id"],
        schema="hr",
    )

    op.create_table(
        "employee_promotion_detail",
        sa.Column(
            "detail_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "promotion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee_promotion.promotion_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("property_name", sa.String(length=100), nullable=False),
        sa.Column("current_value", sa.String(length=255), nullable=True),
        sa.Column("new_value", sa.String(length=255), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=True),
        schema="hr",
    )
    op.create_index(
        "idx_promotion_detail_promotion",
        "employee_promotion_detail",
        ["promotion_id"],
        schema="hr",
    )

    op.create_table(
        "employee_transfer",
        sa.Column(
            "transfer_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee.employee_id"),
            nullable=False,
        ),
        sa.Column("transfer_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        schema="hr",
    )
    op.create_index(
        "idx_transfer_employee",
        "employee_transfer",
        ["organization_id", "employee_id"],
        schema="hr",
    )

    op.create_table(
        "employee_transfer_detail",
        sa.Column(
            "detail_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "transfer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee_transfer.transfer_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("property_name", sa.String(length=100), nullable=False),
        sa.Column("current_value", sa.String(length=255), nullable=True),
        sa.Column("new_value", sa.String(length=255), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=True),
        schema="hr",
    )
    op.create_index(
        "idx_transfer_detail_transfer",
        "employee_transfer_detail",
        ["transfer_id"],
        schema="hr",
    )


def downgrade() -> None:
    op.drop_index("idx_transfer_detail_transfer", table_name="employee_transfer_detail", schema="hr")
    op.drop_table("employee_transfer_detail", schema="hr")
    op.drop_index("idx_transfer_employee", table_name="employee_transfer", schema="hr")
    op.drop_table("employee_transfer", schema="hr")

    op.drop_index("idx_promotion_detail_promotion", table_name="employee_promotion_detail", schema="hr")
    op.drop_table("employee_promotion_detail", schema="hr")
    op.drop_index("idx_promotion_employee", table_name="employee_promotion", schema="hr")
    op.drop_table("employee_promotion", schema="hr")

    op.drop_index("idx_separation_activity_separation", table_name="employee_separation_activity", schema="hr")
    op.drop_table("employee_separation_activity", schema="hr")
    op.drop_index("idx_separation_employee", table_name="employee_separation", schema="hr")
    op.drop_index("idx_separation_status", table_name="employee_separation", schema="hr")
    op.drop_table("employee_separation", schema="hr")

    op.drop_index("idx_onboarding_activity_onboarding", table_name="employee_onboarding_activity", schema="hr")
    op.drop_table("employee_onboarding_activity", schema="hr")
    op.drop_index("idx_onboarding_employee", table_name="employee_onboarding", schema="hr")
    op.drop_index("idx_onboarding_status", table_name="employee_onboarding", schema="hr")
    op.drop_table("employee_onboarding", schema="hr")

    op.execute("DROP TYPE IF EXISTS separation_status")
    op.execute("DROP TYPE IF EXISTS separation_type")
    op.execute("DROP TYPE IF EXISTS boarding_status")
