"""Add Dotmac Academy learning requirement and progress tables.

Revision ID: 20260815_academy_learning_sync
Revises: 20260722_info_change_batches
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.alembic_utils import ensure_enum

revision = "20260815_academy_learning_sync"
down_revision = "20260722_info_change_batches"
branch_labels = None
depends_on = None

SCHEMA = "training"
TABLES = ["academy_learning_requirement", "academy_learning_progress"]


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
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {SCHEMA}.{table}
            USING (should_bypass_rls() OR organization_id = get_current_organization_id())
            WITH CHECK (should_bypass_rls() OR organization_id = get_current_organization_id())
            """
        )


def disable_rls() -> None:
    for table in reversed(TABLES):
        op.execute(
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {SCHEMA}.{table}"
        )
        op.execute(f"ALTER TABLE {SCHEMA}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS training")
    bind = op.get_bind()
    progress_status = ensure_enum(
        bind,
        "academy_progress_status",
        "assigned",
        "started",
        "in_progress",
        "assessment_taken",
        "passed",
        "failed",
        "completed",
        "certificate_issued",
        schema=SCHEMA,
    )

    op.create_table(
        "academy_learning_requirement",
        uuid_pk(),
        org_id_col(),
        sa.Column("designation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("academy_course_id", sa.String(length=120), nullable=False),
        sa.Column("academy_course_title", sa.String(length=255), nullable=False),
        sa.Column("academy_assessment_id", sa.String(length=120), nullable=True),
        sa.Column("academy_assessment_title", sa.String(length=255), nullable=True),
        sa.Column(
            "is_required",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(
            ["designation_id"],
            ["hr.designation.designation_id"],
        ),
        sa.ForeignKeyConstraint(["created_by"], ["people.id"]),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_academy_req_org_designation",
        "academy_learning_requirement",
        ["organization_id", "designation_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_academy_req_course",
        "academy_learning_requirement",
        ["organization_id", "academy_course_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_academy_req_active",
        "academy_learning_requirement",
        ["organization_id", "is_active"],
        schema=SCHEMA,
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_academy_req_designation_course_assessment
        ON training.academy_learning_requirement (
            organization_id,
            designation_id,
            academy_course_id,
            coalesce(academy_assessment_id, '')
        )
        """
    )

    op.create_table(
        "academy_learning_progress",
        uuid_pk(),
        org_id_col(),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requirement_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("academy_course_id", sa.String(length=120), nullable=False),
        sa.Column("academy_course_title", sa.String(length=255), nullable=True),
        sa.Column("academy_assessment_id", sa.String(length=120), nullable=True),
        sa.Column("academy_assessment_title", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            progress_status,
            nullable=False,
            server_default="in_progress",
        ),
        sa.Column(
            "progress_percentage",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="0.00",
        ),
        sa.Column("score", sa.Numeric(8, 2), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("certificate_ref", sa.String(length=120), nullable=True),
        sa.Column("certification_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        org_fk(),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["training.academy_learning_requirement.id"],
        ),
        sa.ForeignKeyConstraint(
            ["certification_id"],
            ["hr.employee_certification.certification_id"],
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_academy_progress_employee",
        "academy_learning_progress",
        ["organization_id", "employee_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_academy_progress_course",
        "academy_learning_progress",
        ["organization_id", "academy_course_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_academy_progress_status",
        "academy_learning_progress",
        ["organization_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_academy_progress_requirement",
        "academy_learning_progress",
        ["organization_id", "requirement_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_academy_progress_synced",
        "academy_learning_progress",
        ["organization_id", "last_synced_at"],
        schema=SCHEMA,
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_academy_progress_employee_course_assessment
        ON training.academy_learning_progress (
            organization_id,
            employee_id,
            academy_course_id,
            coalesce(academy_assessment_id, '')
        )
        """
    )

    enable_rls()


def downgrade() -> None:
    disable_rls()
    op.drop_index(
        "uq_academy_progress_employee_course_assessment",
        table_name="academy_learning_progress",
        schema=SCHEMA,
    )
    op.drop_table("academy_learning_progress", schema=SCHEMA)
    op.drop_index(
        "uq_academy_req_designation_course_assessment",
        table_name="academy_learning_requirement",
        schema=SCHEMA,
    )
    op.drop_table("academy_learning_requirement", schema=SCHEMA)
    op.execute("DROP TYPE IF EXISTS training.academy_progress_status")
